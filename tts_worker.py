# tts_worker.py - TTS 백그라운드 작업 모듈
import re
import time
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

try:
    from qwen_tts import Qwen3TTSModel
    QWEN_AVAILABLE = True
except ImportError:
    QWEN_AVAILABLE = False

try:
    import soundfile as sf
    SOUNDFILE_AVAILABLE = True
except ImportError:
    SOUNDFILE_AVAILABLE = False


class TTSWorker(QThread):
    """백그라운드 TTS 처리 스레드 (ETA 계산 포함)"""
    progress = pyqtSignal(int)      # 0-100
    status = pyqtSignal(str)        # 상태 메시지
    eta = pyqtSignal(str)           # 예상 남은 시간
    finished_signal = pyqtSignal(str)  # 출력 파일 경로
    error = pyqtSignal(str)         # 오류 메시지
    stopped = pyqtSignal(str)       # 중지 시그널 (중간 저장 파일 경로)

    def __init__(self, text, output_path, voice="Sohee", instruct_text="", device="cpu",
                 is_custom_voice=False, ref_audio_path=None, ref_text=None):
        super().__init__()
        self.text = text
        self.output_path = output_path
        self.voice = voice
        self.instruct_text = instruct_text
        self.device = device
        self.is_custom_voice = is_custom_voice
        self.ref_audio_path = ref_audio_path
        self.ref_text = ref_text
        self.model = None
        self.chunk_times = []  # 각 청크 처리 시간 기록
        self._is_running = True  # 중지 플래그

    def run(self):
        try:
            self.status.emit(f"Qwen3-TTS 모델 로딩 중... (디바이스: {self.device})")
            start_time = time.time()

            if not QWEN_AVAILABLE:
                # Mock mode
                for i in range(100):
                    time.sleep(0.05)
                    self.progress.emit(i + 1)
                    # ETA 계산
                    elapsed = time.time() - start_time
                    remaining = (elapsed / (i + 1)) * (100 - (i + 1))
                    self.eta.emit(f"남은 시간: {self._format_time(remaining)}")

                sample_rate = 24000
                duration = 3
                t = np.linspace(0, duration, int(sample_rate * duration))
                audio = np.sin(2 * np.pi * 440 * t) * 0.3

                import soundfile as sf
                sf.write(self.output_path, audio, sample_rate)
                self.finished_signal.emit(self.output_path)
                return

            # 모델 로드
            import torch
            self.progress.emit(5)

            if self.is_custom_voice:
                # Voice Clone: Base 모델 사용
                self.status.emit(f"🔄 Qwen3-TTS Base 모델 로딩 중... (보이스 클론 모드)")
                self.model = Qwen3TTSModel.from_pretrained(
                    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                    device_map=self.device,
                    dtype=torch.float16,
                    attn_implementation="sdpa",
                )
                self.progress.emit(8)

                # 참조 오디오로 voice clone prompt 사전 생성 (재사용)
                self.status.emit("🎤 참조 음성 분석 중...")
                self.voice_clone_prompt = self.model.create_voice_clone_prompt(
                    ref_audio=self.ref_audio_path,
                    ref_text=self.ref_text,
                    x_vector_only_mode=False,  # ICL 모드 (더 높은 품질)
                )
            else:
                # Built-in: CustomVoice 모델 사용
                self.status.emit(f"🔄 Qwen3-TTS 모델 다운로드/로딩 중... (최초 1회, 수 분 소요될 수 있습니다)")
                self.model = Qwen3TTSModel.from_pretrained(
                    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
                    device_map=self.device,
                    dtype=torch.float16,
                    attn_implementation="sdpa",
                )

            self.progress.emit(10)  # 모델 로딩 완료

            self.status.emit("음성 생성 중...")
            chunks = self._chunk_text(self.text)
            total_chunks = len(chunks)

            import soundfile as sf
            import os

            # 10분 단위 즉시 저장 설정
            max_duration_sec = 600  # 10분
            base_path = self.output_path.replace('.wav', '')
            sample_rate = None
            segment_audio = []       # 현재 세그먼트의 오디오 버퍼
            segment_samples = 0      # 현재 세그먼트의 샘플 수
            file_idx = 0             # 저장된 파일 번호
            saved_files = []

            for i, chunk in enumerate(chunks):
                # 중지 확인
                if not self._is_running:
                    self.status.emit("⏹️ 사용자에 의해 중지됨. 현재까지 진행된 내용 저장 중...")
                    break

                chunk_start = time.time()

                if not chunk.strip():
                    continue

                # 진행률 계산 (10% ~ 95% 범위에서 청크 진행률 반영)
                chunk_progress = ((i + 1) / total_chunks) * 85
                progress = int(10 + chunk_progress)
                self.progress.emit(progress)
                self.status.emit(f"처리 중... {i+1}/{total_chunks} 청크 ({progress}%) | 저장된 파일: {file_idx}개")

                # TTS 생성
                import torch
                with torch.no_grad():
                    if self.is_custom_voice:
                        # Voice Clone: Base 모델 + ICL 프롬프트
                        from document_parser import CUSTOM_VOICE_PRESETS
                        voice_info = CUSTOM_VOICE_PRESETS.get(self.voice, {})
                        language = voice_info.get("language", "Korean")
                        wavs, sr = self.model.generate_voice_clone(
                            text=chunk,
                            language=language,
                            voice_clone_prompt=self.voice_clone_prompt,
                            max_new_tokens=2048,
                        )
                    else:
                        # Built-in: CustomVoice 모델
                        wavs, sr = self.model.generate_custom_voice(
                            text=chunk,
                            language="Korean" if self.voice == "Sohee" else "Auto",
                            speaker=self.voice,
                            instruct=self.instruct_text if self.instruct_text else None,
                            max_new_tokens=2048,
                        )

                # 오디오 데이터를 CPU numpy로 즉시 이동 (GPU 메모리 해제)
                wav_data = wavs[0]
                if hasattr(wav_data, 'cpu'):
                    wav_data = wav_data.cpu().numpy()
                elif hasattr(wav_data, 'numpy'):
                    wav_data = wav_data.numpy()

                del wavs
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                # 볼륨 정규화 (peak normalization → -1dB target)
                peak = np.max(np.abs(wav_data))
                if peak > 0:
                    target_peak = 0.89  # -1dB
                    wav_data = wav_data * (target_peak / peak)

                if sample_rate is None:
                    sample_rate = sr
                max_samples = int(max_duration_sec * sample_rate)

                segment_audio.append(wav_data)
                segment_samples += len(wav_data)
                del wav_data

                # 10분 분량이 쌓이면 즉시 파일로 저장 → MP3 변환
                if segment_samples >= max_samples:
                    combined = np.concatenate(segment_audio)
                    file_idx += 1
                    segment_path = f"{base_path}_{file_idx:02d}.wav"
                    sf.write(segment_path, combined[:max_samples], sample_rate)
                    mp3_path = self._convert_wav_to_mp3(segment_path)
                    saved_files.append(mp3_path)
                    self.status.emit(f"💾 파일 {file_idx} 저장 완료! 계속 처리 중... {i+1}/{total_chunks}")

                    # 남은 오디오를 다음 세그먼트로 이월
                    leftover = combined[max_samples:]
                    segment_audio = [leftover] if len(leftover) > 0 else []
                    segment_samples = len(leftover)
                    del combined

                # 청크 처리 시간 기록
                chunk_time = time.time() - chunk_start
                self.chunk_times.append(chunk_time)

                # ETA 계산 (이동 평균 사용)
                if i > 0:
                    avg_time_per_chunk = sum(self.chunk_times) / len(self.chunk_times)
                    remaining_chunks = total_chunks - (i + 1)
                    remaining_seconds = avg_time_per_chunk * remaining_chunks
                    self.eta.emit(f"남은 시간: {self._format_time(remaining_seconds)} | 저장된 파일: {file_idx}개")

            # 남은 오디오 저장
            if segment_audio:
                combined = np.concatenate(segment_audio)
                was_stopped = not self._is_running

                if was_stopped and file_idx == 0:
                    # 중지 + 파일 없음 → partial로 저장
                    partial_path = f"{base_path}_partial.wav"
                    sf.write(partial_path, combined, sample_rate)
                    mp3_path = self._convert_wav_to_mp3(partial_path)
                    self.stopped.emit(mp3_path)
                    return
                elif was_stopped:
                    # 중지 + 이미 저장된 파일 있음 → 나머지를 partial로 저장
                    file_idx += 1
                    partial_path = f"{base_path}_{file_idx:02d}_partial.wav"
                    sf.write(partial_path, combined, sample_rate)
                    saved_files.append(partial_path)
                    mp3_files = self._convert_all_to_mp3(saved_files)
                    self.stopped.emit(mp3_files[0])
                    return
                else:
                    # 정상 완료 → 마지막 세그먼트 저장
                    if file_idx == 0:
                        # 전체가 10분 이하 → 단일 파일
                        sf.write(self.output_path, combined, sample_rate)
                        total_time = time.time() - start_time
                        self.eta.emit(f"총 소요 시간: {self._format_time(total_time)}")
                        self.progress.emit(100)
                        mp3_path = self._convert_wav_to_mp3(self.output_path)
                        self.finished_signal.emit(mp3_path)
                        return
                    else:
                        file_idx += 1
                        segment_path = f"{base_path}_{file_idx:02d}.wav"
                        sf.write(segment_path, combined, sample_rate)
                        saved_files.append(segment_path)
                del combined

            if saved_files:
                total_time = time.time() - start_time
                mp3_files = self._convert_all_to_mp3(saved_files)
                self.eta.emit(f"총 소요 시간: {self._format_time(total_time)} | {len(mp3_files)}개 파일 생성됨")
                self.progress.emit(100)
                self.finished_signal.emit(mp3_files[0])
            else:
                self.error.emit("변환할 텍스트가 없습니다.")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))

    def _normalize_text(self, text):
        """텍스트 정규화 - 특수 문자 및 비정상적인 유니코드 제거"""
        import unicodedata

        # NFKC 정규화 (호환성 문자 표준화)
        text = unicodedata.normalize('NFKC', text)

        # 제어 문자 제거 (null, bell 등) - 개행은 보존
        text = ''.join(char for char in text if unicodedata.category(char)[0] != 'C' or char in '\n\t ')

        # 단락 구분 보존: \n\n을 임시 마커로 변환
        text = re.sub(r'\n\s*\n', '\n\n', text)  # 다양한 빈 줄 패턴 통일

        # 단락 내부의 연속 공백/탭만 정리 (개행은 보존)
        text = re.sub(r'[^\S\n]+', ' ', text)

        # 비정상적인 문장 부호 정리
        text = re.sub(r'[""]', '"', text)
        text = re.sub(r"[''']", "'", text)
        text = re.sub(r'[…]', '...', text)
        text = re.sub(r'[—–]', '-', text)

        return text.strip()

    def _split_long_paragraph(self, para, max_chars):
        """긴 단락을 문장 단위로 분할"""
        # 문장 경계로 분할 (한국어/영어/일본어/중국어 문장부호)
        sentences = re.split(r'(?<=[.!?。！？\n])\s*', para)
        chunks = []
        current = ""
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 <= max_chars:
                current = (current + " " + sent).strip() if current else sent
            else:
                if current:
                    chunks.append(current)
                # 문장 하나가 max_chars보다 길면 강제 분할
                if len(sent) > max_chars:
                    for i in range(0, len(sent), max_chars):
                        chunks.append(sent[i:i + max_chars])
                else:
                    current = sent
                    continue
                current = ""
        if current:
            chunks.append(current)
        return chunks

    def _chunk_text(self, text, max_chars=200):
        """텍스트를 청크로 분할 (문맥 보존, 문장 단위)"""
        # 텍스트 정규화 적용
        text = self._normalize_text(text)

        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 단락이 max_chars보다 길면 문장 단위로 분할
            if len(para) > max_chars:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                chunks.extend(self._split_long_paragraph(para, max_chars))
                continue

            if len(current_chunk) + len(para) + 2 <= max_chars:
                current_chunk = (current_chunk + "\n\n" + para).strip() if current_chunk else para
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para

        if current_chunk:
            chunks.append(current_chunk.strip())

        # 빈 청크 제거
        chunks = [c for c in chunks if c.strip()]
        return chunks if chunks else [text[:max_chars]]

    def _convert_wav_to_mp3(self, wav_path):
        """WAV → MP3 변환 후 WAV 삭제. 성공 시 mp3 경로 반환."""
        import os
        try:
            import av as _av
        except ImportError:
            print("[warn] PyAV 미설치 - MP3 변환 건너뜀 (wav 유지)")
            return wav_path
        mp3_path = wav_path.replace('.wav', '.mp3')
        try:
            in_c = _av.open(wav_path)
            in_s = in_c.streams.audio[0]
            out_c = _av.open(mp3_path, 'w')
            out_s = out_c.add_stream('mp3', rate=in_s.rate)
            out_s.bit_rate = 192000
            for packet in in_c.demux(in_s):
                for frame in packet.decode():
                    for out_packet in out_s.encode(frame):
                        out_c.mux(out_packet)
            for out_packet in out_s.encode(None):
                out_c.mux(out_packet)
            out_c.close()
            in_c.close()
            os.remove(wav_path)
            return mp3_path
        except Exception as e:
            print(f"[warn] MP3 변환 실패 ({wav_path}): {e}")
            return wav_path  # 실패 시 wav 유지

    def _convert_all_to_mp3(self, wav_paths):
        """여러 WAV 파일을 MP3로 변환"""
        mp3_paths = []
        for i, wav_path in enumerate(wav_paths):
            self.status.emit(f"MP3 변환 중... {i+1}/{len(wav_paths)}")
            mp3_paths.append(self._convert_wav_to_mp3(wav_path))
        return mp3_paths

    def stop(self):
        """음성 변환 중지 요청"""
        self._is_running = False
        self.status.emit("⏹️ 중지 요청됨... 현재 청크 완료 후 저장합니다")

    def _format_time(self, seconds):
        """초를 읽기 쉬운 형식으로 변환"""
        if seconds < 60:
            return f"{int(seconds)}초"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            secs = int(seconds % 60)
            return f"{minutes}분 {secs}초"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}시간 {minutes}분"
