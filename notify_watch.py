#!/usr/bin/env python
"""
notify_watch.py - 변환이 끝나는 파일을 감시해서 텔레그램 + macOS 알림을 보낸다.

배치 러너와 완전히 별개의 프로세스로 돌기 때문에, 러너가 죽거나 재시작해도
알림은 독립적으로 동작한다. 표준 라이브러리만 쓴다 (torch 등 무거운 것 로드 금지).

  nohup python notify_watch.py > /dev/null 2>&1 &
"""

import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

def _load_telegram_credentials():
    """봇 토큰은 저장소에 두지 않는다 (공개 저장소).

    1) 환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
    2) 같은 폴더의 telegram_config.json (.gitignore에 등록됨)
    둘 다 없으면 텔레그램 알림만 생략하고 macOS 알림은 계속 동작한다.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return token, chat_id
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_config.json")
    try:
        import json
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("bot_token"), cfg.get("chat_id")
    except Exception:
        return None, None


TOKEN, CHAT_ID = _load_telegram_credentials()

OUT_DIR = "/Users/simon/Documents/Qwen3-TTSApp/audiofiles"
LOG = os.path.expanduser("~/Library/Logs/VoiceBookNotify.log")
POLL_SEC = 60
MAX_HOURS = 20

TARGETS = [
    ("Part 2-3", "Why the World Does Not Exist Part 2-3_audiobook"),
    ("Part 4", "Why the World Does Not Exist Part 4_audiobook"),
    ("Part 5", "Why the World Does Not Exist Part 5_audiobook"),
    ("Part 6-7", "Why the World Does Not Exist Part 6-7_audiobook"),
]


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}\n")


def telegram(text):
    if not TOKEN or not CHAT_ID:
        log("텔레그램 자격정보 없음 — 전송 생략 (TELEGRAM_BOT_TOKEN/CHAT_ID 또는 telegram_config.json)")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data),
            timeout=30,
        )
        log(f"텔레그램 전송: {text}")
        return True
    except Exception as e:
        log(f"텔레그램 실패: {e}")
        return False


def mac_notify(text):
    try:
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{text}" with title "VoiceBook 변환 완료"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def srt_hours(path):
    try:
        with open(path, encoding="utf-8") as f:
            stamps = [l for l in f if "-->" in l]
        h, m, rest = stamps[-1].split("-->")[1].strip().split(":")
        return int(h) + int(m) / 60 + float(rest.replace(",", ".")) / 3600
    except Exception:
        return None


def audio_mb(stem):
    for ext in (".mp3", ".wav"):
        p = os.path.join(OUT_DIR, stem + ext)
        if os.path.exists(p):
            return os.path.getsize(p) / 1048576
    return 0


def main():
    log("=" * 50)
    log(f"감시 시작 — 대상: {', '.join(n for n, _ in TARGETS)}")

    pending = [(n, s) for n, s in TARGETS
               if not os.path.exists(os.path.join(OUT_DIR, s + ".srt"))]
    for name, stem in TARGETS:
        if (name, stem) not in pending:
            log(f"{name}: 이미 완료 상태 — 알림 생략")

    deadline = time.time() + MAX_HOURS * 3600
    while pending and time.time() < deadline:
        time.sleep(POLL_SEC)
        for item in list(pending):
            name, stem = item
            srt = os.path.join(OUT_DIR, stem + ".srt")
            if not os.path.exists(srt):
                continue
            time.sleep(3)  # 파일 기록이 끝나도록 잠깐 대기
            hours = srt_hours(srt)
            length = f"{hours:.2f}시간 분량, " if hours else ""
            msg = (f"✅ {name} 변환 완료\n"
                   f"{length}{audio_mb(stem):.0f}MB\n"
                   f"{datetime.now():%H:%M} · 남은 작업 {len(pending) - 1}개")
            telegram(msg)
            mac_notify(f"{name} 완료 — {length}{audio_mb(stem):.0f}MB")
            pending.remove(item)

    if pending:
        telegram(f"⚠️ {MAX_HOURS}시간이 지나 감시를 종료합니다. 미완료: "
                 f"{', '.join(n for n, _ in pending)}")
    else:
        telegram("🏁 오디오북 변환 전체 완료 (Part 1 ~ Part 6-7)")
        mac_notify("전체 변환 완료")
    log("감시 종료")


if __name__ == "__main__":
    sys.exit(main())
