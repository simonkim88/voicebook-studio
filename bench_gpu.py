#!/usr/bin/env python3
"""배치 크기 / torch.compile 조합을 실측해 최적값을 찾는다.

변환이 느릴 때 감으로 설정을 바꾸지 말고 이 스크립트로 재고 결정한다.
결과의 RTF(realtime factor)는 "생성한 오디오 길이 ÷ 걸린 시간"이라 클수록 좋다.
RTF 1.0이면 1분짜리 오디오를 만드는 데 1분 걸린다는 뜻이다.

사용법:
    python bench_gpu.py                      # 배치 1/2/4/8 비교
    python bench_gpu.py --batch 1 4 8 16     # 비교할 배치 크기 지정
    python bench_gpu.py --compile            # torch.compile 켜고 비교
    python bench_gpu.py --chunks 8           # 배치당 사용할 청크 수

주의: 다른 변환 작업이 돌고 있으면 GPU를 나눠 쓰게 되어 결과가 왜곡된다.
      반드시 다른 작업이 끝난 뒤에 실행할 것.
"""
import argparse
import re
import subprocess
import threading
import time

from config_manager import load_config, get_device

# 벤치마크용 고정 텍스트 (실제 변환과 비슷한 길이의 문장들)
SAMPLE_TEXTS = [
    "역사적으로 러시아와 우크라이나는 하나의 공간을 공유해 왔으며, 그 관계는 "
    "단순한 이웃 국가의 그것으로 설명되지 않는다.",
    "언어와 종교, 그리고 오랜 세월에 걸쳐 축적된 문화적 공통성은 두 민족을 "
    "잇는 가장 근본적인 토대였다고 할 수 있다.",
    "그러나 이십 세기의 정치적 격변은 이 공통의 유산을 여러 차례 다시 쓰게 "
    "만들었고, 그 결과는 오늘날까지 이어지고 있다.",
    "이 글은 그러한 역사적 맥락을 처음부터 되짚어 보려는 시도이며, 특정한 "
    "결론을 미리 정해 두고 쓰인 것은 아니다.",
    "우리는 서로의 역사를 존중하는 태도에서 출발해야 하며, 그것이 어떤 논의든 "
    "가능하게 만드는 최소한의 조건이 된다.",
    "경제적 협력과 안보 문제는 서로 분리되지 않으며, 한쪽을 무시한 채 다른 "
    "쪽만 논의하는 방식은 오래 지속되기 어렵다.",
    "각 세대는 자신이 물려받은 조건 위에서 판단을 내리지만, 그 판단의 결과는 "
    "다음 세대가 감당하게 된다는 점을 기억해야 한다.",
    "이러한 이유로 과거를 정확히 기술하는 일은 학문적 관심사에 그치지 않고 "
    "현재의 선택에 직접 영향을 미친다.",
]


def _gpu_utilization_macos():
    """macOS: ioreg로 GPU 사용률(%) 샘플 1개.

    ioreg는 한 줄에 키를 여러 개 붙여 내보내므로 정규식으로 뽑아야 한다
    (split('=')[1]로 자르면 뒤 키까지 딸려와 int 변환에 실패한다)."""
    out = subprocess.run(
        ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
        capture_output=True, text=True, timeout=5,
    ).stdout
    m = re.search(r'"Device Utilization %"=(\d+)', out)
    return int(m.group(1)) if m else None


def _gpu_utilization_nvidia():
    """NVIDIA: nvidia-smi로 GPU 사용률(%) 샘플 1개.

    ioreg와 달리 프로세스 기동이 비싸(~30ms) 샘플 간격을 너무 좁히면
    측정 자체가 CPU를 잡아먹는다. 기본 0.2초면 무해한 수준이다."""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=utilization.gpu",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip().splitlines()
    return int(out[0].strip()) if out else None


# 플랫폼별 샘플러를 한 번만 정해 두고 재사용한다 (매 샘플마다 탐색하지 않도록).
_UTIL_BACKEND = "unknown"


