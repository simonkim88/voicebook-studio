# config_manager.py - 설정 관리 모듈
import os
import json
import platform

CONFIG_FILE = os.path.expanduser("~/Documents/Qwen3-TTSApp/config.json")
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Documents/Qwen3-TTSApp/audiofiles")

def get_default_device():
    """기본 디바이스 자동 감지"""
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"  # Mac Apple Silicon
        elif torch.cuda.is_available():
            return "cuda"  # NVIDIA GPU
        else:
            return "cpu"
    except:
        return "cpu"

MODEL_SIZE_OPTIONS = [
    ("1.7B", "1.7B (고품질, VRAM 많이 사용)"),
    ("0.6B", "0.6B (경량, VRAM 적게 사용)"),
]

# TTS 엔진 선택 (Qwen3-TTS / Kokoro-82M)
TTS_ENGINE_OPTIONS = [
    ("qwen", "Qwen3-TTS (다국어·한국어 지원, 스타일 프롬프트 지원)"),
    ("kokoro", "Kokoro-82M (영어 전용, 경량·고속, 스타일 프롬프트 미지원)"),
]

# 출력 형식
#
# 자막은 항상 .srt 1개로 만든다. Simon Reader에 .srt를 책으로 등록하면
# (align_srt_book_native) 형제 오디오 파일들의 길이를 누적해 각 파일이 담당하는
# 구간을 잘라내므로, 자막은 첫 파일 0초부터 이어지는 하나의 타임라인이어야 한다.
#
# 오디오까지 1개로 만들면 Simon Reader가 길이를 누적할 필요 없이 절대 시간을
# 그대로 쓰므로 매칭이 가장 정확하다 (파일 경계 오차가 아예 없음).
OUTPUT_FORMAT_OPTIONS = [
    ("audio", "오디오만 (MP3)"),
    ("audio_srt", "오디오 + 자막 (오디오는 10분 분할, .srt 1개)"),
    ("audio_srt_single", "오디오 1개 + 자막 1개 (분할 없음, 매칭 정확도 최고)"),
]

# 출력 형식 → (srt_mode, split_audio)
OUTPUT_FORMAT_SETTINGS = {
    "audio":            ("none",   True),
    "audio_srt":        ("merged", True),
    "audio_srt_single": ("merged", False),
}

def get_default_config():
    """기본 설정 반환"""
    return {
        "output_directory": DEFAULT_OUTPUT_DIR,
        "default_voice": "Sohee",
        "default_tone": "natural",
        "default_volume": 70,
        "device": "auto",  # auto, cpu, cuda, mps
        "custom_device": None,  # auto가 아닐 경우 사용
        "model_size": "1.7B",  # 1.7B 또는 0.6B
        "tts_engine": "qwen",  # qwen 또는 kokoro
        "output_format": "audio",  # audio 또는 audio_srt
    }

def load_config():
    """설정 파일 로드"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 기본값과 병합
                default = get_default_config()
                for key, value in default.items():
                    if key not in config:
                        config[key] = value
                return config
        except:
            pass
    return get_default_config()

def save_config(config):
    """설정 파일 저장"""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"설정 저장 오류: {e}")
        return False

def get_device(config):
    """실제 사용할 디바이스 반환"""
    device_setting = config.get("device", "auto")
    
    if device_setting == "auto":
        return get_default_device()
    else:
        return device_setting

def get_available_devices():
    """사용 가능한 디바이스 목록 반환"""
    devices = [("auto", "자동 감지 (권장)"), ("cpu", "CPU (모든 시스템)")]
    
    try:
        import torch
        if torch.cuda.is_available():
            devices.append(("cuda", f"CUDA - NVIDIA GPU ({torch.cuda.get_device_name(0)})"))
        if torch.backends.mps.is_available():
            devices.append(("mps", "MPS - Apple Silicon GPU"))
    except:
        pass
    
    return devices
