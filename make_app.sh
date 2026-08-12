#!/bin/bash
# make_app.sh - VoiceBook Studio.app (macOS 앱 번들) 생성/재생성
#
#   ./make_app.sh              → /Applications 에 설치
#   ./make_app.sh ~/Desktop    → 원하는 위치에 설치
#
# 만들어진 앱은 Finder/Spotlight/Dock에서 바로 실행되며,
# launchd가 부모 프로세스라 터미널이나 Claude Code 세션과 무관하게 계속 살아있습니다.
#
# .app 번들은 macOS 전용입니다. Windows에서는 make_shortcut.ps1 로
# 시작 메뉴·바탕화면 바로가기를 만드세요 (동작은 동일합니다).

set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
    echo "❌ make_app.sh 는 macOS 전용입니다 (.app 번들 생성)."
    echo "   Windows: powershell -ExecutionPolicy Bypass -File make_shortcut.ps1"
    echo "   Linux  : python launch_voicebook.py 를 직접 실행하거나 .desktop 파일을 만드세요."
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="${1:-/Applications}"
APP="$DEST_DIR/VoiceBook Studio.app"

find_python() {
    [ -n "${VOICEBOOK_PYTHON:-}" ] && { echo "$VOICEBOOK_PYTHON"; return; }
    for candidate in \
        "$REPO_DIR/venv/bin/python" \
        "$REPO_DIR/.venv/bin/python" \
        "$HOME/miniconda3/envs/qwen3-tts/bin/python" \
        "$HOME/anaconda3/envs/qwen3-tts/bin/python"
    do
        [ -x "$candidate" ] && { echo "$candidate"; return; }
    done
    command -v python3 || command -v python
}

PYTHON="$(find_python)"
[ -x "$PYTHON" ] || { echo "❌ 파이썬을 찾을 수 없습니다. VOICEBOOK_PYTHON 을 지정하세요."; exit 1; }

echo "▶ 대상: $APP"
echo "▶ 파이썬: $PYTHON"

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
# 중복 실행 방지·로깅·실패 알림은 launch_voicebook.py 가 처리합니다
# (Windows 바로가기와 같은 코드를 씁니다).
cat > "$APP/Contents/MacOS/VoiceBookStudio" <<LAUNCHER
#!/bin/bash
# VoiceBook Studio 런처 (make_app.sh 가 생성 — 직접 수정하지 말 것)

APP_DIR="$REPO_DIR"
PYTHON="$PYTHON"
LAUNCHER_PY="\$APP_DIR/launch_voicebook.py"
LOG="\$HOME/Library/Logs/VoiceBookStudio.log"

mkdir -p "\$(dirname "\$LOG")"

fail() {
    echo "ERROR: \$1" >>"\$LOG"
    /usr/bin/osascript -e "display notification \"\$1\" with title \"VoiceBook Studio 실행 실패\"" >/dev/null 2>&1 &
    exit 1
}

[ -x "\$PYTHON" ] || fail "Python 환경을 찾을 수 없습니다: \$PYTHON"
[ -f "\$LAUNCHER_PY" ] || fail "런처를 찾을 수 없습니다: \$LAUNCHER_PY"
cd "\$APP_DIR" || fail "폴더로 이동할 수 없습니다: \$APP_DIR"

# exec: 스크립트 프로세스를 파이썬으로 교체 → 앱 종료 = 프로세스 종료
# (launch_voicebook.py 도 내부에서 exec 하므로 이 PID가 그대로 GUI가 됩니다)
exec "\$PYTHON" "\$LAUNCHER_PY"
LAUNCHER
chmod +x "$APP/Contents/MacOS/VoiceBookStudio"

# ---------------------------------------------------------------- 등록
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
touch "$APP"

echo "✅ 완료: $APP"
echo "   로그: ~/Library/Logs/VoiceBookStudio.log"
