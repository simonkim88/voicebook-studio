# make_shortcut.ps1 - VoiceBook Studio 바로가기 생성 (Windows)
#
#   .\make_shortcut.ps1                 # 시작 메뉴 + 바탕화면
#   .\make_shortcut.ps1 -NoDesktop      # 시작 메뉴만
#
# macOS 의 make_app.sh (.app 번들) 에 대응합니다. 실행 로직(중복 실행 방지·로깅·
# 실패 알림)은 양쪽 모두 launch_voicebook.py 를 씁니다.
#
# 콘솔 창이 뜨지 않도록 pythonw.exe 를 대상으로 삼습니다.

[CmdletBinding(PositionalBinding = $false)]  # 전부 이름 지정 전용
param(
    [string]$Python = $env:VOICEBOOK_PYTHON,
    [switch]$NoDesktop,
    [switch]$Force          # 아이콘(.ico)이 이미 있어도 다시 만든다
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------------------------------------------------------------- 파이썬 찾기
function Find-PythonW {
    if ($Python) {
        # 콘솔 없는 pythonw.exe 가 옆에 있으면 그걸 쓴다
        $w = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
        if (Test-Path $w) { return @($w, $Python) }
        return @($Python, $Python)
    }
    $roots = @(
        "$Repo\venv\Scripts",
        "$Repo\.venv\Scripts",
        "$env:USERPROFILE\miniconda3\envs\qwen3-tts",
        "$env:USERPROFILE\anaconda3\envs\qwen3-tts"
    )
    foreach ($r in $roots) {
        if (Test-Path "$r\pythonw.exe") { return @("$r\pythonw.exe", "$r\python.exe") }
    }
    $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($cmd) {
        $console = Get-Command python -ErrorAction SilentlyContinue
        return @($cmd.Source, $(if ($console) { $console.Source } else { $cmd.Source }))
    }
    return $null
}

$found = Find-PythonW
if (-not $found) {
    Write-Host "[X] pythonw.exe 를 찾을 수 없습니다. -Python 또는 VOICEBOOK_PYTHON 을 지정하세요." -ForegroundColor Red
    exit 1
}
$PythonW, $PythonExe = $found

$LauncherPy = Join-Path $Repo 'launch_voicebook.py'
if (-not (Test-Path $LauncherPy)) {
    Write-Host "[X] launch_voicebook.py 가 없습니다: $LauncherPy" -ForegroundColor Red
    exit 1
}

Write-Host "▶ 저장소 : $Repo"
Write-Host "▶ 파이썬 : $PythonW"

# ---------------------------------------------------------------- 아이콘 (.ico)
# assets/icon.svg 를 여러 크기로 렌더링해서 멀티사이즈 .ico 로 묶는다.
# (Windows는 목록/타일/작업표시줄에서 서로 다른 크기를 골라 쓴다)
$IcoPath = Join-Path $Repo 'assets\AppIcon.ico'
if ($Force -or -not (Test-Path $IcoPath)) {
    Write-Host "▶ 아이콘 생성 중..."
    $py = @'
import struct, sys
from PyQt6.QtCore import QBuffer, QSize
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QColor
from PyQt6.QtSvg import QSvgRenderer

app = QGuiApplication(sys.argv)  # 위젯은 필요 없다 (렌더링만)
src, dst = sys.argv[1], sys.argv[2]
renderer = QSvgRenderer(src)
if not renderer.isValid():
    sys.exit(f"SVG를 읽을 수 없습니다: {src}")

pngs = []
for size in (16, 24, 32, 48, 64, 128, 256):
    img = QImage(QSize(size, size), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(p)
    p.end()
    # QBuffer(QByteArray()) 로 쓰면 임시 QByteArray가 GC되면서 죽는다. 인자 없이 만들 것.
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    pngs.append((size, bytes(buf.data())))
    buf.close()

# ICO: ICONDIR(6) + ICONDIRENTRY(16) * N + PNG 데이터
# Vista 이상은 엔트리에 PNG를 그대로 담을 수 있다 (BMP 변환 불필요).
offset = 6 + 16 * len(pngs)
header = struct.pack("<HHH", 0, 1, len(pngs))
entries, blobs = b"", b""
for size, data in pngs:
    dim = 0 if size >= 256 else size
    entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
    blobs += data
    offset += len(data)
with open(dst, "wb") as f:
    f.write(header + entries + blobs)
print(f"{dst} ({len(pngs)} sizes, {len(header + entries + blobs):,} bytes)")
'@
    $tmpPy = Join-Path $env:TEMP 'voicebook_make_ico.py'
    [System.IO.File]::WriteAllText($tmpPy, $py, (New-Object System.Text.UTF8Encoding $false))
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $PythonExe $tmpPy (Join-Path $Repo 'assets\icon.svg') $IcoPath
    Remove-Item -LiteralPath $tmpPy -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $IcoPath)) {
        Write-Host "⚠️  아이콘 생성 실패 — 기본 파이썬 아이콘으로 진행합니다." -ForegroundColor Yellow
        $IcoPath = $null
    }
}

# ---------------------------------------------------------------- 바로가기
function New-VoiceBookShortcut([string]$Path) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($Path)
    $sc.TargetPath = $PythonW
    $sc.Arguments = "`"$LauncherPy`""
    $sc.WorkingDirectory = $Repo
    $sc.Description = 'VoiceBook Studio - 텍스트를 오디오북으로'
    $sc.WindowStyle = 1
    if ($IcoPath) { $sc.IconLocation = "$IcoPath,0" }
    $sc.Save()
    Write-Host "✅ $Path" -ForegroundColor Green
}

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\VoiceBook Studio.lnk'
New-VoiceBookShortcut $startMenu

if (-not $NoDesktop) {
    $desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'VoiceBook Studio.lnk'
    New-VoiceBookShortcut $desktop
}

$logDir = if ($env:LOCALAPPDATA) { "$env:LOCALAPPDATA\VoiceBookStudio\Logs" } else { "$env:USERPROFILE\VoiceBookStudio\Logs" }
Write-Host "   로그: $logDir\VoiceBookStudio.log"
