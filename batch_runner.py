#!/usr/bin/env python
"""
batch_runner.py - 텍스트 파일 여러 개를 순서대로 음성 변환하는 무인 큐 러너

GUI 없이 TTSWorker를 직접 호출한다. 설정은 config.json을 그대로 따르므로
GUI에서 돌린 것과 동일한 조건(목소리/엔진/모델/출력형식)으로 처리된다.

  python batch_runner.py --wait-for "<진행 중인 작업의 출력 wav 경로>" --quit-gui  <파일...>

동작:
  1) 원본 텍스트를 로컬로 복사 (NAS 연결이 끊겨도 작업이 계속되도록)
  2) --wait-for 로 지정한 작업이 끝날 때까지 대기 (.srt 생성 = 완료 신호)
  3) --quit-gui 면 GUI 앱 종료 (메모리 확보)
  4) 파일을 하나씩 변환. 하나가 실패해도 다음 파일로 계속 진행
  5) 이미 완성본(.wav + .srt)이 있으면 건너뜀 → 중단 후 재실행해도 이어서 진행

macOS / Windows / Linux 공통으로 동작한다. 로그 경로, 데스크톱 알림, GUI 앱
탐색/종료 방식만 OS별로 갈라지고 나머지 흐름은 동일하다.
"""

import argparse
import functools
import os
import shutil
import subprocess
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import QCoreApplication

import platform_utils as pu
from config_manager import load_config, get_device, OUTPUT_FORMAT_SETTINGS
from document_parser import DocumentParser, CUSTOM_VOICE_PRESETS, load_custom_voices
from tts_worker import TTSWorker

pu.use_utf8_console()

INSTRUCT_TEXT = "자연스럽고 편안한 톤으로 읽어주세요."
LOCAL_INPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batch_input")
GUI_SCRIPT = "voicebook_studio_v1.0.py"

log = pu.make_logger("VoiceBookBatch.log", env_var="VOICEBOOK_BATCH_LOG")
LOG_PATH = log.path
notify = functools.partial(pu.notify, title="VoiceBook 배치")


def fmt(seconds):
    return str(timedelta(seconds=int(seconds)))


def final_audio(wav_path):
    """완성된 오디오 경로. 변환이 끝나면 wav는 mp3로 바뀌고 원본 wav는 지워진다."""
    mp3 = os.path.splitext(wav_path)[0] + ".mp3"
    if os.path.exists(mp3):
        return mp3
    if os.path.exists(wav_path):
        return wav_path
    return None


def srt_duration(srt_path):
    """마지막 자막 큐의 끝 시각 = 오디오 길이(시간). 실패하면 None."""
    try:
        with open(srt_path, encoding="utf-8") as f:
            stamps = [l for l in f if "-->" in l]
        if not stamps:
            return None
        h, m, rest = stamps[-1].split("-->")[1].strip().split(":")
        return int(h) + int(m) / 60 + float(rest.replace(",", ".")) / 3600
    except Exception:
        return None


def stage_inputs(paths):
    """원본을 로컬로 복사. NAS가 중간에 끊겨도 작업이 이어지도록."""
    os.makedirs(LOCAL_INPUT_DIR, exist_ok=True)
    staged = []
    for p in paths:
        if not os.path.exists(p):
            log(f"❌ 원본 없음, 건너뜀: {p}")
            continue
        local = os.path.join(LOCAL_INPUT_DIR, os.path.basename(p))
        if not os.path.exists(local) or os.path.getsize(local) != os.path.getsize(p):
            shutil.copy2(p, local)
            log(f"   복사됨: {os.path.basename(p)} ({os.path.getsize(local):,} bytes)")
        staged.append(local)
    return staged


def gui_pids():
    """실행 중인 GUI 앱의 PID 목록."""
    return pu.find_python_pids(GUI_SCRIPT)


