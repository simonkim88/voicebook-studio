#!/bin/bash
# setup.sh - Qwen3-TTS Audiobook App 설치 스크립트

echo "🎙️ Qwen3-TTS Audiobook Creator 설치"
echo "================================"

# 가상환경 확인
if [ -z "$CONDA_DEFAULT_ENV" ]; then
    echo "⚠️  Conda 가상환경이 활성화되어 있지 않습니다."
    echo "qwen3-tts 환경을 활성화하세요:"
    echo "   conda activate qwen3-tts"
    exit 1
fi

echo "✅ 가상환경: $CONDA_DEFAULT_ENV"

# 필요한 패키지 설치
echo "📦 필요한 패키지 설치 중..."
pip install PyQt6 PyQt6-Qt6 soundfile numpy

echo ""
echo "✅ 설치 완료!"
echo ""
echo "🚀 앱 실행 방법:"
echo "   python qwen3_audiobook_app.py"
echo ""
echo "📁 텍스트 파일을 드래그앤드롭하거나 클릭해서 선택하세요."
