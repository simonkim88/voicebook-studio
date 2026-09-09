# srt_writer.py - Simon Reader 호환 .srt 자막(오디오-텍스트 매칭) 생성 모듈
#
# Simon Reader의 alignment 임포터(backend/audio_processor.py::_parse_srt)는
# 책 본문용 SRT 파서보다 규칙이 엄격하다. 아래 형식을 반드시 지켜야 한다.
#
#   1                                        <- 일련번호 (1부터)
#   00:00:00,000 --> 00:00:04,120            <- " --> " (앞뒤 공백 각 1칸)
#   문장 텍스트 (한 줄, 빈 줄 포함 금지)
#   <빈 줄>
#
#   - 블록 구분자는 빈 줄("\n\n"). 자막 텍스트 안에 빈 줄이 있으면 블록이 깨진다.
#   - 줄바꿈은 LF("\n"). 인코딩은 UTF-8.
#   - end > start 인 블록만 채택된다 (같으면 버려짐).
#   - 임포트 시 오디오 파일과 "파일명 stem"으로 짝을 맞추므로
#     (audiobook.py::_normalize_stem) .srt 파일명은 오디오와 동일해야 한다.
#     예) my_book_01.mp3  ↔  my_book_01.srt
#
# 원문 줄바꿈: SRT 큐는 "타이밍" 단위라 원문의 줄·문단 경계가 남지 않는다.
# 그래서 .srt 옆에 같은 이름의 레이아웃 사이드카(<stem>.srtlayout.json)를 같이 쓴다.
# Simon Reader의 srt_parser 가 이걸 읽어 <p>/<br> 를 복원한다.
#
#   {"version":1,"cue_count":1286,"breaks":[[0,0],[3,0],[3,18],...]}
#
#   - breaks 의 각 항목은 [큐 인덱스(0부터), 문자 오프셋]
#   - 오프셋 0  = 그 큐에서 새 문단이 시작한다 (<p>)
#   - 오프셋 >0 = 큐 텍스트 안쪽의 줄바꿈 위치 (<br>)
#   - cue_count 가 .srt 의 블록 수와 다르면 리더가 통째로 무시한다
#   - 큐 텍스트는 절대 바꾸지 않는다 → 오디오-텍스트 매칭에 영향이 없다
import json
import os
import re

import numpy as np

# 문장 경계 (한국어/영어/일본어/중국어 문장부호). 개행은 아래에서 따로 다룬다.
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?。！？])\s*')

# 원문 줄바꿈 = 자막 문단 경계. 빈 줄이든 한 줄이든 "여기서 줄이 바뀐다"는 뜻은 같다.
_LINE_SPLIT = re.compile(r'\n+')

# 큐 텍스트는 SRT 한 줄로 나가므로, 오프셋을 재기 전에 미리 한 줄로 만들어 둔다
_WS_RUN = re.compile(r'\s+')

# 매칭에 의미가 있는 글자만 남기는 패턴 (공백/문장부호 제거, CJK는 \w에 포함)
_LETTERS_ONLY = re.compile(r'[^\w]', re.UNICODE)

# 이보다 짧은 문장은 앞 문장에 붙인다 (0.2초짜리 파편 큐 방지)
_MIN_SENTENCE_LETTERS = 4

# 레이아웃 사이드카 확장자 (Simon Reader srt_parser.LAYOUT_SUFFIX 와 같아야 한다)
LAYOUT_SUFFIX = ".srtlayout.json"


