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

    def __init__(self, text, output_path, voice="Sohee", tone="natural", device="cpu"):
        super().__init__()
        self.text = text
        self.output_path = output_path
        self.voice = voice
        self.tone = tone
        self.device = device
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

            # 모델 로드 (float32 사용으로 수치적 안정성 확보)
            import torch
            self.status.emit(f"🔄 Qwen3-TTS 모델 다운로드/로딩 중... (최초 1회, 수 분 소요될 수 있습니다)")
            self.progress.emit(5)  # 5%로 설정 - 로딩 시작 표시
            
            self.model = Qwen3TTSModel.from_pretrained(
                "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
                device_map=self.device,
                dtype=torch.float32,  # float16 대신 float32 사용 (inf/nan 오류 방지)
            )
            
            self.progress.emit(10)  # 모델 로딩 완료

            self.status.emit("음성 생성 중...")
            tone_instruction = self._get_tone_instruction(self.tone)
            chunks = self._chunk_text(self.text)
            total_chunks = len(chunks)

            all_audio = []
            sample_rate = None

            for i, chunk in enumerate(chunks):
                # 중지 확인
                if not self._is_running:
                    self.status.emit("⏹️ 사용자에 의해 중지됨. 현재까지 진행된 내용 저장 중...")
                    break

                chunk_start = time.time()

                if not chunk.strip():
                    continue

                # 진행률 계산 (10% ~ 90% 범위에서 청크 진행률 반영)
                chunk_progress = ((i + 1) / total_chunks) * 80  # 80% 범위 사용
                progress = int(10 + chunk_progress)  # 10%에서 시작
                self.progress.emit(progress)
                self.status.emit(f"처리 중... {i+1}/{total_chunks} 청크 ({progress}%)")

                # TTS 생성
                import torch
                wavs, sr = self.model.generate_custom_voice(
                    text=chunk,
                    language="Korean" if self.voice == "Sohee" else "Auto",
                    speaker=self.voice,
                    instruct=tone_instruction if tone_instruction else None
                )

                all_audio.append(wavs[0])
                sample_rate = sr

                # 청크 처리 시간 기록
                chunk_time = time.time() - chunk_start
                self.chunk_times.append(chunk_time)

                # ETA 계산 (이동 평균 사용)
                if i > 0:
                    avg_time_per_chunk = sum(self.chunk_times) / len(self.chunk_times)
                    remaining_chunks = total_chunks - (i + 1)
                    remaining_seconds = avg_time_per_chunk * remaining_chunks
                    self.eta.emit(f"남은 시간: {self._format_time(remaining_seconds)}")

            # 오디오 합치기 (중지되었어도 현재까지 생성된 내용 저장)
            if all_audio:
                combined_audio = np.concatenate(all_audio)
                total_samples = len(combined_audio)
                total_duration_sec = total_samples / sample_rate
                
                # 중지되었는지 확인
                was_stopped = not self._is_running
                if was_stopped:
                    # 중지된 경우 파일명에 _partial 추가
                    base_path = self.output_path.replace('.wav', '')
                    partial_path = f"{base_path}_partial.wav"
                    import soundfile as sf
                    sf.write(partial_path, combined_audio, sample_rate)
                    self.stopped.emit(partial_path)
                    return
                
                # 파일 저장 (10분 단위 분할)
                max_duration_sec = 600  # 10분 = 600초
                max_samples = int(max_duration_sec * sample_rate)
                
                import soundfile as sf
                import os
                
                # 기본 파일명에서 확장자 제거
                base_path = self.output_path.replace('.wav', '')
                
                if total_duration_sec <= max_duration_sec:
                    # 10분 이하면 단일 파일로 저장
                    self.status.emit("오디오 파일 저장 중...")
                    sf.write(self.output_path, combined_audio, sample_rate)
                    total_time = time.time() - start_time
                    self.eta.emit(f"총 소요 시간: {self._format_time(total_time)}")
                    self.progress.emit(100)
                    self.finished_signal.emit(self.output_path)
                else:
                    # 10분 초과 시 분할 저장 (1초 오버랩 적용)
                    overlap_sec = 1.0  # 1초 오버랩
                    overlap_samples = int(overlap_sec * sample_rate)
                    
                    # 실제 분할 간격 (10분 - 1초 = 9분 59초)
                    split_interval = max_samples - overlap_samples
                    
                    num_files = (total_samples - overlap_samples + split_interval - 1) // split_interval
                    self.status.emit(f"오디오 파일 분할 저장 중... (총 {num_files}개 파일, 1초 오버랩)")
                    
                    saved_files = []
                    for file_idx in range(num_files):
                        # 오버랩 고려한 시작/종료 위치 계산
                        if file_idx == 0:
                            # 첫 파일: 0부터 시작
                            start_sample = 0
                            end_sample = min(max_samples, total_samples)
                        else:
                            # 이후 파일: 이전 파일의 (끝 - 1초)부터 시작
                            start_sample = file_idx * split_interval
                            end_sample = min(start_sample + max_samples, total_samples)
                        
                        segment = combined_audio[start_sample:end_sample]
                        
                        # 파일명 생성: 파일명_01.wav, 파일명_02.wav, ...
                        segment_path = f"{base_path}_{file_idx+1:02d}.wav"
                        sf.write(segment_path, segment, sample_rate)
                        saved_files.append(segment_path)
                        
                        progress = int(90 + ((file_idx + 1) / num_files) * 10)
                        self.progress.emit(progress)
                        self.status.emit(f"파일 저장 중... ({file_idx+1}/{num_files})")
                    
                    total_time = time.time() - start_time
                    self.eta.emit(f"총 소요 시간: {self._format_time(total_time)} | {num_files}개 파일 생성됨")
                    self.progress.emit(100)
                    # 첫 번째 파일 경로를 반환 (또는 파일 목록을 쉼표로 구분하여 반환)
                    self.finished_signal.emit(saved_files[0] if saved_files else self.output_path)
            else:
                self.error.emit("변환할 텍스트가 없습니다.")

        except Exception as e:
            self.error.emit(str(e))

    def _get_tone_instruction(self, tone):
        """톤 설정을 Qwen3-TTS instruction으로 변환"""
        tone_map = {
            "natural": "자연스럽고 편안한 톤으로",
            "calm": "차분하고 안정된 톤으로",
            "happy": "밝고 긍정적인 톤으로",
            "serious": "진지하고 무게 있는 톤으로",
            "emotional": "감정이 풍부하고 표현력 있는 톤으로"
        }
        return tone_map.get(tone, "자연스럽고 편안한 톤으로")

    def _normalize_text(self, text):
        """텍스트 정규화 - 특수 문자 및 비정상적인 유니코드 제거"""
        import unicodedata

        # NFKC 정규화 (호환성 문자 표준화)
        text = unicodedata.normalize('NFKC', text)

        # 제어 문자 제거 (null, bell 등)
        text = ''.join(char for char in text if unicodedata.category(char)[0] != 'C' or char in '\n\t ')

        # 연속된 공백 정리
        text = re.sub(r'\s+', ' ', text)

        # 비정상적인 문장 부호 정리
        text = re.sub(r'[""]', '"', text)
        text = re.sub(r"[''']", "'", text)
        text = re.sub(r'[…]', '...', text)
        text = re.sub(r'[—–]', '-', text)

        return text.strip()

    def _chunk_text(self, text, max_chars=300):
        """텍스트를 청크로 분할 (문맥 보존)"""
        # 텍스트 정규화 적용
        text = self._normalize_text(text)

        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) < max_chars:
                current_chunk += para + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para + "\n\n"

        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks if chunks else [text]

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
