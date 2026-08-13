#!/usr/bin/env python3
"""배치 크기 / torch.compile 조합을 실측해 최적값을 찾는다.

변환이 느릴 때 감으로 설정을 바꾸지 말고 이 스크립트로 재고 결정한다.
결과의 RTF(realtime factor)는 "생성한 오디오 길이 ÷ 걸린 시간"이라 클수록 좋다.
RTF 1.0이면 1분짜리 오디오를 만드는 데 1분 걸린다는 뜻이다.

사용법:
    python bench_gpu.py                      # 배치 1/2/4/8 비교
    python bench_gpu.py --batch 1 4 8 16     # 비교할 배치 크기 지정
    python bench_gpu.py --compile            # torch.compile 켜고 비교
    python bench_gpu.py --chunks 8           # 한 번의 측정에 쓸 청크 수
    python bench_gpu.py --text-file book.txt # 이 문서를 실제 경로로 청킹해 사용
    python bench_gpu.py --batch-chars 4000   # 한 배치의 글자 수 예산

주의: 다른 변환 작업이 돌고 있으면 GPU를 나눠 쓰게 되어 결과가 왜곡된다.
      반드시 다른 작업이 끝난 뒤에 실행할 것.
"""
import argparse
import glob
import os
import re
import statistics
import subprocess
import threading
import time

from config_manager import load_config, get_device