def format_srt_time(ms):
    """밀리초(int) → 'HH:MM:SS,mmm'"""
    if ms < 0:
        ms = 0
    h, rem = divmod(int(ms), 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def _letters(text):
    """매칭에 쓰이는 실질 글자 수 (공백·문장부호 제외)"""
    return _LETTERS_ONLY.sub('', text or '')


def split_units(text, starts_block=True):
    """텍스트를 자막 단위로 분할하면서 원문 줄바꿈 위치를 함께 기록한다.

    반환: [{'text': str, 'breaks': [문자 오프셋, ...]}, ...]
      - breaks 에 0 이 있으면 그 큐에서 새 문단이 시작된다 (리더의 <p> 경계)
      - 0 보다 큰 오프셋은 큐 안쪽 줄바꿈 위치다 (리더의 <br>)

    큐 텍스트 자체는 예전과 똑같다. 줄바꿈은 텍스트를 건드리지 않고 오프셋으로만
    남기므로, 오디오-텍스트 매칭(큐 텍스트 대조)에 영향을 주지 않는다.

    starts_block: 이 텍스트가 원문에서 새 문단으로 시작하는가. 긴 문단을 잘라
    만든 이어지는 청크라면 False 를 넘겨야 문단이 쪼개지지 않는다.
    """
    if not text:
        return []

    # (조각, 이 조각 앞에 원문 줄바꿈이 있었는가)
    units = []
    for line in _LINE_SPLIT.split(text):
        line = line.strip()
        if not line:
            continue
        first_of_line = True
        for part in _SENTENCE_SPLIT.split(line):
            part = _WS_RUN.sub(' ', part).strip()
            if not part:
                continue
            units.append((part, first_of_line and (bool(units) or starts_block)))
            first_of_line = False
    if not units:
        return []

    out = []
    pending = None   # 줄 첫머리에 있던 너무 짧은 조각 (뒤 조각 앞에 붙인다)
    for part, brk in units:
        if pending is not None:
            # "1." + "Definition of art" → 한 큐. 원문에서 같은 줄이었으니 붙는 게 맞다.
            breaks = [0]
            if brk:
                breaks.append(len(pending) + 1)  # 정말 줄이 달랐다면 <br> 로 남긴다
            out.append({'text': f"{pending} {part}", 'breaks': breaks})
            pending = None
            continue

        # 너무 짧은 조각은 이웃 큐에 붙인다 (0.2초짜리 파편 큐 방지)
        if len(_letters(part)) < _MIN_SENTENCE_LETTERS:
            if brk:
                # 줄 첫머리 조각("1." 같은 항목 번호)은 그 줄 뒤쪽에 붙여야 한다.
                # 앞 큐에 붙이면 앞줄 끝에 매달린다 ("Part I 1.")
                pending = part
                continue
            if out:
                out[-1]['text'] = f"{out[-1]['text']} {part}"
                continue

        out.append({'text': part, 'breaks': [0] if brk else []})

    if pending is not None:
        # 뒤에 붙일 조각이 없으면 앞 큐 뒤에 매단다 (줄바꿈은 오프셋으로 남긴다)
        if out:
            cur = out[-1]
            cur['breaks'].append(len(cur['text']) + 1)
            cur['text'] = f"{cur['text']} {pending}"
        else:
            out.append({'text': pending, 'breaks': [0] if starts_block else []})

    # 첫 조각이 너무 짧아 병합되지 못한 경우 뒤 큐에 붙인다
    if len(out) > 1 and len(_letters(out[0]['text'])) < _MIN_SENTENCE_LETTERS:
        head, nxt = out[0], out[1]
        shift = len(head['text']) + 1
        nxt['breaks'] = sorted(set(head['breaks'] + [o + shift for o in nxt['breaks']]))
        nxt['text'] = f"{head['text']} {nxt['text']}"
        out.pop(0)

    return out


def split_sentences(text):
    """텍스트를 문장 단위로 분할 (줄바꿈 정보 없이 텍스트만)."""
    return [u['text'] for u in split_units(text)]


def _snap_to_silence(audio, sample_rate, boundaries, max_shift_sec=0.4):
    """문자 수 비례로 추정한 경계를 실제 무음 구간으로 스냅.

    TTS는 문장 사이에 짧은 묵음을 넣으므로, 추정 경계 주변에서 RMS가 가장 낮은
    프레임을 찾아 이동시키면 문장 경계 정확도가 눈에 띄게 올라간다.
    실패하거나 여유가 없으면 추정값을 그대로 쓴다 (항상 단조 증가 보장).
    """
    if audio is None or len(audio) == 0 or not boundaries:
        return boundaries

    frame = max(1, int(sample_rate * 0.02))  # 20ms
    n_frames = len(audio) // frame
    if n_frames < 3:
        return boundaries

    block = np.asarray(audio[:n_frames * frame], dtype=np.float32).reshape(n_frames, frame)
    rms = np.sqrt(np.mean(block * block, axis=1))

    max_shift = int(sample_rate * max_shift_sec)
    snapped = []
    prev = 0
    for i, bound in enumerate(boundaries):
        nxt = boundaries[i + 1] if i + 1 < len(boundaries) else len(audio)
        # 양옆 구간의 30%를 넘지 않는 선에서만 이동 (인접 문장 침범 방지)
        shift = min(max_shift, int((bound - prev) * 0.3), int((nxt - bound) * 0.3))
        if shift < frame:
            snapped.append(bound)
            prev = bound
            continue

        lo = max(prev + frame, bound - shift)
        hi = min(len(audio) - frame, bound + shift)
        lo_f, hi_f = lo // frame, hi // frame
        if hi_f <= lo_f:
            snapped.append(bound)
            prev = bound
            continue

        best_f = int(np.argmin(rms[lo_f:hi_f + 1])) + lo_f
        new_bound = best_f * frame + frame // 2
        new_bound = max(prev + frame, min(new_bound, len(audio)))
        snapped.append(new_bound)
        prev = new_bound

    return snapped


class SrtTimeline:
    """생성된 오디오 전체(전역 샘플 좌표)에 대한 자막 큐를 누적한다.

    오디오는 10분 단위로 여러 파일에 나뉘어 저장되므로, 큐는 일단 전역 샘플
    좌표로 모아두고 파일을 저장하는 시점에 해당 구간만 잘라 0초 기준으로
    다시 계산한다(cues_for_range).
    """

    def __init__(self):
        self.cues = []    # {'start': 샘플, 'end': 샘플, 'text': str, 'breaks': [int]}
        self.cursor = 0   # 지금까지 누적된 전역 샘플 수

    # --- 큐 누적 ---

    def add_engine_segments(self, segments, sample_rate, starts_block=True):
        """엔진이 구간별 오디오를 직접 준 경우 (Kokoro).

        segments: [(텍스트, 오디오 ndarray)]
        Kokoro는 청크를 문장이 아니라 토큰 한도 기준으로 나누므로 한 구간에
        여러 문장이 들어있을 수 있다. 구간 경계는 엔진이 준 정확한 값이므로
        그대로 쓰고, 구간 안쪽만 문장 단위로 다시 쪼갠다.
        """
        for i, (text, audio) in enumerate(segments):
            # 문단 시작은 첫 구간뿐이다. 뒤 구간은 같은 문단의 연속이다.
            self.add_chunk(text, audio, sample_rate,
                           starts_block=starts_block and i == 0)

    def add_chunk(self, text, audio, sample_rate, starts_block=True):
        """엔진이 청크 전체를 한 덩어리로 준 경우 (Qwen3-TTS).

        청크를 문장으로 나눈 뒤 글자 수에 비례해 시간을 배분하고,
        경계를 실제 무음 지점으로 스냅해 정확도를 보정한다.
        원문 줄바꿈 위치는 큐의 'breaks' 에 오프셋으로 함께 실린다.
        """
        n_samples = int(len(audio))
        start_global = self.cursor
        self.cursor += n_samples

        units = split_units(text, starts_block=starts_block)
        if not units or n_samples <= 0:
            return

        if len(units) == 1:
            self.cues.append({
                'start': start_global,
                'end': start_global + n_samples,
                'text': units[0]['text'],
                'breaks': units[0]['breaks'],
            })
            return

        weights = [max(1, len(_letters(u['text']))) for u in units]
        total_weight = sum(weights)

        # 문장 사이 경계(내부 경계만) 추정 → 무음 스냅
        boundaries = []
        acc = 0
        for w in weights[:-1]:
            acc += w
            boundaries.append(int(n_samples * acc / total_weight))
        boundaries = _snap_to_silence(audio, sample_rate, boundaries)

        edges = [0] + boundaries + [n_samples]
        for i, unit in enumerate(units):
            seg_start, seg_end = edges[i], edges[i + 1]
            if seg_end <= seg_start:
                continue
            self.cues.append({
                'start': start_global + seg_start,
                'end': start_global + seg_end,
                'text': unit['text'],
                'breaks': unit['breaks'],
            })

    # --- 파일 단위 추출 ---

    def cues_for_range(self, start_sample, end_sample, sample_rate):
        """[start_sample, end_sample) 구간의 큐를 초 단위·0초 기준으로 반환.

        10분 경계에 걸친 문장은 양쪽 파일에 잘라서 넣는다 (실제로 들리는
        위치에 자막이 있어야 하므로). 겹침이 아주 짧은 조각은 버린다.
        """
        min_overlap = int(sample_rate * 0.2)
        out = []
        for cue in self.cues:
            s = max(cue['start'], start_sample)
            e = min(cue['end'], end_sample)
            overlap = e - s
            if overlap <= 0:
                continue
            cue_len = cue['end'] - cue['start']
            if overlap < min(min_overlap, max(1, cue_len // 2)):
                continue
            out.append({
                'start': (s - start_sample) / sample_rate,
                'end': (e - start_sample) / sample_rate,
                'text': cue['text'],
                'breaks': cue.get('breaks') or [],
            })
        out.sort(key=lambda c: c['start'])
        return out


def build_srt(cues):
    """큐 목록 → (SRT 텍스트, 레이아웃 사이드카 dict 또는 None).

    SRT 텍스트는 Simon Reader 임포터가 요구하는 형식 그대로다(큐 하나 = 한 줄).
    원문 줄바꿈은 텍스트를 건드리지 않고 사이드카에 오프셋으로만 기록한다.
    사이드카 인덱스는 여기서 실제로 써 나가는 블록 번호와 같아야 하므로
    (버려진 큐가 있으면 어긋난다) 렌더링과 같은 루프에서 만든다.
    """
    lines = []
    breaks = []
    index = 0
    for cue in cues:
        # 자막 텍스트는 반드시 한 줄 (개행·빈 줄이 있으면 블록이 깨진다)
        text = re.sub(r'\s+', ' ', cue.get('text') or '').strip()
        if not text:
            continue

        start_ms = int(round(float(cue['start']) * 1000))
        end_ms = int(round(float(cue['end']) * 1000))
        if start_ms < 0:
            start_ms = 0
        if end_ms <= start_ms:
            end_ms = start_ms + 1  # end > start 인 블록만 임포트된다

        # 0 = 이 큐에서 새 문단이 시작, 그 외 = 큐 안쪽 줄바꿈 위치
        for offset in sorted(set(cue.get('breaks') or [])):
            if 0 <= offset < len(text):
                breaks.append([index, offset])

        index += 1
        lines.append(str(index))
        lines.append(f"{format_srt_time(start_ms)} --> {format_srt_time(end_ms)}")
        lines.append(text)
        lines.append("")

    if index == 0:
        return "", None

    layout = None
    if breaks:
        layout = {'version': 1, 'cue_count': index, 'breaks': breaks}
    return "\n".join(lines), layout


def render_srt(cues):
    """큐 목록 → SRT 텍스트."""
    return build_srt(cues)[0]


def write_srt(audio_path, cues):
    """오디오 파일과 같은 이름의 .srt(+레이아웃 사이드카)를 같은 폴더에 저장.

    Simon Reader는 파일명 stem으로 오디오와 자막을 짝지으므로 확장자만 바꾼다.
    생성된 .srt 경로를 반환하고, 쓸 큐가 없으면 None을 반환한다.
    """
    content, layout = build_srt(cues)
    if not content:
        return None

    srt_path = os.path.splitext(audio_path)[0] + ".srt"
    try:
        # UTF-8 + LF 고정 (Windows에서도 CRLF로 바뀌지 않도록 newline='' 사용)
        with open(srt_path, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
    except OSError as e:
        print(f"[warn] SRT 저장 실패 ({srt_path}): {e}")
        return None

    write_layout(srt_path, layout)
    return srt_path


def write_layout(srt_path, layout):
    """.srt 옆에 레이아웃 사이드카를 저장한다 (없으면 남아 있던 것을 지운다).

    사이드카가 .srt 와 짝이 맞지 않으면(cue_count 불일치) 리더가 통째로 무시하므로,
    옛 파일을 남겨 두는 것보다 지우는 편이 안전하다.
    """
    path = os.path.splitext(srt_path)[0] + LAYOUT_SUFFIX
    if not layout:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    try:
        with open(path, 'w', encoding='utf-8', newline='') as f:
            json.dump(layout, f, ensure_ascii=False, separators=(',', ':'))
        return path
    except OSError as e:
        print(f"[warn] 자막 레이아웃 저장 실패 ({path}): {e}")
        return None
