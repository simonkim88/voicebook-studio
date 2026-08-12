#!/usr/bin/env python
"""
merge_audiobook.py - 파트별 mp3 + srt를 순서대로 이어붙여 통합본 1쌍을 만든다.

핵심은 자막 타임스탬프 보정이다. 각 파트의 자막은 0초부터 시작하므로,
앞 파트들의 "실제 오디오 길이 합"만큼 밀어줘야 통합 오디오와 맞는다.
자막에 적힌 마지막 큐 시각이 아니라 ffprobe로 잰 실제 mp3 길이를 기준으로 삼는다
(둘은 몇 초 어긋날 수 있고, 그 오차가 뒤로 갈수록 누적되기 때문).

  python merge_audiobook.py
"""

import os
import subprocess
import sys

AUDIO_DIR = "/Users/simon/Documents/Qwen3-TTSApp/audiofiles"
BASE = "Why the World Does Not Exist"
PARTS = ["Part 1", "Part 2-3", "Part 4", "Part 5", "Part 6-7"]  # 이어붙일 순서
OUT_STEM = f"{BASE}_FULL"


def part_paths(part):
    stem = os.path.join(AUDIO_DIR, f"{BASE} {part}_audiobook")
    return stem + ".mp3", stem + ".srt"


def duration(path):
    """mp3 프레임 수 × 프레임당 샘플 수 / 샘플레이트 = 정확한 길이(초).

    format=duration 은 비트레이트 기반 추정이라 파트당 수십 ms씩 어긋나고,
    그 오차가 뒤 파트로 갈수록 누적된다. 프레임 수로 세면 정확히 맞는다
    (concat -c copy 는 프레임을 그대로 보존하므로 합이 통합본과 일치한다).
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets,sample_rate",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    sample_rate, packets = int(out[0]), int(out[1])
    frame_samples = 1152 if sample_rate >= 32000 else 576  # MPEG-1 / MPEG-2·2.5 Layer III
    return packets * frame_samples / sample_rate


def to_seconds(stamp):
    h, m, rest = stamp.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))


def to_stamp(seconds):
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path):
    """[(start_sec, end_sec, [text...]), ...] 로 파싱."""
    with open(path, encoding="utf-8-sig") as f:
        blocks = f.read().replace("\r\n", "\n").strip().split("\n\n")
    cues = []
    for b in blocks:
        lines = [l for l in b.split("\n") if l.strip() != ""]
        if len(lines) < 2:
            continue
        # 첫 줄이 번호면 버리고, 타임스탬프 줄을 찾는다
        ts_idx = 0 if "-->" in lines[0] else 1
        if ts_idx >= len(lines) or "-->" not in lines[ts_idx]:
            continue
        start, end = lines[ts_idx].split("-->")
        cues.append((to_seconds(start), to_seconds(end), lines[ts_idx + 1:]))
    return cues


def main():
    out_mp3 = os.path.join(AUDIO_DIR, OUT_STEM + ".mp3")
    out_srt = os.path.join(AUDIO_DIR, OUT_STEM + ".srt")

    # 1) 입력 점검 + 길이 측정
    print("파트별 실제 길이 (ffprobe 기준)")
    durations, srt_files, offsets, cursor = [], [], [], 0.0
    for part in PARTS:
        mp3, srt = part_paths(part)
        for p in (mp3, srt):
            if not os.path.exists(p):
                sys.exit(f"❌ 파일 없음: {p}")
        d = duration(mp3)
        cues = parse_srt(srt)
        last_cue_end = cues[-1][1] if cues else 0.0
        print(f"  {part:<9} {d/3600:6.3f}시간  시작 {to_stamp(cursor)}  "
              f"자막 {len(cues):4d}개  마지막 큐 {last_cue_end/3600:.3f}시간 "
              f"(오디오와 차이 {d - last_cue_end:+.2f}초)")
        durations.append(d)
        srt_files.append(srt)
        offsets.append(cursor)
        cursor += d
    print(f"  {'합계':<9} {cursor/3600:6.3f}시간")

    # 2) 오디오 이어붙이기 (재인코딩 없음 = 음질 손실 없음)
    list_path = os.path.join(AUDIO_DIR, ".concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for part in PARTS:
            f.write(f"file '{part_paths(part)[0]}'\n")
    print(f"\n오디오 병합 중 → {os.path.basename(out_mp3)}")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", list_path, "-c", "copy", out_mp3],
        check=True,
    )
    os.remove(list_path)

    # 3) 자막 이어붙이기 (앞 파트 길이만큼 시각 이동)
    print(f"자막 병합 중 → {os.path.basename(out_srt)}")
    n = 0
    with open(out_srt, "w", encoding="utf-8") as out:
        for srt, offset in zip(srt_files, offsets):
            for start, end, text in parse_srt(srt):
                n += 1
                out.write(f"{n}\n{to_stamp(start + offset)} --> {to_stamp(end + offset)}\n")
                out.write("\n".join(text) + "\n\n")

    # 4) 검증
    merged_dur = duration(out_mp3)
    merged_cues = parse_srt(out_srt)
    print("\n검증")
    print(f"  통합 오디오 {merged_dur/3600:.4f}시간 "
          f"(각 파트 합 {cursor/3600:.4f}시간, 차이 {merged_dur - cursor:+.3f}초)")
    print(f"  통합 자막   {len(merged_cues)}개 (파트별 합 {n}개)")
    print(f"  마지막 큐   {to_stamp(merged_cues[-1][1])} / 오디오 {to_stamp(merged_dur)}")
    over = [c for c in merged_cues if c[1] > merged_dur + 1]
    print(f"  오디오 길이를 넘는 큐: {len(over)}개")
    mono = all(merged_cues[i][0] <= merged_cues[i + 1][0] for i in range(len(merged_cues) - 1))
    print(f"  시각 단조 증가: {'예' if mono else '아니오 ❌'}")
    print(f"\n  {out_mp3}\n  {out_srt}")


if __name__ == "__main__":
    main()
