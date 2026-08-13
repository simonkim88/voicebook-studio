# tts_worker.py - TTS 백그라운드 작업 모듈
import re
import time
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from srt_writer import SrtTimeline, write_srt

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

# flash-attn is CUDA-only and hard to build on Windows; it does not exist on
# macOS (MPS)/CPU at all. Choose the attention backend by device so that
# Windows-without-flash-attn, macOS (Apple Silicon), and CPU all fall back to
# PyTorch's built-in SDPA instead of erroring on flash_attention_2.
def _has_flash_attn():
    try:
        import flash_attn  # noqa: F401
        return True
    except ImportError:
        return False


def get_attn_implementation(device):
    """디바이스에 맞는 attention 구현 반환.
    flash_attention_2는 CUDA + flash-attn 설치 시에만 사용. 그 외(macOS MPS/CPU,
    flash-attn 미설치)는 sdpa로 폴백."""
    if device == "cuda" and _has_flash_attn():
        return "flash_attention_2"
    return "sdpa"


class TTSWorker(QThread):
    """백그라운드 TTS 처리 스레드 (ETA 계산 포함)"""
    progress = pyqtSignal(int)      # 0-100
    status = pyqtSignal(str)        # 상태 메시지
    eta = pyqtSignal(str)           # 예상 남은 시간
    finished_signal = pyqtSignal(str)  # 출력 파일 경로
    error = pyqtSignal(str)         # 오류 메시지
    stopped = pyqtSignal(str)       # 중지 시그널 (중간 저장 파일 경로)

    def __init__(self, text, output_path, voice="Sohee", instruct_text="", device="cpu",
                 is_custom_voice=False, ref_audio_path=None, ref_text=None, model_size="1.7B",
                 tts_engine="qwen", kokoro_lang_code="a", srt_mode="none"):
        super().__init__()
        self.text = text
        self.output_path = output_path
        self.voice = voice
        self.instruct_text = instruct_text
        self.device = device
        self.is_custom_voice = is_custom_voice
        self.ref_audio_path = ref_audio_path
        self.ref_text = ref_text
        self.model_size = model_size
        self.tts_engine = tts_engine          # "qwen" 또는 "kokoro"
        self.kokoro_lang_code = kokoro_lang_code
        self.kokoro_pipeline = None
        self.model = None
        self.chunk_times = []  # 각 청크 처리 시간 기록
        self._is_running = True  # 중지 플래그
        self.max_duration_sec = 600  # 파일 분할 단위 (10분)
        # False면 분할 없이 오디오 1개로 저장 (자막 매칭 정확도가 가장 높음).
        # 전체를 메모리에 쌓지 않고 디스크로 스트리밍 저장한다.
        self.split_audio = True

        # --- GPU 활용 튜닝 -------------------------------------------------
        # Qwen3-TTS의 자기회귀 디코딩은 연산량이 아니라 커널 런치/인코딩에
        # 묶여 있다 (batch=1이면 GPU가 대부분 유휴 상태로 논다). 청크 여러
        # 개를 한 번의 generate로 묶으면 그 오버헤드가 분산돼 스루풋이 오른다.
        # None이면 _resolve_batch_size()가 디바이스별 기본값을 고른다.
        self.batch_size = None
        # torch.compile 적용 여부. None이면 디바이스별 기본값.
        self.torch_compile = None
        # generate()에 덧씌울 파라미터 (do_sample, temperature, top_k 등).
        # 비워 두면 qwen_tts의 generate_config.json 기본값을 그대로 쓴다.
        self.generate_overrides = None

        # SRT 자막(오디오-텍스트 매칭) 출력
        # "none": 만들지 않음 / "per_file": 오디오 파일마다 1개 / "merged": 전체 1개
        self.srt_mode = srt_mode if srt_mode in ("none", "per_file", "merged") else "none"
        self.srt_files = []            # 생성된 .srt 경로 목록
        self._timeline = SrtTimeline() if self.srt_mode != "none" else None
        self._written_samples = 0      # 지금까지 파일로 저장한 전역 샘플 수
        self._last_engine_segments = None  # 엔진이 문장 단위로 준 구간 (Kokoro)
        self._stream_writer = None     # 분할 없이 저장할 때 쓰는 스트리밍 핸들

    def run(self):
        try:
            engine_name = "Kokoro-82M" if self.tts_engine == "kokoro" else "Qwen3-TTS"
            self.status.emit(f"{engine_name} 모델 로딩 중... (디바이스: {self.device})")
            start_time = time.time()

            if self.tts_engine == "qwen" and not QWEN_AVAILABLE:
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

            if self.tts_engine == "kokoro":
                self._load_kokoro()
            else:
                self._load_qwen(torch)

            self.progress.emit(10)  # 모델 로딩 완료

            # torch.compile로 추론 최적화 (첫 청크만 느리고 이후 빨라짐)
            if self.tts_engine != "kokoro":
                self._apply_torch_compile(torch)

            self.status.emit("음성 생성 중...")
            self._run_generation(torch, start_time)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            # 변환이 끝나면 반드시 모델과 디바이스 캐시를 놓아준다.
            # PyTorch의 MPS/CUDA 캐싱 할당자는 한 번 잡은 버퍼를 OS에 돌려주지
            # 않으므로, 이걸 빼먹으면 앱이 떠 있는 동안 수십 GB를 계속 쥐고
            # 있게 된다 (실제로 91분짜리 변환 뒤 40GB를 물고 있었다).
            self._release_model()

    def _load_qwen(self, torch):
        """Qwen3-TTS 모델 로드"""
        # 모델 크기에 따른 모델 ID 결정
        size = self.model_size  # "1.7B" 또는 "0.6B"
        base_model_id = f"Qwen/Qwen3-TTS-12Hz-{size}-Base"
        custom_model_id = f"Qwen/Qwen3-TTS-12Hz-{size}-CustomVoice"

        # bfloat16: float16보다 수치 범위가 넓어 확률 언더플로우 방지
        model_dtype = torch.bfloat16

        # 디바이스에 맞는 attention 백엔드 (CUDA+flash-attn만 flash_attention_2, 그 외 sdpa)
        attn_impl = get_attn_implementation(self.device)

        if self.is_custom_voice:
            # Voice Clone: Base 모델 사용
            self.status.emit(f"🔄 Qwen3-TTS {size} Base 모델 로딩 중... (보이스 클론 모드)")
            self.model = Qwen3TTSModel.from_pretrained(
                base_model_id,
                device_map=self.device,
                dtype=model_dtype,
                attn_implementation=attn_impl,
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
            self.status.emit(f"🔄 Qwen3-TTS {size} 모델 다운로드/로딩 중... (최초 1회, 수 분 소요될 수 있습니다)")
            self.model = Qwen3TTSModel.from_pretrained(
                custom_model_id,
                device_map=self.device,
                dtype=model_dtype,
                attn_implementation=attn_impl,
            )

    def _should_torch_compile(self):
        """torch.compile을 적용할지 결정.

        CUDA는 reduce-overhead(CUDA graphs) + flash-attn 조합으로 이득이
        분명해 기본 ON. MPS는 inductor가 Metal 셰이더를 만들어 주긴 하지만
        KV 캐시 길이가 매 스텝 바뀌어 재컴파일이 잦을 수 있으므로, 벤치마크로
        확인하기 전까지는 기본 OFF로 두고 배치 쪽 이득을 먼저 취한다.
        worker.torch_compile = True/False 로 언제든 강제할 수 있다."""
        if self.torch_compile is not None:
            return bool(self.torch_compile)
        return self.device == "cuda"

    def _apply_torch_compile(self, torch):
        """자기회귀 디코더를 torch.compile로 감싼다.

        이전 구현은 `self.model.llm`을 대상으로 삼았는데 qwen_tts에는 그런
        속성이 없어 조건이 항상 False였다 (= 어떤 플랫폼에서도 컴파일된 적이
        없음). 실제 핫패스는 Qwen3TTSModel → .model(HF 모델) → .talker 안쪽의
        디코더 스택이다. talker.forward 자체는 generation_step 같은 파이썬
        int를 인자로 받아 스텝마다 재컴파일을 유발하므로, 텐서만 받는 안쪽
        디코더(talker.model / talker.code_predictor.model)만 감싼다."""
        if not hasattr(torch, "compile") or not self._should_torch_compile():
            return

        # reduce-overhead는 CUDA graphs 기반이라 CUDA에서만 의미가 있다.
        mode = "reduce-overhead" if self.device == "cuda" else "default"

        talker = getattr(getattr(self.model, "model", None), "talker", None)
        if talker is None:
            print("[warn] talker를 찾지 못해 torch.compile을 건너뜁니다.")
            return

        targets = [talker]
        code_predictor = getattr(talker, "code_predictor", None)
        if code_predictor is not None:
            targets.append(code_predictor)

        compiled = []
        for owner in targets:
            module = getattr(owner, "model", None)
            if module is None:
                continue
            try:
                owner.model = torch.compile(module, mode=mode)
                compiled.append(f"{type(owner).__name__}.model")
            except Exception as e:
                print(f"[warn] torch.compile 실패, 기본 모드로 진행: {e}")

        if compiled:
            self.status.emit(f"모델 최적화 적용 (torch.compile, mode={mode})")
            print(f"[info] torch.compile 적용: {', '.join(compiled)} (mode={mode})")
        else:
            print("[warn] torch.compile 대상을 찾지 못했습니다.")

    def _load_kokoro(self):
        """Kokoro-82M 파이프라인 로드 (최초 1회 모델 자동 다운로드)"""
        from kokoro import KPipeline
        self.status.emit("🔄 Kokoro-82M 모델 다운로드/로딩 중... (최초 1회 ~330MB)")
        device = self.device if self.device in ("cuda", "cpu") else None
        self.kokoro_pipeline = KPipeline(
            lang_code=self.kokoro_lang_code,
            repo_id="hexgrad/Kokoro-82M",
            device=device,
        )

    def _synthesize_kokoro(self, chunk):
        """Kokoro로 한 청크를 합성 → ([audio_np], sample_rate) 반환

        Kokoro는 청크를 내부적으로 문장 단위로 쪼개 (텍스트, 오디오) 쌍을 yield
        하므로, SRT용으로 그 경계를 그대로 기록해 둔다 (시간 추정 불필요)."""
        parts = []
        segments = []
        for graphemes, _, audio in self.kokoro_pipeline(chunk, voice=self.voice, speed=1):
            if hasattr(audio, 'detach'):
                audio = audio.detach().cpu().numpy()
            elif hasattr(audio, 'numpy'):
                audio = audio.numpy()
            audio = np.asarray(audio, dtype=np.float32)
            parts.append(audio)
            segments.append((graphemes if isinstance(graphemes, str) else chunk, audio))
        if not parts:
            self._last_engine_segments = None
            return None, 24000
        self._last_engine_segments = segments
        return [np.concatenate(parts)], 24000

    def _track_cues(self, chunk, wav_data, sample_rate):
        """이번 청크의 오디오를 자막 타임라인에 반영"""
        if self._timeline is None:
            self._last_engine_segments = None  # 자막을 안 만들어도 참조는 즉시 해제
            return
        try:
            if self._last_engine_segments:
                self._timeline.add_engine_segments(self._last_engine_segments, sample_rate)
            else:
                self._timeline.add_chunk(chunk, wav_data, sample_rate)
        except Exception as e:
            print(f"[warn] 자막 타임라인 기록 실패: {e}")
        finally:
            self._last_engine_segments = None

    def _flush_audio(self, data, wav_path, sample_rate):
        """오디오 세그먼트를 저장 → MP3 변환 → (옵션) 같은 이름의 .srt 저장.

        오디오 파일 경로를 반환한다. Simon Reader는 파일명 stem으로 오디오와
        자막을 짝짓기 때문에 .srt는 반드시 최종 오디오 파일과 같은 이름이어야 한다."""
        import soundfile as sf
        sf.write(wav_path, data, sample_rate)
        audio_path = self._convert_wav_to_mp3(wav_path)

        if self.srt_mode == "per_file" and self._timeline is not None:
            start = self._written_samples
            end = start + len(data)
            cues = self._timeline.cues_for_range(start, end, sample_rate)
            srt_path = write_srt(audio_path, cues)
            if srt_path:
                self.srt_files.append(srt_path)

        self._written_samples += len(data)
        return audio_path

    def _stream_write(self, data, sample_rate):
        """분할 없이 저장하는 모드: 청크를 그때그때 WAV에 이어 쓴다.

        오디오 전체를 메모리에 들고 있으면 장편에서 수 GB가 되므로,
        soundfile 핸들을 열어 두고 디스크로 흘려보낸다."""
        import soundfile as sf
        if self._stream_writer is None:
            self._stream_writer = sf.SoundFile(
                self.output_path, mode='w', samplerate=int(sample_rate), channels=1,
            )
        self._stream_writer.write(data)
        self._written_samples += len(data)

    def _finish_stream(self, sample_rate):
        """스트리밍 저장 마무리: 파일 닫고 MP3 변환 + 통합 자막 저장."""
        if self._stream_writer is None:
            return None
        self._stream_writer.close()
        self._stream_writer = None

        audio_path = self._convert_wav_to_mp3(self.output_path)

        if self._timeline is not None:
            # 분할이 없으므로 per_file / merged 모두 결과가 같다 (파일 1개 = 자막 1개)
            cues = self._timeline.cues_for_range(0, self._written_samples, sample_rate)
            srt_path = write_srt(audio_path, cues)
            if srt_path:
                self.srt_files.append(srt_path)
        return audio_path

    def _write_merged_srt(self, sample_rate):
        """전체 오디오를 아우르는 .srt 1개를 저장 (merged 모드).

        시간은 첫 번째 오디오 파일의 0초부터 누적된 값이다. Simon Reader는
        .srt를 책으로 등록했을 때 형제 오디오 파일들의 길이를 누적해 각 파일이
        담당하는 구간을 잘라내므로(align_srt_book_native), 통합 타임라인이어야
        한다. 파일명은 출력 파일명 그대로이고 _01/_02 접미사가 붙지 않는다."""
        if self.srt_mode != "merged" or self._timeline is None:
            return None
        if not sample_rate or self._written_samples <= 0:
            return None

        cues = self._timeline.cues_for_range(0, self._written_samples, sample_rate)
        srt_path = write_srt(self.output_path, cues)
        if srt_path:
            self.srt_files.append(srt_path)
        return srt_path

    def _resolve_batch_size(self):
        """한 번의 generate에 묶을 청크 수."""
        if self.tts_engine == "kokoro":
            return 1  # KPipeline에는 배치 API가 없다
        if self.batch_size:
            return max(1, int(self.batch_size))
        # GPU에서는 배치를 키울수록 커널 런치/인코딩 비용이 분산된다.
        # CPU는 배치를 키워봐야 연산량만 비례해 늘어 이득이 없다.
        return 4 if self.device in ("mps", "cuda") else 1

    def _synthesize_qwen(self, texts):
        """청크 리스트를 한 번의 generate로 합성 → (wavs, sample_rate).

        qwen_tts의 generate_* API는 text를 list로 받으면 배치로 디코딩하고
        입력 순서 그대로 List[np.ndarray]를 돌려준다. language/speaker/instruct
        는 길이 1이면 배치 크기에 맞춰 자동으로 복제된다."""
        # 기본값은 do_sample=True(temperature 0.9)라 같은 문장도 매번 길이가
        # 달라진다. 벤치마크처럼 재현 가능한 실행이 필요하면
        # generate_overrides={"do_sample": False}로 고정할 수 있다.
        gen_kwargs = dict(self.generate_overrides or {})
        gen_kwargs.setdefault("max_new_tokens", 2048)

        if self.is_custom_voice:
            from document_parser import CUSTOM_VOICE_PRESETS
            voice_info = CUSTOM_VOICE_PRESETS.get(self.voice, {})
            language = voice_info.get("language", "Korean")
            return self.model.generate_voice_clone(
                text=texts,
                language=language,
                voice_clone_prompt=self.voice_clone_prompt,
                **gen_kwargs,
            )
        return self.model.generate_custom_voice(
            text=texts,
            language="Korean" if self.voice == "Sohee" else "Auto",
            speaker=self.voice,
            instruct=self.instruct_text if self.instruct_text else None,
            **gen_kwargs,
        )

    @staticmethod
    def _is_device_error(err):
        """재시도해 볼 만한 디바이스/메모리 오류인지 판정 (CUDA·MPS 공통)."""
        msg = str(err).lower()
        return any(k in msg for k in ("cuda", "mps", "metal", "out of memory"))

    def _release_model(self):
        """모델 참조를 끊고 디바이스 캐시를 비운다 (변환 종료 시 항상 호출).

        워커는 변환마다 새로 만들어지지만 앱이 이전 워커를 self.tts_worker로
        붙들고 있어, 여기서 놓아주지 않으면 모델과 MPS 버퍼 풀이 다음 변환
        때까지 그대로 남는다."""
        try:
            import torch
        except ImportError:
            return
        self.model = None
        self.kokoro_pipeline = None
        self.voice_clone_prompt = None
        try:
            self._free_device_memory(torch)
        except Exception as e:
            print(f"[warn] 디바이스 메모리 정리 실패: {e}")

    def _free_device_memory(self, torch):
        """디바이스 캐시 정리. CUDA와 MPS는 API가 서로 다르다."""
        import gc
        if self.device == "cuda" and torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
        elif self.device == "mps" and torch.backends.mps.is_available():
            torch.mps.synchronize()
            torch.mps.empty_cache()
        gc.collect()

    def _generate_with_retry(self, torch, texts, max_retries=3):
        """한 배치를 합성. 디바이스 오류면 캐시를 비우고 재시도한다."""
        for attempt in range(max_retries):
            try:
                with torch.no_grad():
                    if self.tts_engine == "kokoro":
                        return self._synthesize_kokoro(texts[0])
                    return self._synthesize_qwen(texts)
            except RuntimeError as e:
                if not self._is_device_error(e) or attempt == max_retries - 1:
                    raise
                self.status.emit(f"⚠️ 디바이스 오류 발생, 재시도 중... ({attempt+2}/{max_retries})")
                self._free_device_memory(torch)
                time.sleep(1)
        return None, None

    def _iter_synthesized(self, torch, chunks):
        """청크를 배치로 묶어 합성하되, 결과는 입력 순서대로 하나씩 내보낸다.

        자막 타임라인(_track_cues)과 파일 분할이 '지금까지 쓴 전역 샘플 수'에
        의존하므로 생성만 배치로 묶고 소비는 순차적으로 유지한다.

        yield: (청크 인덱스, 청크 텍스트, 오디오, 샘플레이트, 청크당 생성 시간)"""
        batch_size = self._resolve_batch_size()
        start = 0
        while start < len(chunks):
            if not self._is_running:
                return
            group = chunks[start:start + batch_size]

            batch_start = time.time()
            try:
                # 메모리 부족으로 실패한 배치는 그대로 재시도해 봐야 또 실패한다.
                # 쪼개는 편이 빠르므로 배치일 때는 한 번만 시도한다.
                wavs, sr = self._generate_with_retry(
                    torch, group, max_retries=1 if len(group) > 1 else 3
                )
            except RuntimeError:
                if len(group) == 1:
                    raise
                # 배치가 커서 실패했을 수 있다 → 비우고 1개씩 다시 시도
                self.status.emit(f"⚠️ 배치 {len(group)}개 실패 → 1개씩 재시도")
                self._free_device_memory(torch)
                wavs, sr = [], None
                for text in group:
                    single, single_sr = self._generate_with_retry(torch, [text])
                    wavs.extend(single or [])
                    sr = single_sr or sr
                # 남은 배치도 같은 이유로 실패할 테니 아예 크기를 줄인다
                batch_size = max(1, len(group) // 2)
                self.status.emit(f"⚠️ 이후 배치 크기를 {batch_size}(으)로 줄입니다")
            elapsed = time.time() - batch_start

            base = start          # yield 전에 다음 배치 위치로 옮기므로 따로 잡아 둔다
            start += len(group)
            if not wavs:
                continue  # 배치 전체 실패 → 다음 배치로
            per_chunk = elapsed / len(wavs)
            for offset, wav in enumerate(wavs):
                yield base + offset, group[offset], wav, sr, per_chunk

    def _run_generation(self, torch, start_time):
        """청크 단위로 음성 생성 후 파일로 저장 (Qwen/Kokoro 공통)"""
        try:
            # 빈 청크는 미리 걸러낸다 (배치 구성과 진행률 계산이 정확해진다)
            chunks = [c for c in self._chunk_text(self.text) if c.strip()]
            total_chunks = len(chunks)

            import soundfile as sf
            import os

            # 10분 단위 즉시 저장 설정
            max_duration_sec = self.max_duration_sec
            base_path = self.output_path.replace('.wav', '')
            sample_rate = None
            segment_audio = []       # 현재 세그먼트의 오디오 버퍼
            segment_samples = 0      # 현재 세그먼트의 샘플 수
            file_idx = 0             # 저장된 파일 번호
            saved_files = []

            # 생성은 배치로 묶고(GPU 활용률↑), 소비는 원래 순서대로 한 개씩.
            for i, chunk, wav_data, sr, gen_time in self._iter_synthesized(torch, chunks):
                # 중지 확인
                if not self._is_running:
                    self.status.emit("⏹️ 사용자에 의해 중지됨. 현재까지 진행된 내용 저장 중...")
                    break

                chunk_start = time.time()

                # 진행률 계산 (10% ~ 95% 범위에서 청크 진행률 반영)
                chunk_progress = ((i + 1) / total_chunks) * 85
                progress = int(10 + chunk_progress)
                self.progress.emit(progress)
                self.status.emit(f"처리 중... {i+1}/{total_chunks} 청크 ({progress}%) | 저장된 파일: {file_idx}개")

                # 오디오 데이터를 CPU numpy로 즉시 이동 (GPU 메모리 해제)
                if hasattr(wav_data, 'cpu'):
                    wav_data = wav_data.cpu().numpy()
                elif hasattr(wav_data, 'numpy'):
                    wav_data = wav_data.numpy()

                # 볼륨 정규화 (peak normalization → -1dB target)
                peak = np.max(np.abs(wav_data))
                if peak > 0:
                    target_peak = 0.89  # -1dB
                    wav_data = wav_data * (target_peak / peak)

                if sample_rate is None:
                    sample_rate = sr
                max_samples = int(max_duration_sec * sample_rate)

                # 자막 타임라인 기록 (저장 직전 = 전역 샘플 순서 그대로)
                self._track_cues(chunk, wav_data, sample_rate)

                if not self.split_audio:
                    # 분할 없음 → 바로 디스크로 흘려보낸다 (메모리에 쌓지 않음)
                    self._stream_write(wav_data, sample_rate)
                    del wav_data

                    # 생성 시간(배치에서 청크당으로 환산) + 이 청크의 저장 시간
                    chunk_time = gen_time + (time.time() - chunk_start)
                    self.chunk_times.append(chunk_time)
                    if i > 0:
                        avg = sum(self.chunk_times) / len(self.chunk_times)
                        remaining = avg * (total_chunks - (i + 1))
                        self.eta.emit(f"남은 시간: {self._format_time(remaining)}")
                    continue

                segment_audio.append(wav_data)
                segment_samples += len(wav_data)
                del wav_data

                # 10분 분량이 쌓이면 즉시 파일로 저장 → MP3 변환
                if segment_samples >= max_samples:
                    combined = np.concatenate(segment_audio)
                    file_idx += 1
                    segment_path = f"{base_path}_{file_idx:02d}.wav"
                    mp3_path = self._flush_audio(combined[:max_samples], segment_path, sample_rate)
                    saved_files.append(mp3_path)
                    self.status.emit(f"💾 파일 {file_idx} 저장 완료! 계속 처리 중... {i+1}/{total_chunks}")

                    # 남은 오디오를 다음 세그먼트로 이월
                    leftover = combined[max_samples:]
                    segment_audio = [leftover] if len(leftover) > 0 else []
                    segment_samples = len(leftover)
                    del combined

                # 청크 처리 시간 기록 (생성 시간 + 저장/변환 시간)
                chunk_time = gen_time + (time.time() - chunk_start)
                self.chunk_times.append(chunk_time)

                # ETA 계산 (이동 평균 사용)
                if i > 0:
                    avg_time_per_chunk = sum(self.chunk_times) / len(self.chunk_times)
                    remaining_chunks = total_chunks - (i + 1)
                    remaining_seconds = avg_time_per_chunk * remaining_chunks
                    self.eta.emit(f"남은 시간: {self._format_time(remaining_seconds)} | 저장된 파일: {file_idx}개")

            # 분할 없음 모드 마무리 (오디오 1개 + 자막 1개)
            if not self.split_audio:
                was_stopped = not self._is_running
                audio_path = self._finish_stream(sample_rate)
                if audio_path is None:
                    self.error.emit("변환할 텍스트가 없습니다.")
                    return
                total_time = time.time() - start_time
                if was_stopped:
                    self.eta.emit(f"총 소요 시간: {self._format_time(total_time)}")
                    self.stopped.emit(audio_path)
                else:
                    self.eta.emit(f"총 소요 시간: {self._format_time(total_time)}")
                    self.progress.emit(100)
                    self.finished_signal.emit(audio_path)
                return

            # 남은 오디오 저장
            if segment_audio:
                combined = np.concatenate(segment_audio)
                was_stopped = not self._is_running

                if was_stopped and file_idx == 0:
                    # 중지 + 파일 없음 → partial로 저장
                    partial_path = f"{base_path}_partial.wav"
                    mp3_path = self._flush_audio(combined, partial_path, sample_rate)
                    self._write_merged_srt(sample_rate)
                    self.stopped.emit(mp3_path)
                    return
                elif was_stopped:
                    # 중지 + 이미 저장된 파일 있음 → 나머지를 partial로 저장
                    file_idx += 1
                    partial_path = f"{base_path}_{file_idx:02d}_partial.wav"
                    mp3_path = self._flush_audio(combined, partial_path, sample_rate)
                    saved_files.append(mp3_path)
                    self._write_merged_srt(sample_rate)
                    self.stopped.emit(saved_files[0])
                    return
                else:
                    # 정상 완료 → 마지막 세그먼트 저장
                    if file_idx == 0:
                        # 전체가 10분 이하 → 단일 파일
                        total_time = time.time() - start_time
                        self.eta.emit(f"총 소요 시간: {self._format_time(total_time)}")
                        self.progress.emit(100)
                        mp3_path = self._flush_audio(combined, self.output_path, sample_rate)
                        self._write_merged_srt(sample_rate)
                        self.finished_signal.emit(mp3_path)
                        return
                    else:
                        file_idx += 1
                        segment_path = f"{base_path}_{file_idx:02d}.wav"
                        mp3_path = self._flush_audio(combined, segment_path, sample_rate)
                        saved_files.append(mp3_path)
                del combined

            self._write_merged_srt(sample_rate)

            if saved_files:
                total_time = time.time() - start_time
                self.eta.emit(f"총 소요 시간: {self._format_time(total_time)} | {len(saved_files)}개 파일 생성됨")
                self.progress.emit(100)
                self.finished_signal.emit(saved_files[0])
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

    def _chunk_text(self, text, max_chars=500):
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
        # 이미 MP3인 경우 변환 건너뜀
        if not wav_path.lower().endswith('.wav'):
            return wav_path
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
