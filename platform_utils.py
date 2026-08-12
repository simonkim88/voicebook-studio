# platform_utils.py - OS별로 갈리는 것들 (로그 위치 · 데스크톱 알림 · 프로세스 · 외부 도구)
"""
표준 라이브러리만 쓴다.

notify_watch.py 처럼 torch/PyQt를 절대 로드하면 안 되는 가벼운 감시 프로세스에서도
그대로 import할 수 있어야 하기 때문이다. psutil은 있으면 쓰고, 없으면 OS 기본
도구(pgrep / Get-CimInstance)로 폴백한다 — requirements.txt 필수 항목이 아니다.
"""

import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime

try:
    import psutil
except ImportError:
    psutil = None

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MACOS

# 자식 프로세스(알림·프로세스 조회)를 띄울 때 콘솔 창이 깜빡이지 않도록.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


def use_utf8_console():
    """콘솔 기본 인코딩이 UTF-8이 아닌 환경(Windows cp949 등)에서 이모지·한글
    출력이 UnicodeEncodeError로 죽지 않게 한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# ------------------------------------------------------------------ 로그
def log_dir():
    """OS별 표준 로그 디렉터리. VOICEBOOK_LOG_DIR 로 덮어쓸 수 있다."""
    override = os.environ.get("VOICEBOOK_LOG_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if IS_MACOS:
        return os.path.expanduser("~/Library/Logs")
    if IS_WINDOWS:
        return os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
            "VoiceBookStudio", "Logs",
        )
    return os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
        "VoiceBookStudio",
    )


def log_path(filename, env_var=None):
    """로그 파일의 전체 경로. env_var가 지정되면 그 환경변수가 최우선."""
    if env_var:
        override = os.environ.get(env_var)
        if override:
            return os.path.abspath(os.path.expanduser(override))
    return os.path.join(log_dir(), filename)


def make_logger(filename, env_var=None, echo_tty=True):
    """타임스탬프를 붙여 UTF-8로 기록하는 로거를 만든다.

    echo_tty: 터미널에서 직접 돌릴 때만 화면에도 찍는다. 백그라운드 실행 시
    stdout이 같은 파일로 리다이렉트되는 경우가 많아 중복 기록을 막기 위함.
    """
    path = log_path(filename, env_var)

    def log(msg):
        line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
        if echo_tty and sys.stdout.isatty():
            print(line, flush=True)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # 로그를 못 쓴다고 본 작업까지 멈출 이유는 없다.

    log.path = path
    return log


# ------------------------------------------------------------------ 알림
# PowerShell 토스트. AppId는 Windows PowerShell 것을 빌려 쓴다
# (전용 AppId를 등록하지 않고도 알림 센터에 뜨게 하는 표준적인 방법).
_PS_TOAST = """
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $xml.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($xml.CreateTextNode('{title}')) | Out-Null
$nodes.Item(1).AppendChild($xml.CreateTextNode('{message}')) | Out-Null
$appId = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
    [Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def notify(message, title="VoiceBook Studio"):
    """데스크톱 알림. 반드시 논블로킹이어야 하고, 실패해도 무시한다.

    macOS에서 display alert 같은 모달을 쓰면 응답할 때까지 프로세스를 붙잡으므로
    쓰지 않는다. 토스트/notification만 사용한다.
    """
    if IS_MACOS:
        cmd = ["osascript", "-e",
               f'display notification "{message}" with title "{title}"']
    elif IS_WINDOWS:
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               _PS_TOAST.format(title=title.replace("'", "''"),
                                message=message.replace("'", "''"))]
    else:
        cmd = ["notify-send", title, message]
    try:
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    except (OSError, ValueError):
        pass


# ------------------------------------------------------------------ 프로세스
def _pids_via_psutil(script_name):
    pids = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.info["pid"] == os.getpid():
                continue
            cmdline = proc.info.get("cmdline") or []
            if not cmdline or "python" not in os.path.basename(cmdline[0]).lower():
                continue
            if any(script_name in part for part in cmdline[1:]):
                pids.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def _pids_via_shell(script_name):
    """psutil이 없을 때의 폴백. OS 기본 도구로 커맨드라인을 뒤진다."""
    if IS_WINDOWS:
        script = (
            "Get-CimInstance Win32_Process "
            "| Where-Object { $_.Name -like 'python*' "
            f"-and $_.CommandLine -like '*{script_name}*' }} "
            "| ForEach-Object { $_.ProcessId }"
        )
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        cmd = ["pgrep", "-f", rf"python.*{script_name}"]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, creationflags=NO_WINDOW,
        ).stdout.split()
    except (OSError, ValueError):
        return []
    return [int(p) for p in out if p.isdigit() and int(p) != os.getpid()]


def find_python_pids(script_name):
    """해당 파이썬 스크립트를 돌리고 있는 프로세스의 PID 목록 (자기 자신 제외)."""
    if psutil is not None:
        return _pids_via_psutil(script_name)
    return _pids_via_shell(script_name)


def terminate_pid(pid, force=False):
    """종료 요청. Windows의 taskkill(무옵션)은 창에 WM_CLOSE를 보내므로
    PyQt 앱이 SIGTERM처럼 정상 종료 경로를 탈 수 있다."""
    try:
        if IS_WINDOWS:
            cmd = ["taskkill", "/PID", str(pid)] + (["/F", "/T"] if force else [])
            subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
            )
        else:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except (OSError, ValueError):
        pass


def describe_exit_code(code):
    """비정상 종료 코드를 사람이 읽을 수 있게. 정상 범위면 None."""
    if not IS_WINDOWS and code < 0:
        return f"시그널 {-code}로 종료됨 (메모리 부족에 의한 강제 종료 가능성)"
    if IS_WINDOWS and code > 0xFF:
        return (f"비정상 종료 0x{code & 0xFFFFFFFF:08X} "
                f"(메모리 부족에 의한 강제 종료 가능성)")
    return None


# ------------------------------------------------------------------ 외부 도구
def find_tool(name):
    """ffmpeg/ffprobe 같은 외부 실행파일 찾기. 없으면 None.

    1) 환경변수 (FFMPEG / FFPROBE)
    2) PATH
    3) imageio-ffmpeg 번들 (ffmpeg 한정) — Windows에서 별도 설치 없이 쓰이는 흔한 경로
    """
    override = os.environ.get(name.upper())
    if override and os.path.isfile(override):
        return override
    found = shutil.which(name)
    if found:
        return found
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return None


def require_tool(name):
    """find_tool + 친절한 설치 안내. 없으면 SystemExit."""
    path = find_tool(name)
    if path:
        return path
    if IS_MACOS:
        how = "brew install ffmpeg"
    elif IS_WINDOWS:
        how = "winget install Gyan.FFmpeg  (또는 choco install ffmpeg)"
    else:
        how = "sudo apt install ffmpeg"
    sys.exit(f"❌ {name} 을(를) 찾을 수 없습니다.\n"
             f"   설치: {how}\n"
             f"   또는 환경변수 {name.upper()} 에 실행파일 경로를 지정하세요.")