def wait_for_current_job(wav_path, poll=60):
    """진행 중인 GUI 작업이 끝날 때까지 대기.

    완료 판정: 같은 이름의 .srt 생성 (merged 모드는 변환 완료 직후에 기록됨).
    앱이 .srt 없이 사라지면 그 작업은 미완성이므로 경고만 남기고 큐를 계속 진행한다
    (사용자 작업물을 임의로 덮어쓰지 않는다).
    """
    srt_path = os.path.splitext(wav_path)[0] + ".srt"
    mp3_path = os.path.splitext(wav_path)[0] + ".mp3"
    if os.path.exists(srt_path):
        log(f"✅ 선행 작업 이미 완료됨: {os.path.basename(srt_path)}")
        return True

    log(f"⏳ 선행 작업 대기 중 → {os.path.basename(wav_path)}")
    last_size, stagnant_since = -1, time.time()
    while True:
        if os.path.exists(srt_path):
            log(f"✅ 선행 작업 완료: {os.path.basename(srt_path)}")
            return True

        # wav → mp3 변환이 끝나면 wav가 사라진다. 자막 기록 직전 단계.
        if os.path.exists(mp3_path):
            log("   mp3 변환 완료 — 자막 기록 대기 중")
            time.sleep(poll)
            continue

        if not gui_pids():
            log("⚠️  GUI 앱이 .srt 없이 종료됨 — 선행 작업은 미완성입니다.")
            log("    (해당 파일은 건드리지 않고 큐를 계속 진행합니다)")
            return False

        size = os.path.getsize(wav_path) if os.path.exists(wav_path) else 0
        now = time.time()
        if size != last_size:
            hours = size / (24000 * 2) / 3600
            log(f"   진행 중… {size:,} bytes ({hours:.2f}시간 분량)")
            last_size, stagnant_since = size, now
        elif now - stagnant_since > 3600:
            log(f"   ⚠️ 1시간째 파일 크기 변화 없음 (멈췄을 수 있음) — 계속 대기")
            stagnant_since = now

        time.sleep(poll)


def quit_gui():
    pids = gui_pids()
    if not pids:
        log("   GUI 앱이 이미 종료되어 있습니다.")
        return
    for pid in pids:
        log(f"   GUI 앱 종료 (PID {pid})")
        pu.terminate_pid(pid)
    for _ in range(30):
        time.sleep(1)
        if not gui_pids():
            log("   ✅ 종료 완료 — 메모리 확보")
            return
    for pid in gui_pids():
        pu.terminate_pid(pid, force=True)
    log("   ✅ 종료 완료 (강제)")