def gpu_utilization():
    """현재 GPU 사용률(%) 샘플 1개. 잴 수 없으면 None."""
    global _UTIL_BACKEND
    if _UTIL_BACKEND == "unknown":
        for name, fn in (("nvidia", _gpu_utilization_nvidia),
                         ("macos", _gpu_utilization_macos)):
            try:
                if fn() is not None:
                    _UTIL_BACKEND = name
                    break
            except Exception:
                continue
        else:
            _UTIL_BACKEND = "none"
    if _UTIL_BACKEND == "none":
        return None
    try:
        return (_gpu_utilization_nvidia if _UTIL_BACKEND == "nvidia"
                else _gpu_utilization_macos)()
    except Exception:
        return None


class GpuSampler:
    """측정 구간 동안 백그라운드에서 GPU 사용률을 계속 찍는다.

    청크 사이에만 재면 생성이 끝난 직후(=유휴)만 잡혀 실제보다 낮게 나온다."""

    def __init__(self, interval=0.2):
        self.interval = interval
        self.samples = []
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            u = gpu_utilization()
            if u is not None:
                self.samples.append(u)
            self._stop.wait(self.interval)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)

    @property
    def mean(self):
        return sum(self.samples) / len(self.samples) if self.samples else float("nan")

    @property
    def peak(self):
        return max(self.samples) if self.samples else float("nan")


