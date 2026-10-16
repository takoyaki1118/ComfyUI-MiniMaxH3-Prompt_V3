"""MiniMax-H3 Prompt Generator 共通ユーティリティ（V4）。"""
import re

SUBJECT_TYPES = ["woman", "man", "girl", "boy", "character", "person"]
POSSESSIVE = {"woman": "her", "girl": "her", "man": "his", "boy": "his"}
_POSSESSIVE = POSSESSIVE  # 後方互換エイリアス

MAX_SHOTS = 4
DEFAULT_DURATION = 3.0
MIN_DURATION = 0.5
MAX_SHOT_DURATION = 15.0
# 合計秒数の警告しきい値（あくまで目安。公式仕様に合わせて変更してください）
RECOMMENDED_MAX_TOTAL_SEC = 15.0

_SHOT_PREFIX_RE = re.compile(
    r"^\s*[\[\(［（【]?\s*shot\s*\d+\s*[\]\)］）】]?\s*[:：\-－]?\s*",
    re.I,
)
# LLM が付けがちな "At 00:03.500," を除去（common 側で自動付与するため二重防止）
_TIMESTAMP_PREFIX_RE = re.compile(r"^\s*At\s+\d{1,2}:\d{2}(?:\.\d+)?\s*,?\s*", re.I)

_D_TAG_RE = re.compile(r"<d>(.*?)</d>", re.S | re.I)
_SAYS_PREFIX_RE = re.compile(r"^\s*<[^<>]*>\s*(?:\(S\d+\)\s*)?says\s*,?\s*", re.I)

_TAG_LABELS = [
    ("Subject", ["被写体", "サブジェクト"]),
    ("Video", ["参照動画"]),
    ("Picture", ["画像"]),
]

_ASCII_BEFORE = r"(?<![<A-Za-z0-9_])"
_ASCII_AFTER = r"(?![A-Za-z0-9_>])"


# ---------------------------------------------------------------- tags / text
def normalize_tags(text):
    if not text:
        return ""
    for label, jp_words in _TAG_LABELS:
        alts = "|".join([label] + jp_words)
        text = re.sub(
            rf"<\s*(?:{alts})\s*(\d+)\s*>",
            lambda m, l=label: f"<{l} {m.group(1)}>",
            text, flags=re.I,
        )
        text = re.sub(
            rf"{_ASCII_BEFORE}{label}\s*(\d+){_ASCII_AFTER}",
            lambda m, l=label: f"<{l} {m.group(1)}>",
            text,
        )
        if label != "Picture":
            for jp in jp_words:
                text = re.sub(
                    rf"{jp}\s*(\d+)",
                    lambda m, l=label: f"<{l} {m.group(1)}>",
                    text,
                )
    return text


def strip_shot_prefix(text):
    """先頭の [Shot N] と、続く "At mm:ss.xxx," を除去する。"""
    text = _SHOT_PREFIX_RE.sub("", text or "", count=1)
    return _TIMESTAMP_PREFIX_RE.sub("", text, count=1)


def normalize_and_strip_shot(text):
    return normalize_tags(strip_shot_prefix((text or "").strip())).strip()


