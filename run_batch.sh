#!/bin/bash
# run_batch.sh - 야간 배치 변환을 세션과 분리해서 시작한다.
#
#   ./run_batch.sh
#
# - caffeinate: 작업 중 맥이 잠들지 않게 (idle/system/disk sleep 방지)
# - nohup + disown: 터미널·Claude Code 세션을 닫아도 계속 실행 (부모가 launchd로 이관)
# - 이미 돌고 있으면 두 번 띄우지 않는다

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$HOME/miniconda3/envs/qwen3-tts/bin/python"
SRC="/Volumes/Simon_NAS/Audiobooks/2026/08"
OUT="$REPO/audiofiles"
LOG="$HOME/Library/Logs/VoiceBookBatch.log"

if pgrep -f "[b]atch_runner.py" >/dev/null 2>&1; then
    echo "❌ 배치가 이미 실행 중입니다 (PID $(pgrep -f '[b]atch_runner.py' | tr '\n' ' '))"
    echo "   진행 상황: tail -f \"$LOG\""
    exit 1
fi

# 감시 루프: 러너가 예기치 않게 죽으면 자동으로 다시 시작한다.
# 완성된 파일(.mp3+.srt)은 건너뛰므로 재시작해도 이어서 진행된다.
# 정상 종료(exit 0)면 루프를 빠져나온다.
nohup caffeinate -ism bash -c '
    LOG="$1"; PYTHON="$2"; REPO="$3"; SRC="$4"; OUT="$5"
    for attempt in $(seq 1 20); do
        [ "$attempt" -gt 1 ] && \
            echo "[$(date "+%m-%d %H:%M:%S")] ♻️  러너가 종료됨 — 재시작 ($attempt/20)" >>"$LOG"
        "$PYTHON" "$REPO/batch_runner.py" \
            --voice Ryan --model-size 0.6B \
            "$SRC/Why the World Does Not Exist Part 2-3.txt" \
            "$SRC/Why the World Does Not Exist Part 4.txt" \
            "$SRC/Why the World Does Not Exist Part 5.txt" \
            "$SRC/Why the World Does Not Exist Part 6-7.txt" \
            >>"$LOG" 2>&1 && break
        sleep 30
    done
' _ "$LOG" "$PYTHON" "$REPO" "$SRC" "$OUT" >>"$LOG" 2>&1 &

disown
sleep 2
echo "✅ 배치 시작됨 (PID $(pgrep -f '[b]atch_runner.py' | head -1))"
echo "   진행 상황: tail -f \"$LOG\""
