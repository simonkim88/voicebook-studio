#!/bin/bash
# make_app.sh - VoiceBook Studio.app (macOS 앱 번들) 생성/재생성
#
#   ./make_app.sh              → /Applications 에 설치
#   ./make_app.sh ~/Desktop    → 원하는 위치에 설치
#
# 만들어진 앱은 Finder/Spotlight/Dock에서 바로 실행되며,
# launchd가 부모 프로세스라 터미널이나 Claude Code 세션과 무관하게 계속 살아있습니다.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="${1:-/Applications}"
APP="$DEST_DIR/VoiceBook Studio.app"
PYTHON="$HOME/miniconda3/envs/qwen3-tts/bin/python"

echo "▶ 대상: $APP"

# ---------------------------------------------------------------- 아이콘
if [ ! -f "$REPO_DIR/assets/AppIcon.icns" ]; then
    echo "▶ 아이콘 생성 중..."
    ICONSET="$(mktemp -d)/AppIcon.iconset"
    mkdir -p "$ICONSET"
    render() {
        QT_QPA_PLATFORM=offscreen "$PYTHON" - "$REPO_DIR/assets/icon.svg" "$ICONSET/$2.png" "$1" <<'PY'
import sys
from PyQt6.QtGui import QImage, QPainter, QColor
from PyQt6.QtCore import QSize
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
src, dst, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
img = QImage(QSize(size, size), QImage.Format.Format_ARGB32_Premultiplied)
img.fill(QColor(0, 0, 0, 0))
p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
QSvgRenderer(src).render(p)
p.end()
img.save(dst)
PY
    }
    render 16 icon_16x16;      render 32 'icon_16x16@2x'
    render 32 icon_32x32;      render 64 'icon_32x32@2x'
    render 128 icon_128x128;   render 256 'icon_128x128@2x'
    render 256 icon_256x256;   render 512 'icon_256x256@2x'
    render 512 icon_512x512;   render 1024 'icon_512x512@2x'
    iconutil -c icns "$ICONSET" -o "$REPO_DIR/assets/AppIcon.icns"
fi

# ---------------------------------------------------------------- 번들 뼈대
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$REPO_DIR/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>                  <string>VoiceBook Studio</string>
    <key>CFBundleDisplayName</key>           <string>VoiceBook Studio</string>
    <key>CFBundleExecutable</key>            <string>VoiceBookStudio</string>
    <key>CFBundleIdentifier</key>            <string>com.simonkim.voicebookstudio</string>
    <key>CFBundleIconFile</key>              <string>AppIcon</string>
    <key>CFBundleInfoDictionaryVersion</key> <string>6.0</string>
    <key>CFBundlePackageType</key>           <string>APPL</string>
    <key>CFBundleShortVersionString</key>    <string>1.0</string>
    <key>CFBundleVersion</key>               <string>1</string>
    <key>LSMinimumSystemVersion</key>        <string>11.0</string>
    <key>NSHighResolutionCapable</key>       <true/>
    <key>NSHumanReadableCopyright</key>      <string>Simon Kim</string>
</dict>
</plist>
PLIST

# ---------------------------------------------------------------- 런처
cat > "$APP/Contents/MacOS/VoiceBookStudio" <<LAUNCHER
#!/bin/bash
# VoiceBook Studio 런처 (make_app.sh 가 생성 — 직접 수정하지 말 것)

APP_DIR="$REPO_DIR"
PYTHON="$PYTHON"
MAIN="voicebook_studio_v1.0.py"
LOG="\$HOME/Library/Logs/VoiceBookStudio.log"

mkdir -p "\$(dirname "\$LOG")"
exec >>"\$LOG" 2>&1
echo "===== launch \$(date '+%Y-%m-%d %H:%M:%S') ====="

fail() {
    echo "ERROR: \$1"
    /usr/bin/osascript -e "display alert \"VoiceBook Studio를 실행할 수 없습니다\" message \"\$1\" as critical" >/dev/null 2>&1
    exit 1
}

# 이미 실행 중이면 두 번 띄우지 않음 (TTS 모델이 메모리를 크게 차지).
# 알림은 반드시 논블로킹(display notification)으로 — display alert 는 응답할 때까지
# 런처 프로세스를 붙잡고, 그동안 macOS가 앱을 "실행 중"으로 봐서 재실행이 막힙니다.
if /usr/bin/pgrep -f "bin/python .*\$MAIN" >/dev/null 2>&1; then
    echo "already running — skip"
    /usr/bin/osascript -e 'display notification "이미 실행 중입니다. 열려 있는 창을 사용하세요." with title "VoiceBook Studio"' >/dev/null 2>&1 &
    exit 0
fi

[ -x "\$PYTHON" ] || fail "Python 환경을 찾을 수 없습니다: \$PYTHON (conda 환경 qwen3-tts 확인 필요)"
[ -f "\$APP_DIR/\$MAIN" ] || fail "앱 파일을 찾을 수 없습니다: \$APP_DIR/\$MAIN"
cd "\$APP_DIR" || fail "폴더로 이동할 수 없습니다: \$APP_DIR"

# exec: 스크립트 프로세스를 파이썬으로 교체 → 앱 종료 = 프로세스 종료
exec "\$PYTHON" "\$MAIN"
LAUNCHER
chmod +x "$APP/Contents/MacOS/VoiceBookStudio"

# ---------------------------------------------------------------- 등록
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
touch "$APP"

echo "✅ 완료: $APP"
echo "   로그: ~/Library/Logs/VoiceBookStudio.log"
