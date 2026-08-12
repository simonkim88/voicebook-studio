#!/bin/bash
# run_batch.sh - 야간 배치 변환을 세션과 분리해서 시작한다 (macOS / Linux).
#
#   ./run_batch.sh                          # $SRC 의 기본 파일 목록
#   ./run_batch.sh a.txt b.txt              # 변환할 파일을 직접 지정
#
# Windows에서는 run_batch.ps1 을 쓰세요.
#
# 환경변수로 조정:
#   VOICEBOOK_PYTHON  파이썬 실행파일 (기본: 저장소 venv → conda → python3)
#   VOICEBOOK_SRC     기본 입력 폴더
#   VOICEBOOK_VOICE   목소리 (기본 Ryan)
#   VOICEBOOK_MODEL   모델 크기 (기본 0.6B)
#
# - 슬립 방지: macOS는 caffeinate, Linux는 systemd-inhibit (있을 때만)
# - nohup + disown: 터미널·Claude Code 세션을 닫아도 계속 실행
# - 이미 돌고 있으면 두 번 띄우지 않는다

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------- 파이썬 찾기
find_python() {
    [ -n "${VOICEBOOK_PYTHON:-}" ] && { echo "$VOICEBOOK_PYTHON"; return; }
    for candidate in \
        "$REPO/venv/bin/python" \
        "$REPO/.venv/bin/python" \
        "$HOME/miniconda3/envs/qwen3-tts/bin/python" \
        "$HOME/anaconda3/envs/qwen3-tts/bin/python"
    do
        [ -x "$candidate" ] && { echo "$candidate"; return; }
    done
    command -v python3 || command -v python
}

PYTHON="$(find_python)"
[ -x "$PYTHON" ] || { echo "❌ 파이썬을 찾을 수 없습니다. VOICEBOOK_PYTHON 을 지정하세요."; exit 1; }

# ---------------------------------------------------------------- 경로
case "$(uname -s)" in
    Darwin) LOG_DIR="$HOME/Library/Logs" ;;
    *)      LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/VoiceBookStudio" ;;
esac
LOG="${VOICEBOOK_BATCH_LOG:-$LOG_DIR/VoiceBookBatch.log}"
mkdir -p "$(dirname "$LOG")"

SRC="${VOICEBOOK_SRC:-/Volumes/Simon_NAS/Audiobooks/2026/08}"
VOICE="${VOICEBOOK_VOICE:-Ryan}"
MODEL="${VOICEBOOK_MODEL:-0.6B}"

# 인자로 파일을 넘기지 않으면 기본 목록을 쓴다.
if [ "$#" -gt 0 ]; then
    FILES=("$@")
else
    FILES=(
        "$SRC/Why the World Does Not Exist Part 2-3.txt"
        "$SRC/Why the World Does Not Exist Part 4.txt"
        "$SRC/Why the World Does Not Exist Part 5.txt"
        "$SRC/Why the World Does Not Exist Part 6-7.txt"
    )
fi

if pgrep -f "[b]atch_runner.py" >/dev/null 2>&1; then
    echo "❌ 배치가 이미 실행 중입니다 (PID $(pgrep -f '[b]atch_runner.py' | tr '\n' ' '))"
    echo "   진행 상황: tail -f \"$LOG\""
    exit 1
fi

# ---------------------------------------------------------------- 슬립 방지
# 있으면 쓰고 없으면 그냥 맨몸으로 실행한다 (기능 자체는 동작해야 하므로).
if [ "$(uname -s)" = "Darwin" ] && command -v caffeinate >/dev/null 2>&1; then
    KEEPAWAKE=(caffeinate -ism)
elif command -v systemd-inhibit >/dev/null 2>&1; then
    KEEPAWAKE=(systemd-inhibit --what=idle:sleep --why="VoiceBook 배치 변환")
else
    KEEPAWAKE=()
    echo "⚠️  슬립 방지 도구가 없습니다 — 시스템 절전 설정을 확인하세요."
fi

# 감시 루프: 러너가 예기치 않게 죽으면 자동으로 다시 시작한다.
# 완성된 파일(.mp3+.srt)은 건너뛰므로 재시작해도 이어서 진행된다.
# 정상 종료(exit 0)면 루프를 빠져나온다.
nohup "${KEEPAWAKE[@]}" bash -c '
    LOG="$1"; PYTHON="$2"; REPO="$3"; VOICE="$4"; MODEL="$5"; shift 5
    for attempt in $(seq 1 20); do
        [ "$attempt" -gt 1 ] && \
            echo "[$(date "+%m-%d %H:%M:%S")] ♻️  러너가 종료됨 — 재시작 ($attempt/20)" >>"$LOG"
        "$PYTHON" "$REPO/batch_runner.py" \
            --voice "$VOICE" --model-size "$MODEL" "$@" >>"$LOG" 2>&1 && break
        sleep 30
    done
' _ "$LOG" "$PYTHON" "$REPO" "$VOICE" "$MODEL" "${FILES[@]}" >>"$LOG" 2>&1 &

disown
sleep 2
echo "✅ 배치 시작됨 (PID $(pgrep -f '[b]atch_runner.py' | head -1))"
echo "   파이썬: $PYTHON"
echo "   진행 상황: tail -f \"$LOG\""
