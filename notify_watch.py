#!/usr/bin/env python
"""
notify_watch.py - 변환이 끝나는 파일을 감시해서 텔레그램 + 데스크톱 알림을 보낸다.

배치 러너와 완전히 별개의 프로세스로 돌기 때문에, 러너가 죽거나 재시작해도
알림은 독립적으로 동작한다. 표준 라이브러리만 쓴다 (torch 등 무거운 것 로드 금지).

  # macOS / Linux
  nohup python notify_watch.py "Why the World Does Not Exist Part 4" ... &

  # Windows
  pythonw notify_watch.py "Why the World Does Not Exist Part 4" ...

감시 대상은 인자로 넘긴다. 인자가 없으면 출력 폴더에서 아직 .srt가 없는
_audiobook 산출물을 자동으로 찾아 감시한다.
"""

import argparse
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import platform_utils as pu
from config_manager import load_config

pu.use_utf8_console()

SUFFIX = "_audiobook"
DEFAULT_POLL_SEC = 60
DEFAULT_MAX_HOURS = 20

log = pu.make_logger("VoiceBookNotify.log", env_var="VOICEBOOK_NOTIFY_LOG",
                     echo_tty=False)


def _load_telegram_credentials():
    """봇 토큰은 저장소에 두지 않는다 (공개 저장소).

    1) 환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
    2) 같은 폴더의 telegram_config.json (.gitignore에 등록됨)
    둘 다 없으면 텔레그램 알림만 생략하고 데스크톱 알림은 계속 동작한다.
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


def desktop_notify(text):
    pu.notify(text, title="VoiceBook 변환 완료")


def srt_hours(path):
    try:
        with open(path, encoding="utf-8") as f:
            stamps = [l for l in f if "-->" in l]
        h, m, rest = stamps[-1].split("-->")[1].strip().split(":")
        return int(h) + int(m) / 60 + float(rest.replace(",", ".")) / 3600
    except Exception:
        return None


def audio_mb(out_dir, stem):
    for ext in (".mp3", ".wav"):
        p = os.path.join(out_dir, stem + ext)
        if os.path.exists(p):
            return os.path.getsize(p) / 1048576
    return 0


def resolve_targets(out_dir, names):
    """(표시이름, 파일 stem) 목록. 인자로 넘긴 이름이 우선.

    이름은 "Part 4" 같은 짧은 이름이든 전체 stem이든 받는다.
    인자가 없으면 폴더에서 미완료 산출물(.wav/.mp3는 있는데 .srt가 없는 것)을 찾는다.
    """
    if names:
        targets = []
        for name in names:
            stem = name if name.endswith(SUFFIX) else name + SUFFIX
            targets.append((name, stem))
        return targets

    stems = set()
    try:
        entries = os.listdir(out_dir)
    except OSError:
        return []
    for entry in entries:
        stem, ext = os.path.splitext(entry)
        if ext.lower() in (".wav", ".mp3") and stem.endswith(SUFFIX):
            stems.add(stem)
    return [(s[: -len(SUFFIX)], s) for s in sorted(stems)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*",
                    help="감시할 산출물 이름. 생략하면 출력 폴더에서 자동 탐색")
    ap.add_argument("--dir", help="출력 폴더 (생략하면 config.json 값)")
    ap.add_argument("--poll", type=int, default=DEFAULT_POLL_SEC, help="확인 주기(초)")
    ap.add_argument("--max-hours", type=float, default=DEFAULT_MAX_HOURS,
                    help="이 시간이 지나면 감시를 종료")
    args = ap.parse_args()

    out_dir = args.dir or load_config().get("output_directory")
    targets = resolve_targets(out_dir, args.names)

    log("=" * 50)
    log(f"감시 시작 — 출력 폴더: {out_dir}")
    if not targets:
        log("감시할 대상이 없습니다. 종료.")
        return 1
    log(f"대상: {', '.join(n for n, _ in targets)}")

    pending = [(n, s) for n, s in targets
               if not os.path.exists(os.path.join(out_dir, s + ".srt"))]
    for name, stem in targets:
        if (name, stem) not in pending:
            log(f"{name}: 이미 완료 상태 — 알림 생략")

    deadline = time.time() + args.max_hours * 3600
    while pending and time.time() < deadline:
        time.sleep(args.poll)
        for item in list(pending):
            name, stem = item
            srt = os.path.join(out_dir, stem + ".srt")
            if not os.path.exists(srt):
                continue
            time.sleep(3)  # 파일 기록이 끝나도록 잠깐 대기
            hours = srt_hours(srt)
            length = f"{hours:.2f}시간 분량, " if hours else ""
            mb = audio_mb(out_dir, stem)
            msg = (f"✅ {name} 변환 완료\n"
                   f"{length}{mb:.0f}MB\n"
                   f"{datetime.now():%H:%M} · 남은 작업 {len(pending) - 1}개")
            telegram(msg)
            desktop_notify(f"{name} 완료 — {length}{mb:.0f}MB")
            pending.remove(item)

    if pending:
        telegram(f"⚠️ {args.max_hours}시간이 지나 감시를 종료합니다. 미완료: "
                 f"{', '.join(n for n, _ in pending)}")
    else:
        telegram("🏁 오디오북 변환 전체 완료")
        desktop_notify("전체 변환 완료")
    log("감시 종료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