def clean_dialogue_text(text):
    """セリフを素の日本語テキストに正規化する。

    - [Shot N] 接頭辞を除去
    - LLM が完成形 (<Subject 1> (S1) says, <d>[Japanese] ...</d>) を返しても中身だけ抽出
    - [Japanese] マーカーと < > を除去（タグ崩れ防止）
    """
    text = strip_shot_prefix((text or "").strip()).strip()
    if not text:
        return ""
    m = _D_TAG_RE.search(text)
    if m:
        text = m.group(1)
    else:
        text = _SAYS_PREFIX_RE.sub("", text, count=1)
    text = re.sub(r"^\s*\[\s*Japanese\s*\]\s*", "", text, flags=re.I)
    text = re.sub(r"[<>]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- shot inputs
def build_shot_inputs(n=MAX_SHOTS):
    """INPUT_TYPES['required'] に update する Shot 入力ブロックを生成。"""
    d = {}
    for i in range(1, n + 1):
        d[f"shot{i}_enable"] = ("BOOLEAN", {"default": i == 1})
        d[f"shot{i}_text"] = ("STRING", {"multiline": True, "default": "", "placeholder": f"【Shot {i}】本文"})
        d[f"shot{i}_duration"] = ("FLOAT", {
            "default": DEFAULT_DURATION, "min": MIN_DURATION, "max": MAX_SHOT_DURATION, "step": 0.5})
        d[f"shot{i}_dialogue"] = ("STRING", {"multiline": False, "default": "", "placeholder": f"【Shot {i}】セリフ"})
    return d


def gather_shot_kwargs(kw, n=MAX_SHOTS):
    rng = range(1, n + 1)
    shots = [(kw.get(f"shot{i}_enable", False), kw.get(f"shot{i}_text", "")) for i in rng]
    durs = [kw.get(f"shot{i}_duration", DEFAULT_DURATION) for i in rng]
    dlgs = [(kw.get(f"shot{i}_enable", False), kw.get(f"shot{i}_dialogue", "")) for i in rng]
    return shots, durs, dlgs


def _collect(shot_inputs, duration_inputs, dialogue_inputs=None, append_suffix=None,
             skip_if_contains=None, auto_speech_cue=True):
    warnings = []
    bodies, dialogues, durations = [], [], []

    for i, (enabled, text) in enumerate(shot_inputs):
        if not enabled:
            continue
        body = normalize_and_strip_shot(text)
        d_text = ""
        if dialogue_inputs and i < len(dialogue_inputs):
            d_text = clean_dialogue_text(dialogue_inputs[i][1])
        if not body:
            if d_text:
                warnings.append(f"shot{i + 1}: 本文が空のためセリフも無視されました")
            continue
        if d_text and auto_speech_cue and "<Subject 1>" in body and "speaks" not in body:
            body = f"{body} <Subject 1> (S1) speaks a line."
        bodies.append(body)
        dialogues.append(d_text)
        try:
            dur = float(duration_inputs[i]) if i < len(duration_inputs) else DEFAULT_DURATION
        except (TypeError, ValueError):
            dur = DEFAULT_DURATION
        durations.append(max(MIN_DURATION, dur))

    formatted, names, formatted_dialogues = [], [], []
    cumulative = 0.0
    for i, (body, d_text, dur) in enumerate(zip(bodies, dialogues, durations), 1):
        prefix = f"[Shot {i}]"
        if i > 1:
            mins = int(cumulative // 60)
            secs = cumulative % 60
            prefix = f"[Shot {i}] At {mins:02d}:{secs:06.3f},"
        cumulative += dur

        if append_suffix and not (skip_if_contains and skip_if_contains in body):
            body = f"{body} {append_suffix}"

        names.append(f"[Shot {i}]")
        formatted.append(f"{prefix} {body}".strip())
        if d_text:
            formatted_dialogues.append(f"[Shot {i}] <Subject 1> (S1) says, <d>[Japanese] {d_text}</d>")
        else:
            formatted_dialogues.append(f"[Shot {i}]")

    if cumulative > RECOMMENDED_MAX_TOTAL_SEC:
        warnings.append(f"合計 {cumulative:.1f} 秒（目安 {RECOMMENDED_MAX_TOTAL_SEC:.0f} 秒を超過）")

    return formatted, names, formatted_dialogues, cumulative, warnings


def collect_from_kwargs(kw, auto_speech_cue=True, append_suffix=None, skip_if_contains=None):
    """ノード側から使う入口。(formatted, names, formatted_dialogues, total_sec, warnings)"""
    shots, durs, dlgs = gather_shot_kwargs(kw)
    return _collect(shots, durs, dlgs, append_suffix=append_suffix,
                    skip_if_contains=skip_if_contains, auto_speech_cue=auto_speech_cue)


def collect_shots_with_duration(shot_inputs, duration_inputs, dialogue_inputs=None,
                                append_suffix=None, skip_if_contains=None, auto_speech_cue=True):
    """後方互換ラッパー（3値を返す）。"""
    f, n, d, _, _ = _collect(shot_inputs, duration_inputs, dialogue_inputs,
                             append_suffix, skip_if_contains, auto_speech_cue)
    return f, n, d


def dialogue_lines_only(formatted_dialogues):
    return "\n".join(d for d in formatted_dialogues if "<d>" in d)


def build_status(total_sec, warnings):
    base = f"total {total_sec:.1f}s"
    return f"OK ({base})" if not warnings else f"WARN ({base}): " + " / ".join(warnings)


# ---------------------------------------------------------------- misc
def resolve_music(enable_music, music_text):
    text = (music_text or "").strip()
    return text if (enable_music and text) else "N/A"


def clean_prompt(prompt):
    return re.sub(r"\n{3,}", "\n\n", prompt).strip()