# 폴백용 짧은 문장들. 실제 문서를 찾지 못했을 때만 쓴다.
#
# 주의: 이건 실제 청크가 아니다. TTSWorker._chunk_text 는 단락을 max_chars=500
# 까지 채워 넣으므로 실제 청크는 평균 370~390자, 중앙값 400자 부근이다. 아래
# 문장들은 65자라 6배 짧고, 그 차이가 측정을 두 군데서 망가뜨린다:
#
#   ① 글자 수 예산(batch_chars=2000)이 걸리지 않는다. 실제로는 390자 청크가
#      5개면 예산이 차서 그룹이 끊기므로, batch_size 를 8/12/16 중 무엇으로
#      두든 실효 배치는 5다. 짧은 텍스트로는 B=16 이 그대로 반영되어 보여서
#      **운영에서 발생할 수 없는 구성**을 재게 된다.
#   ② 길이 편차가 사라진다. 실제 청크는 p10 196자 ~ p90 485자로 흩어져 있고,
#      배치는 가장 긴 시퀀스가 끝날 때까지 돌므로 그만큼 손해를 본다. 아래
#      문장들은 59~69자로 거의 균일해서 그 손해가 0에 가깝다 → 배치 이득이
#      과대평가된다.
#
# 그래서 기본 동작은 batch_input/ 의 실제 문서를 _chunk_text 로 청킹해 쓰는
# 것이다. 아래로 폴백하면 경고를 찍는다.
FALLBACK_TEXTS = [
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


def find_default_document():
    """벤치마크에 쓸 실제 문서. 없으면 None.

    batch_input/ 은 무인 배치 변환의 입력 폴더라 실제로 변환하는 것과 같은
    성격의 문서가 들어 있다. 가장 큰 파일을 고른다 — 청크가 충분히 나와야
    배치를 여러 개 만들어 볼 수 있다."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = glob.glob(os.path.join(here, "batch_input", "*.txt"))
    return max(candidates, key=os.path.getsize) if candidates else None


def load_chunks(count, path=None):
    """벤치 입력을 실제 변환과 똑같은 경로(_chunk_text)로 만든다.

    벤치마크가 자기 손으로 만든 문장을 그대로 쓰면 길이 분포가 실제와 어긋나고,
    그러면 글자 수 예산도 패딩 손해도 재현되지 않는다. 청킹 로직을 공유하는
    것이 유일하게 안전한 방법이다.

    문서가 없으면 폴백 문장을 단락으로 이어 붙여 같은 _chunk_text 에 태운다.
    짧은 문장을 그대로 쓰는 것과 달리, 이렇게 하면 500자까지 채워진 청크가
    나와 길이 분포가 실전과 구조적으로 같아진다."""
    from tts_worker import TTSWorker

    if path:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    else:
        text = "\n\n".join(FALLBACK_TEXTS * 40)
    worker = TTSWorker.__new__(TTSWorker)     # 청킹은 인스턴스 상태를 쓰지 않는다
    chunks = [c for c in worker._chunk_text(text) if c.strip()]
    if len(chunks) < count:
        raise SystemExit(
            f"❌ 청크가 {len(chunks)}개뿐입니다. --text-file 로 더 긴 원문을 주거나 "
            f"--chunks 를 줄이십시오.")
    # 문서 앞부분에는 목차·서문 같은 짧은 조각이 몰려 있는 경우가 있어 그대로
    # 앞에서 자르면 길이 분포가 치우친다. 중앙부에서 연속으로 떼어 온다.
    start = max(0, (len(chunks) - count) // 2)
    return chunks[start:start + count]


def describe_chunks(texts):
    """측정에 쓴 청크의 길이 분포를 한 줄로. 이 수치가 결과의 전제다."""
    lengths = sorted(len(t) for t in texts)
    if not lengths:
        return "청크 없음"
    p10 = lengths[len(lengths) // 10]
    p90 = lengths[min(len(lengths) - 1, len(lengths) * 9 // 10)]
    return (f"청크 {len(lengths)}개 | 글자 수 평균 {statistics.mean(lengths):.0f} "
            f"중앙 {statistics.median(lengths):.0f} "
            f"p10 {p10} p90 {p90} 최대 {lengths[-1]}")


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


def swap_used_gb():
    """macOS: 지금 쓰고 있는 스왑(GB). 실패하면 None.

    맥에서 '조용히 느려지는' 경로는 Windows 의 sysmem fallback 과 이름만
    다르다. 통합메모리가 모자라면 OOM 이 아니라 압축·스왑으로 넘어가고,
    예외가 없으니 _generate_batch 의 재귀 이등분도 돌지 않는다. 측정 중에
    스왑이 늘었다면 그 행은 느린 경로가 섞인 값이다."""
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"],
                             capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"used\s*=\s*([\d.]+)([MG])", out)
        if not m:
            return None
        v = float(m.group(1))
        return v / 1024 if m.group(2) == "M" else v
    except Exception:
        return None


class GpuSampler:
    """측정 구간 동안 백그라운드에서 GPU 사용률을 계속 찍는다.

    청크 사이에만 재면 생성이 끝난 직후(=유휴)만 잡혀 실제보다 낮게 나온다."""

    def __init__(self, interval=0.2, sample_mps_memory=False):
        self.interval = interval
        self.samples = []
        # MPS 에는 CUDA 의 max_memory_allocated / reset_peak_memory_stats 에
        # 해당하는 API 가 없다. 그래서 피크를 여기서 직접 표집한다 — 어차피
        # 사용률 때문에 0.2초마다 깨어나므로 추가 비용이 거의 없다.
        self.sample_mps_memory = sample_mps_memory
        self.mem_samples = []
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
            if self.sample_mps_memory:
                try:
                    import torch
                    # driver_allocated_memory 는 드라이버가 실제로 잡은 양이라
                    # current_allocated_memory(텐서 합)보다 실점유에 가깝다.
                    self.mem_samples.append(torch.mps.driver_allocated_memory())
                except Exception:
                    pass
            self._stop.wait(self.interval)

    @property
    def mem_peak_gb(self):
        return max(self.mem_samples) / 2**30 if self.mem_samples else float("nan")

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
    ap.add_argument("--text-file", default=None,
                    help="청크를 뽑아낼 문서. 생략하면 batch_input/ 에서 가장 큰 "
                         ".txt 를 쓴다. 실제 변환과 같은 _chunk_text 로 자른다")
    ap.add_argument("--batch-chars", type=int, nargs="+", default=[2000],
                    help="비교할 글자 수 예산 (기본: 2000). 실제 변환에서 배치 "
                         "크기를 실제로 결정하는 값이다 — 청크가 평균 400자라 "
                         "2000이면 개수 상한과 무관하게 4~5개에서 잘린다")
    args = ap.parse_args()

    import torch

    cfg = load_config()
    device = get_device(cfg)

    # 청크는 실제 문서에서 실제 경로로 뽑는다. 손으로 쓴 짧은 문장을 쓰면
    # 글자 수 예산과 길이 편차가 재현되지 않아 측정이 조용히 무의미해진다.
    doc = args.text_file or find_default_document()
    texts = load_chunks(args.chunks, doc)
    source = os.path.basename(doc) if doc else "폴백 문장(청킹 후)"

    compile_label = f"ON (mode={args.mode or '기본값'})" if args.compile else "OFF"
    print(f"디바이스: {device} | 모델: {cfg.get('model_size')} | "
          f"torch.compile: {compile_label}")
    print(f"텍스트: {source} | {describe_chunks(texts)}")
    print(f"{args.reps}회 반복 | 배치 {args.batch} × 글자 수 예산 {args.batch_chars}")
    print("→ 실제 변환과 같은 확률적 샘플링으로 잰다. RTF는 생성 길이에 둔감해서"
          " (같은 문장을 10.8초/163.6초로 뽑아도 0.53/0.51) 길이가 달라도 비교된다.\n")

    is_cuda = device == "cuda"
    # 물리 VRAM 을 넘겨 잡으면 Windows NVIDIA 드라이버가 OOM 을 내는 대신
    # 호스트 RAM 으로 흘려보낸다(sysmem fallback). 그러면 OutOfMemoryError 가
    # 발생하지 않아 _generate_batch 의 재귀 이등분도 돌지 않고, 측정값은
    # PCIe 를 타는 느린 경로가 섞인 것이 된다 — 조용히 무효가 되는 측정이다.
    # 그래서 총 용량을 미리 알아 두고 초과한 행에 표시를 단다.
    vram_total = (torch.cuda.get_device_properties(0).total_memory / 2**30
                  if is_cuda else None)
    if is_cuda:
        print(f"GPU 메모리: {vram_total:.2f} GiB "
              f"— 이 값을 넘는 행은 시스템 RAM 으로 샌 것이라 비교에 쓸 수 없다\n")

    # 맥도 같은 함정이 있다. recommended_max_memory 는 하드 한도가 아니라
    # 소프트 경계라, 넘어도 OOM 이 아니라 압축·스왑으로 조용히 느려진다.
    # (실제로 91분 변환 뒤 앱이 40.6GB 를 물고 스왑 12.3GB 까지 간 적이 있다.)
    # 통합메모리는 다른 앱과 공유하므로 이 한도는 '나 혼자 쓸 때'의 값이다.
    is_mps = device == "mps"
    mps_limit = torch.mps.recommended_max_memory() / 2**30 if is_mps else None
    if is_mps:
        print(f"MPS 권장 최대: {mps_limit:.1f} GiB (통합메모리 공유) "
              f"— 넘으면 오류 없이 압축·스왑으로 느려진다")
        print(f"측정 시작 시점 스왑: {swap_used_gb() or 0:.2f} GiB "
              f"— 회차 중 스왑이 늘면 그 행은 무효다\n")

    mem_col = " " * 0
    print(f"{'batch':>6} {'chars':>6} {'실효':>5} {'회':>3} {'생성 오디오':>12} "
          f"{'걸린 시간':>10} {'RTF':>7} {'GPU평균':>7} {'GPU최대':>7}"
          + (f" {'VRAM':>8}" if is_cuda else "")
          + (f" {'MPS메모리':>10} {'스왑Δ':>7}" if is_mps else ""))
    print("-" * (76 + (9 if is_cuda else 0) + (19 if is_mps else 0)))

    # 모델은 한 번만 올리고 배치 크기만 바꿔 가며 잰다 (로딩이 측정보다 오래 걸린다).
    # torch.compile은 모델을 제자리에서 바꾸므로 한 번만 적용한다.
    #
    # 디코딩은 반드시 기본값(샘플링)을 쓴다. do_sample=False로 고정하면 재현은
    # 되지만 EOS를 못 만나고 max_new_tokens까지 돌아버린다 (한 문장이 10.8초
    # 대신 163.6초로 나왔다). 실제와 다른 작업을 재게 되므로 쓸 수 없다.
    worker = build_worker(device, cfg, args.batch[0], args.compile, args.mode)

    # 워커가 조용히 배치를 쪼개는 경로(VRAM 부족 → 재귀 이등분)를 눈에 보이게
    # 한다. 이게 안 보이면 표의 '실효' 열은 의도한 크기를 찍는데 실제로는 더
    # 작은 배치가 돈, 알 수 없는 측정이 된다.
    worker.status.connect(lambda m: print(f"    [worker] {m}", flush=True))

    combos = [(b, c) for b in args.batch for c in args.batch_chars]

    # 설정값이 실제로 반영되는지 먼저 보여준다. 실제 청크는 평균 400자라
    # 예산 2000 이면 4~5개에서 그룹이 끊기고, 그 위로는 batch_size 를 올려도
    # 아무 일도 일어나지 않는다. 모르고 재면 존재하지 않는 구성을 비교한다.
    print("배치 구성 (설정값 → 실제 그룹 크기):")
    effective = {}
    for batch_size, chars in combos:
        worker.batch_size, worker.batch_chars = batch_size, chars
        sizes = [len(g) for g in worker._group_chunks(texts)]
        effective[(batch_size, chars)] = sum(sizes) / len(sizes)
        capped = "  ← 예산에 먼저 걸림" if max(sizes) < batch_size else ""
        print(f"  batch={batch_size:<3} chars={chars:<5} 그룹 {len(sizes)}개, "
              f"실효 {effective[(batch_size, chars)]:.1f}, 크기 {sizes}{capped}")
    print()

    print("모델 로딩 중...")
    worker._load_qwen(torch)
    if args.compile:
        worker._apply_torch_compile(torch)

    # 워밍업 (첫 호출은 커널 컴파일/캐시 때문에 항상 느리다)
    print("워밍업 중...\n")
    worker._generate_batch(torch, texts[:1])

    rtfs = {}          # (batch, chars) -> [RTF, ...]
    spilled_keys = set()   # 물리 VRAM 을 넘겨 시스템 RAM 을 쓴 조합
    for batch_size, chars in combos:
        worker.batch_size, worker.batch_chars = batch_size, chars
        rtfs[(batch_size, chars)] = []
        eff = effective[(batch_size, chars)]

        for rep in range(args.reps):
            samples = 0
            if is_cuda:
                torch.cuda.reset_peak_memory_stats()
            swap_before = swap_used_gb() if is_mps else None
            with GpuSampler(sample_mps_memory=is_mps) as sampler:
                t0 = time.time()
                for _, wav, sr, _ in worker._iter_generated(torch, texts):
                    samples += len(wav)
                elapsed = time.time() - t0
            swap_after = swap_used_gb() if is_mps else None

            audio_sec = samples / sr
            rtf = audio_sec / elapsed if elapsed else 0
            rtfs[(batch_size, chars)].append(rtf)
            peak = torch.cuda.max_memory_allocated() / 2**30 if is_cuda else 0
            spilled = is_cuda and peak > vram_total
            if spilled:
                spilled_keys.add((batch_size, chars))
            vram = f" {peak:>6.2f}G{'!' if spilled else ' '}" if is_cuda else ""

            # MPS: 피크 점유와 스왑 증가분. 스왑이 늘었으면 CUDA 의 sysmem
            # fallback 과 같은 이유로 그 행은 비교에 못 쓴다.
            mps_cols, swapped = "", False
            if is_mps:
                mem_peak = sampler.mem_peak_gb
                swap_delta = ((swap_after - swap_before)
                              if swap_before is not None and swap_after is not None
                              else float("nan"))
                swapped = swap_delta == swap_delta and swap_delta > 0.05
                over = mem_peak == mem_peak and mem_peak > mps_limit
                if swapped or over:
                    spilled_keys.add((batch_size, chars))
                mps_cols = (f" {mem_peak:>8.2f}G{'!' if over else ' '}"
                            f" {swap_delta:>+6.2f}G{'!' if swapped else ' '}")

            print(f"{batch_size:>6} {chars:>6} {eff:>5.1f} {rep+1:>3} "
                  f"{audio_sec:>10.1f}초 {elapsed:>9.1f}초 "
                  f"{rtf:>7.2f} {sampler.mean:>5.0f}% {sampler.peak:>5.0f}%"
                  f"{vram}{mps_cols}",
                  flush=True)
            if spilled:
                print(f"    ⚠️  VRAM {vram_total:.2f}G 초과 — 시스템 RAM 으로 샜다. "
                      f"이 행은 배치 설정 비교에 쓸 수 없다", flush=True)
            if is_mps and swapped:
                print(f"    ⚠️  측정 중 스왑이 {swap_delta:+.2f}G 늘었다 — 압축·스왑 "
                      f"경로가 섞였다. 이 행은 비교에 쓸 수 없다", flush=True)

            worker._free_device_memory(torch)

    worker._release_model()

    print()

    means = {k: sum(v) / len(v) for k, v in rtfs.items()}
    print(f"{'batch':>6} {'chars':>6} {'실효':>5} {'평균 RTF':>10} {'최소~최대':>16}")
    for key in combos:
        v = rtfs[key]
        print(f"{key[0]:>6} {key[1]:>6} {effective[key]:>5.1f} "
              f"{means[key]:>10.2f} {min(v):>7.2f} ~ {max(v):<7.2f}")

    # 같은 설정을 반복했을 때의 편차가 이 측정의 잡음 폭이다.
    # 조합 간 차이가 그보다 작으면 아무것도 주장할 수 없다.
    noise = max((max(v) - min(v)) / (sum(v) / len(v)) for v in rtfs.values() if len(v) > 1) \
        if args.reps > 1 else None

    # 시스템 RAM 으로 샌 조합은 후보에서 뺀다. 그 행이 빨라 보여도 이 기계에서
    # 안전하게 쓸 수 있는 설정이 아니고, 애초에 측정 자체가 오염돼 있다.
    usable = {k: v for k, v in means.items() if k not in spilled_keys}
    if spilled_keys and usable:
        dropped = ", ".join(f"batch={b}/chars={c}" for b, c in sorted(spilled_keys))
        print(f"VRAM 초과로 후보에서 제외: {dropped}")
    if not usable:
        print("모든 조합이 VRAM 을 넘겼다. 예산을 낮춰 다시 잴 것.")
        return
    best = max(usable, key=usable.get)
    # 기준선은 실효 배치가 가장 작은 조합 (보통 batch=1, 없으면 예산이 가장 빡빡한 것)
    base_key = min(effective, key=effective.get)
    gain = means[best] / means[base_key] if means[base_key] else None
    print()

    if noise is not None:
        print(f"측정 잡음 폭(같은 설정 반복 시): {noise:.1%}")

    # 실효 크기가 같은 조합끼리는 같은 것을 두 번 잰 것이다. 이걸 성능 차이로
    # 읽지 않도록 못을 박아 둔다 — 이번 작업에서 실제로 저지른 착각이다.
    dupes = {}
    for key in combos:
        dupes.setdefault(round(effective[key], 1), []).append(key)
    for eff, keys in sorted(dupes.items()):
        if len(keys) > 1:
            names = ", ".join(f"batch={b}/chars={c}" for b, c in keys)
            print(f"⚠️  실효 {eff} 로 동일한 조합: {names} — 같은 것을 여러 번 잰 것이다")

    if gain is None:
        print("기준선 조합의 RTF가 0이라 배율을 낼 수 없다.")
        return

    print(f"가장 빠른 설정: batch_size={best[0]}, batch_chars={best[1]} "
          f"(실효 {effective[best]:.1f}) — 실효 {effective[base_key]:.1f} 대비 {gain:.2f}배")
    if noise is not None and (gain - 1) <= noise:
        print(f"\n→ 조합 간 차이({gain-1:+.1%})가 잡음 폭({noise:.1%})을 넘지 못한다. "
              "키울 이유가 없으므로 기본값을 유지한다.")
    else:
        print(f'\n→ 잡음 폭을 넘는 실제 이득이다. tts_worker 의 batch_chars 를 '
              f'{best[1]} 로, batch_size 를 {best[0]} 로 맞추는 것을 검토할 것 '
              f'(둘의 정합이 맞아야 설정이 의미를 갖는다).')
    if args.compile:
        print("\ntorch.compile을 끈 실행과 이 결과를 비교해 "
              '"torch_compile" 값을 정할 것.')


if __name__ == "__main__":
    main()