def build_worker(device, cfg, batch_size, use_compile, compile_mode=None):
    """모델 로딩 로직을 재사용하려고 TTSWorker를 그대로 쓴다 (스레드는 안 띄움)."""
    from tts_worker import TTSWorker

    worker = TTSWorker(
        text="", output_path="/tmp/bench.wav",
        voice=cfg.get("default_voice", "Sohee"),
        device=device,
        model_size=cfg.get("model_size", "0.6B"),
        tts_engine="qwen",
    )
    worker.batch_size = batch_size
    worker.torch_compile = use_compile
    worker.torch_compile_mode = compile_mode
    return worker


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, nargs="+", default=[1, 2, 4, 8],
                    help="비교할 배치 크기 (기본: 1 2 4 8)")
    ap.add_argument("--compile", action="store_true",
                    help="torch.compile을 켜고 측정 (첫 배치는 컴파일 때문에 느림)")
    ap.add_argument("--mode", default=None,
                    help="torch.compile의 mode (default / reduce-overhead / "
                         "max-autotune). 생략하면 워커 기본값")
    ap.add_argument("--chunks", type=int, default=8,
                    help="한 번의 측정에 쓸 청크 수 (기본: 8)")
    ap.add_argument("--reps", type=int, default=2,
                    help="배치 크기마다 반복 측정할 횟수 (기본: 2). 반복 간 "
                         "편차가 곧 이 측정의 잡음 폭이고, 배치 간 차이가 "
                         "그보다 커야 의미가 있다")
    args = ap.parse_args()

    import torch

    cfg = load_config()
    device = get_device(cfg)
    texts = [SAMPLE_TEXTS[i % len(SAMPLE_TEXTS)] for i in range(args.chunks)]

    compile_label = f"ON (mode={args.mode or '기본값'})" if args.compile else "OFF"
    print(f"디바이스: {device} | 모델: {cfg.get('model_size')} | "
          f"torch.compile: {compile_label}")
    print(f"청크 {len(texts)}개 × {args.reps}회 반복으로 배치 크기 {args.batch} 비교")
    print("→ 실제 변환과 같은 확률적 샘플링으로 잰다. RTF는 생성 길이에 둔감해서"
          " (같은 문장을 10.8초/163.6초로 뽑아도 0.53/0.51) 길이가 달라도 비교된다.\n")

    is_cuda = device == "cuda"
    print(f"{'batch':>6} {'회':>3} {'생성 오디오':>12} {'걸린 시간':>10} {'RTF':>7} "
          f"{'GPU평균':>7} {'GPU최대':>7}" + (f" {'VRAM':>8}" if is_cuda else ""))
    print("-" * (62 + (9 if is_cuda else 0)))

    # 모델은 한 번만 올리고 배치 크기만 바꿔 가며 잰다 (로딩이 측정보다 오래 걸린다).
    # torch.compile은 모델을 제자리에서 바꾸므로 한 번만 적용한다.
    #
    # 디코딩은 반드시 기본값(샘플링)을 쓴다. do_sample=False로 고정하면 재현은
    # 되지만 EOS를 못 만나고 max_new_tokens까지 돌아버린다 (한 문장이 10.8초
    # 대신 163.6초로 나왔다). 실제와 다른 작업을 재게 되므로 쓸 수 없다.
    worker = build_worker(device, cfg, args.batch[0], args.compile, args.mode)
    print("모델 로딩 중...")
    worker._load_qwen(torch)
    if args.compile:
        worker._apply_torch_compile(torch)

    # 워밍업 (첫 호출은 커널 컴파일/캐시 때문에 항상 느리다)
    print("워밍업 중...\n")
    worker._generate_batch(torch, texts[:1])

    rtfs = {}   # batch_size -> [RTF, ...]
    for batch_size in args.batch:
        worker.batch_size = batch_size
        rtfs[batch_size] = []

        for rep in range(args.reps):
            samples = 0
            if is_cuda:
                torch.cuda.reset_peak_memory_stats()
            with GpuSampler() as sampler:
                t0 = time.time()
                for _, wav, sr, _ in worker._iter_generated(torch, texts):
                    samples += len(wav)
                elapsed = time.time() - t0

            audio_sec = samples / sr
            rtf = audio_sec / elapsed if elapsed else 0
            rtfs[batch_size].append(rtf)
            vram = (f" {torch.cuda.max_memory_allocated()/2**30:>6.2f}G"
                    if is_cuda else "")
            print(f"{batch_size:>6} {rep+1:>3} {audio_sec:>10.1f}초 {elapsed:>9.1f}초 "
                  f"{rtf:>7.2f} {sampler.mean:>5.0f}% {sampler.peak:>5.0f}%{vram}",
                  flush=True)

            worker._free_device_memory(torch)

    worker._release_model()

    print()

    means = {b: sum(v) / len(v) for b, v in rtfs.items()}
    print(f"{'batch':>6} {'평균 RTF':>10} {'최소~최대':>16}")
    for b in args.batch:
        v = rtfs[b]
        print(f"{b:>6} {means[b]:>10.2f} {min(v):>7.2f} ~ {max(v):<7.2f}")

    # 같은 설정을 반복했을 때의 편차가 이 측정의 잡음 폭이다.
    # 배치 간 차이가 그보다 작으면 아무것도 주장할 수 없다.
    noise = max((max(v) - min(v)) / (sum(v) / len(v)) for v in rtfs.values() if len(v) > 1) \
        if args.reps > 1 else None

    best_batch = max(means, key=means.get)
    baseline = means.get(1)
    gain = means[best_batch] / baseline if baseline else None
    print()

    if noise is not None:
        print(f"측정 잡음 폭(같은 설정 반복 시): {noise:.1%}")
    if gain is None:
        print("batch_size=1을 함께 재지 않아 비교 기준이 없다.")
        return

    print(f"가장 빠른 설정: batch_size={best_batch}, batch_size=1 대비 {gain:.2f}배")
    if noise is not None and (gain - 1) <= noise:
        print(f"\n→ 배치 간 차이({gain-1:+.1%})가 잡음 폭({noise:.1%})을 넘지 못한다. "
              "배치가 이득이라는 증거가 없으므로")
        print('  config.json에 "batch_size": 1 로 두는 것이 맞다.')
    else:
        print(f'\n→ 잡음 폭을 넘는 실제 이득이다. config.json에 '
              f'"batch_size": {best_batch}')
    if args.compile:
        print("\ntorch.compile을 끈 실행과 이 결과를 비교해 "
              '"torch_compile" 값을 정할 것.')


if __name__ == "__main__":
    main()
