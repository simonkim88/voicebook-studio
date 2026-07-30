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

3. **자막(.srt) 동시 출력 — Simon Reader 오디오-텍스트 매칭용**
   - "출력 형식"에서 `오디오만` / `오디오 + 자막(.srt)` 선택
   - 각 MP3와 **같은 이름**의 `.srt`를 함께 생성 (예: `book_01.mp3` ↔ `book_01.srt`)
   - Simon Reader의 "매칭 파일 가져오기"에 그대로 업로드하면 짝이 맞춰짐
   - 자세한 내용은 아래 [자막(.srt) 출력](#-자막srt-출력--simon-reader-연동) 참고

4. **모듈화된 코드 구조**
   - `voicebook_studio_v1.0.py` - 메인 애플리케이션
   - `config_manager.py` - 설정 관리
   - `document_parser.py` - 문서 파싱
   - `tts_worker.py` - TTS 백그라운드 작업
   - `srt_writer.py` - 자막(.srt) 생성
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
├── srt_writer.py            # 자막(.srt) 생성 모듈
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

## 📝 자막(.srt) 출력 — Simon Reader 연동

"출력 형식"에서 **오디오 + 자막(.srt)** 을 선택하면, 오디오와 함께 문장 단위로
시간이 매겨진 `.srt` 파일이 생성됩니다.

### 파일 짝짓기 규칙 (중요)

Simon Reader는 **파일명 stem**으로 오디오와 자막을 짝짓습니다. 따라서 자막은
항상 오디오와 같은 이름·같은 폴더에 생성됩니다.

```
book_audiobook_01.mp3  ↔  book_audiobook_01.srt
book_audiobook_02.mp3  ↔  book_audiobook_02.srt
```

10분 단위로 오디오가 분할되면 자막도 파일별로 나뉘며, **각 자막의 시간은 해당
오디오 파일의 0초 기준**입니다. 파일 경계에 걸친 문장은 양쪽에 잘려 들어갑니다.

### 사용법

1. 이 앱에서 `오디오 + 자막(.srt)`으로 변환
2. Simon Reader에 원본 문서(EPUB/PDF/DOCX/TXT)를 책으로 등록
3. 같은 책에 생성된 MP3를 오디오로 업로드
4. "매칭 파일 가져오기"로 `.srt`를 업로드 → 책 본문과 자동 정렬

### 타임스탬프 정확도

| 엔진 | 방식 |
|------|------|
| Kokoro-82M | 엔진이 준 구간 경계를 그대로 사용 + 구간 내부는 문장 단위로 세분화 |
| Qwen3-TTS | 문장별 글자 수 비례로 배분한 뒤, 경계를 실제 무음 구간으로 스냅 |

두 경우 모두 문장 사이의 묵음을 찾아 경계를 보정하므로, 문장 단위 하이라이트에
충분한 정확도가 나옵니다.

### 출력 형식 사양

Simon Reader의 alignment 임포터가 요구하는 형식을 그대로 따릅니다.

```
1
00:00:00,000 --> 00:00:02,850
The morning light came slowly through the window.

2
00:00:02,850 --> 00:00:06,450
She opened the old book and began to read aloud.
```

- 인코딩 UTF-8, 줄바꿈 LF, 블록 구분은 빈 줄
- 타임스탬프 구분자는 `" --> "` (앞뒤 공백 각 1칸)
- 자막 텍스트는 항상 한 줄 (내부 개행 없음), `end > start` 보장

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

- **v2.5** - 자막(.srt) 동시 출력
  - 출력 형식 선택 (오디오만 / 오디오 + 자막)
  - Simon Reader 호환 .srt 생성 (문장 단위, 무음 기반 경계 보정)
  - 파일 분할 시 자막도 파일별로 분할 (오디오와 파일명 stem 일치)

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
