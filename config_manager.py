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

def get_default_config():
    """기본 설정 반환"""
    return {
        "output_directory": DEFAULT_OUTPUT_DIR,
        "default_voice": "Sohee",
        "default_tone": "natural",
        "default_volume": 70,
        "device": "auto",  # auto, cpu, cuda, mps
        "custom_device": None  # auto가 아닐 경우 사용
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