def convert(text_path, out_dir, cfg, device):
    stem = os.path.splitext(os.path.basename(text_path))[0]
    out_path = os.path.join(out_dir, f"{stem}_audiobook.wav")
    srt_path = os.path.splitext(out_path)[0] + ".srt"

    if os.path.exists(srt_path) and final_audio(out_path):
        log(f"⏭️  이미 완료됨, 건너뜀: {stem}")
        return True

    text = DocumentParser.parse(text_path)
    # 스마트 필터는 쓰지 않는다: Part 2-3에서 본문 8,600자를 본문 시작 오판으로
    # 잘라내는 것을 확인했다. 전문을 그대로 읽힌다.
    log(f"▶️  시작: {stem}  ({len(text):,}자)")

    srt_mode, split_audio = OUTPUT_FORMAT_SETTINGS.get(
        cfg.get("output_format", "audio"), ("none", True)
    )
    voice = cfg.get("default_voice", "Sohee")
    is_custom = voice in CUSTOM_VOICE_PRESETS
    preset = CUSTOM_VOICE_PRESETS.get(voice, {})

    worker = TTSWorker(
        text=text, output_path=out_path, voice=voice,
        instruct_text=INSTRUCT_TEXT, device=device,
        is_custom_voice=is_custom,
        ref_audio_path=preset.get("ref_audio_path"),
        ref_text=preset.get("ref_text"),
        model_size=cfg.get("model_size", "1.7B"),
        tts_engine=cfg.get("tts_engine", "qwen"),
        kokoro_lang_code="a",
        srt_mode=srt_mode,
    )
    worker.split_audio = split_audio

    state = {"pct": -1, "last_log": 0.0, "error": None, "done": False}

    def on_progress(v):
        now = time.time()
        # 5% 단위 또는 5분마다 한 줄 (로그가 넘치지 않게)
        if v >= state["pct"] + 5 or now - state["last_log"] > 300:
            state["pct"], state["last_log"] = v, now
            log(f"   {stem}: {v}%")

    worker.progress.connect(on_progress)
    worker.eta.connect(lambda t: log(f"   {stem}: {t}"))
    worker.error.connect(lambda e: state.update(error=e))
    worker.finished_signal.connect(lambda p: state.update(done=True))

    t0 = time.time()
    worker.run()  # QThread.run()을 직접 호출 → 이 프로세스에서 동기 실행
    elapsed = time.time() - t0

    if state["error"]:
        log(f"❌ 실패: {stem} — {state['error']} (소요 {fmt(elapsed)})")
        return False

    audio = final_audio(out_path)
    if not audio:
        log(f"❌ 실패: {stem} — 오디오 파일이 생성되지 않았습니다 (소요 {fmt(elapsed)})")
        return False

    mb = os.path.getsize(audio) / 1048576
    hours = srt_duration(srt_path)
    length = f"{hours:.2f}시간 분량, " if hours else ""
    log(f"✅ 완료: {stem} — {length}{mb:.0f}MB, 소요 {fmt(elapsed)}")
    log(f"   → {os.path.basename(audio)}")
    notify(f"{stem} 완료 ({length}소요 {fmt(elapsed)})")
    if not os.path.exists(srt_path):
        log(f"   ⚠️ 자막이 생성되지 않았습니다: {os.path.basename(srt_path)}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="변환할 텍스트 파일 경로")
    ap.add_argument("--wait-for", help="먼저 끝나야 하는 진행 중 작업의 출력 wav 경로")
    ap.add_argument("--quit-gui", action="store_true", help="선행 작업 완료 후 GUI 앱 종료")
    ap.add_argument("--single", action="store_true",
                    help="파일 1개만 변환하고 종료 (부모 프로세스가 내부적으로 사용)")
    ap.add_argument("--voice", help="목소리 지정 (예: Ryan). 생략하면 config.json 값")
    ap.add_argument("--model-size", help="모델 크기 지정 (0.6B / 1.7B). 생략하면 config.json 값")
    args = ap.parse_args()

    def apply_overrides(cfg):
        """CLI로 넘어온 설정이 config.json보다 우선한다.

        config.json은 GUI에서 실행 중 바꾼 선택(목소리 등)을 반영하지 않으므로,
        재작업 시에는 반드시 명시적으로 넘긴다."""
        if args.voice:
            cfg["default_voice"] = args.voice
        if args.model_size:
            cfg["model_size"] = args.model_size
        return cfg

    QCoreApplication(sys.argv)  # QThread/시그널용 (GUI 없음)

    # 자식 모드: 파일 하나만 처리하고 프로세스를 끝낸다.
    # 변환 중 메모리가 계속 늘어나므로, 파일마다 프로세스를 새로 띄워
    # 다음 파일이 깨끗한 메모리에서 시작하도록 한다.
    if args.single:
        cfg = apply_overrides(load_config())
        load_custom_voices()
        ok = convert(args.files[0], cfg.get("output_directory"), cfg, get_device(cfg))
        return 0 if ok else 1

    cfg = apply_overrides(load_config())
    device = get_device(cfg)
    out_dir = cfg.get("output_directory")
    os.makedirs(out_dir, exist_ok=True)
    load_custom_voices()

    log("=" * 68)
    log(f"배치 시작 — {len(args.files)}개 파일")
    log(f"설정: voice={cfg.get('default_voice')} engine={cfg.get('tts_engine')} "
        f"model={cfg.get('model_size')} format={cfg.get('output_format')} device={device}")
    log(f"출력 폴더: {out_dir}")

    staged = stage_inputs(args.files)
    if not staged:
        log("❌ 처리할 파일이 없습니다. 종료.")
        return 1

    if args.wait_for:
        wait_for_current_job(args.wait_for)
    if args.quit_gui:
        quit_gui()
        time.sleep(5)  # 메모리 회수 여유

    t_all = time.time()
    ok = 0
    for i, path in enumerate(staged, 1):
        log(f"--- [{i}/{len(staged)}] ---")
        try:
            # 파일마다 새 프로세스. 변환 중 늘어난 메모리를 확실히 회수한다.
            child = [sys.executable, os.path.abspath(__file__), "--single", path]
            if args.voice:
                child += ["--voice", args.voice]
            if args.model_size:
                child += ["--model-size", args.model_size]
            r = subprocess.run(child)
            if r.returncode == 0:
                ok += 1
            else:
                log(f"❌ 자식 프로세스 종료 코드 {r.returncode}: {os.path.basename(path)}")
                detail = pu.describe_exit_code(r.returncode)
                if detail:
                    log(f"   {detail}")
        except Exception as e:  # 한 파일이 죽어도 큐는 계속
            import traceback
            log(f"❌ 예외 발생: {os.path.basename(path)} — {e}")
            log(traceback.format_exc())

    total = fmt(time.time() - t_all)
    log("=" * 68)
    log(f"🏁 배치 종료 — 성공 {ok}/{len(staged)}, 총 소요 {total}")
    notify(f"변환 완료 {ok}/{len(staged)}개 · 소요 {total}")
    return 0 if ok == len(staged) else 1


if __name__ == "__main__":
    sys.exit(main())
