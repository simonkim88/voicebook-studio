#!/usr/bin/env python
"""
launch_voicebook.py - GUI 앱 런처 (macOS .app 번들 / Windows 바로가기 공용)

바로가기·앱 아이콘에서 실행되므로 터미널이 없다. 따라서
  - 모든 출력은 로그 파일로 보낸다
  - 실패는 데스크톱 알림으로 알린다 (모달 금지 — 프로세스를 붙잡으면 안 됨)
  - 이미 실행 중이면 두 번 띄우지 않는다 (TTS 모델이 메모리를 크게 차지)
"""

import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import platform_utils as pu

MAIN = "voicebook_studio_v1.0.py"
TITLE = "VoiceBook Studio"


def main():
    repo = os.path.dirname(os.path.abspath(__file__))
    log = pu.make_logger("VoiceBookStudio.log", env_var="VOICEBOOK_APP_LOG",
                         echo_tty=False)
    log(f"===== launch {datetime.now():%Y-%m-%d %H:%M:%S} =====")

    if pu.find_python_pids(MAIN):
        log("already running — skip")
        pu.notify("이미 실행 중입니다. 열려 있는 창을 사용하세요.", title=TITLE)
        return 0

    main_path = os.path.join(repo, MAIN)
    if not os.path.exists(main_path):
        log(f"ERROR: 앱 파일 없음 — {main_path}")
        pu.notify(f"앱 파일을 찾을 수 없습니다: {MAIN}", title=TITLE)
        return 1

    os.chdir(repo)
    if pu.IS_WINDOWS:
        # pythonw.exe 로 실행되면 콘솔이 없다. 부모와 분리해 띄우고 런처는 빠진다.
        try:
            with open(log.path, "a", encoding="utf-8") as f:
                subprocess.Popen(
                    [sys.executable, main_path], cwd=repo,
                    stdout=f, stderr=f, stdin=subprocess.DEVNULL,
                    creationflags=subprocess.DETACHED_PROCESS,
                )
        except OSError as e:
            log(f"ERROR: 실행 실패 — {e}")
            pu.notify(f"실행할 수 없습니다: {e}", title=TITLE)
            return 1
        return 0

    # macOS/Linux: exec으로 프로세스를 교체한다.
    # .app 번들에서 앱 종료 = 이 프로세스 종료가 되도록 (launchd가 그렇게 추적한다).
    try:
        fd = os.open(log.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        if fd > 2:
            os.close(fd)
        os.execv(sys.executable, [sys.executable, main_path])
    except OSError as e:
        log(f"ERROR: 실행 실패 — {e}")
        pu.notify(f"실행할 수 없습니다: {e}", title=TITLE)
        return 1


if __name__ == "__main__":
    sys.exit(main())
