# VoiceBook Studio v1.0
# AI 오디오북 생성기 - Qwen3-TTS Powered

크로스 플랫폼 (Mac/Windows/Linux) 지원하는 Qwen3-TTS 기반 오디오북 생성기

---

## 🆕 v2.2 주요 기능

### 크로스 플랫폼 지원
- ✅ **macOS** - Apple Silicon (MPS) / Intel (CPU)
- ✅ **Windows** - NVIDIA GPU (CUDA) / CPU
- ✅ **Linux** - NVIDIA GPU (CUDA) / CPU

### 새로운 기능
1. **디바이스 자동 감지 + 수동 선택**
   - 시스템에 따라 자동으로 최적의 디바이스 선택
   - 설정 화면에서 수동으로 CPU/CUDA/MPS 선택 가능

2. **예상 소요 시간 (ETA) 표시**
   - 실시간 남은 시간 계산 및 표시
   - 청크별 처리 시간 기반 정밀 예측

3. **모듈화된 코드 구조**
   - `voicebook_studio_v1.0.py` - 메인 애플리케이션
   - `config_manager.py` - 설정 관리
   - `document_parser.py` - 문서 파싱
   - `tts_worker.py` - TTS 백그라운드 작업
   - `ui_components.py` - UI 위젯
   - `language_detector.py` - 언어 감지
   - `content_filter.py` - 본문 필터

---

## 📁 파일 구조

```
Qwen3-TTSApp/
├── voicebook_studio_v1.0.py  # 메인 애플리케이션
├── config_manager.py         # 설정 관리 모듈
├── document_parser.py        # 문서 파싱 모듈
├── tts_worker.py            # TTS 작업 모듈
├── ui_components.py         # UI 위젯 모듈
├── language_detector.py     # 언어 감지 모듈
├── content_filter.py        # 본문 필터 모듈
├── requirements.txt         # 의존성 목록
└── config.json             # 사용자 설정 (자동 생성)
```

---

## 🚀 설치 및 실행

### 1. PyTorch 설치 (시스템별)

**macOS (Apple Silicon):**
```bash
pip install torch torchvision torchaudio
```

**Windows/Linux (NVIDIA GPU):**
```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

**Windows/Linux (CPU only):**
```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
```

### 2. 기타 패키지 설치
```bash
pip install PyQt6 qwen-tts soundfile numpy

# 선택적: 문서 파서
pip install pymupdf python-docx ebooklib
```

### 3. 실행
```bash
python voicebook_studio_v1.0.py
```

---

## ⚙️ 설정

### 설정 메뉴 (⌘, 또는 메뉴 > 설정)

**디바이스 선택:**
- `auto` - 자동 감지 (권장)
- `cpu` - CPU만 사용 (모든 시스템)
- `cuda` - NVIDIA GPU (Windows/Linux)
- `mps` - Apple Silicon GPU (Mac)

**저장 폴�더:**
- 기본: `~/Documents/Qwen3-TTSApp/audiofiles/`
- 사용자 지정 가능

---

## 🎯 사용법

1. **파일 선택**: 드래그앤드롭 또는 클릭
   - 지원: TXT, RTF, PDF, DOCX, EPUB

2. **또는 직접 입력**: 탭 전환 후 텍스트 입력

3. **목소리 선택**: 9개 목소리 중 선택
   - Vivian, Serena, Uncle_Fu, Dylan, Eric (Chinese)
   - Ryan, Aiden (English)
   - Ono_Anna (Japanese)
   - Sohee (Korean) ⭐

4. **톤 선택**: 자연스러운/차분한/밝은/진지한/감정적인

5. **변환 시작**: 버튼 클릭
   - 진행률 및 예상 시간 표시

6. **재생/저장**: 생성된 오디오 확인 및 저장

---

## 🖥️ 지원 플랫폼

| 플랫폼 | CPU | GPU | 테스트 상태 |
|--------|-----|-----|-------------|
| macOS (Apple Silicon) | ✅ | ✅ MPS | 테스트됨 |
| macOS (Intel) | ✅ | ❌ | 미테스트 |
| Windows (NVIDIA) | ✅ | ✅ CUDA | 미테스트 |
| Windows (CPU) | ✅ | ❌ | 미테스트 |
| Linux (NVIDIA) | ✅ | ✅ CUDA | 미테스트 |

---

## 🐛 문제 해결

### CUDA 오류 (Windows)
```
CUDA not available
```
→ NVIDIA 드라이버 및 CUDA Toolkit 설치 필요

### MPS 오류 (Mac Intel)
```
MPS not available
```
→ Intel Mac은 CPU 모드 사용

### 모듈 Import 오류
```
ModuleNotFoundError
```
→ 모든 .py 파일이 같은 폴�더에 있는지 확인

---

## 📝 버전 기록

- **v1.0** - VoiceBook Studio 출시
  - 크로스 플랫폼 지원 (Mac/Windows/Linux)
  - 자동 언어 감지 (한/영/중/일)
  - 본문 스마트 필터링
  - 10분 단위 파일 분할 + 1초 오버랩
  - 재생 속도 조절 (0.5x ~ 2.0x)
  - 중간 중지 및 부분 저장 기능

---

## 💡 팁

- **긴 문서**: 자동으로 청크 분할 처리
- **첫 실행**: 모델 다운로드에 시간 소요 (3-5분)
- **M4 Pro**: 1페이지 약 10-30초 소요

---

*제작: Kimi (Moonshot AI)*  
*Qwen3-TTS by Alibaba Cloud*
