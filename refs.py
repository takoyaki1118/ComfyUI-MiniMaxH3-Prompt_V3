"""参照スロット → プロンプト コンパイラ（ComfyUI 非依存・単体テスト可能）。

流れ:
  各 Ref ノードが Ref を出力 → bundle_refs() で番号採番・検証 → generate_from_bundle() でプロンプト生成

採番ルール（kind の優先順。接続順には依存しない）:
  Picture : character → anchor → male → costume → stage
  Video   : motion
  Audio   : audio
  Subject : character（複数可）→ anchor（character が無い場合のみ）→ male
  ⇒ 「最初の参照画像 = <Picture 1> = <Subject 1>」が常に成立する。
"""
from dataclasses import dataclass, field, replace

try:
    from .common import (MAX_SHOTS, POSSESSIVE, collect_from_kwargs, dialogue_lines_only, resolve_music,
                         clean_prompt, normalize_tags, normalize_and_strip_shot)
except ImportError:
    from common import (MAX_SHOTS, POSSESSIVE, collect_from_kwargs, dialogue_lines_only, resolve_music,
                        clean_prompt, normalize_tags, normalize_and_strip_shot)

KIND_ORDER = ("character", "anchor", "male", "costume", "stage")
ALL_KINDS = KIND_ORDER + ("motion", "audio")
MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS = 9, 3, 3

# Bundle の出力ピン。名前は Ref ノードの種類と一致させ、他の参照の ON/OFF で中身が動かない。
# 画像の並びは採番順（character → anchor → male → costume → stage）と同じ。
PIN_SLOTS = {"character": 2, "anchor": 1, "male": 2, "costume": 2, "stage": 2, "motion": 3, "audio": 3}


def pin_name(kind, idx):
    return kind if PIN_SLOTS[kind] == 1 else f"{kind}_{idx + 1}"


def pin_layout():
    """[(pin名, 'image'|'audio')]。motion は IMAGE（24fps フレーム列）。"""
    out = [(pin_name(k, i), "image") for k in KIND_ORDER for i in range(PIN_SLOTS[k])]
    out += [(pin_name("motion", i), "image") for i in range(PIN_SLOTS["motion"])]
    out += [(pin_name("audio", i), "audio") for i in range(PIN_SLOTS["audio"])]
    return out


DEFAULT_MALE_ROLE = "male POV hands"
DEFAULT_MALE_RETENTION = "partially_preserved - hands interacting naturally with <Subject 1>"

# 音声の役割ごとの文言。公式 ref-en.txt の書式に合わせて必要なら編集してください。
AUDIO_ROLES = ["reference", "fully_copy", "partially_copy", "weak_reference"]
AUDIO_ROLE_TEXT = {
    "fully_copy": "the voice, speech content, and timing of {a} are reproduced as is",
    "partially_copy": "the main voice and speech content of {a} are reproduced, with minor adaptation to the scene",
    "reference": "only the vocal timbre, rhythm, and speaking style of {a} are referenced",
    "weak_reference": "the vocal character of {a} is loosely referenced",
}


@dataclass
class Ref:
    kind: str
    images: list = field(default_factory=list)   # IMAGE テンソル [1,H,W,C] のリスト
    videos: list = field(default_factory=list)
    audios: list = field(default_factory=list)
    name: str = ""
    enabled: bool = True    # False の Ref は Bundle に接続されていても無視される
    desc: str = ""          # 補足説明（プロンプトに入る）
    retention: str = ""     # 保持設定（空なら kind 既定）
    role: str = ""          # male の役割
    shots: list = field(default_factory=list)    # 対象 Shot（元スロット番号 1..4）。空 = 全 Shot
    opts: dict = field(default_factory=dict)     # anchor_type / parts / audio_role / lipsync ...
    # --- bundle_refs() が設定 ---
    pics: list = field(default_factory=list)
    vids: list = field(default_factory=list)
    auds: list = field(default_factory=list)
    subj: int = 0


@dataclass
class Bundle:
    refs: list = field(default_factory=list)
    pictures: list = field(default_factory=list)
    videos: list = field(default_factory=list)
    audios: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    pin_media: dict = field(default_factory=dict)   # pin名 -> メディア
    pin_label: dict = field(default_factory=dict)   # pin名 -> "<Picture N>" など
    counts: dict = field(default_factory=dict)

    def of(self, kind):
        return [r for r in self.refs if r.kind == kind]


def _flatten(refs):
    out = []
    for r in refs or []:
        if r is None:
            continue
        out.extend(_flatten(r) if isinstance(r, (list, tuple)) else [r])
    return out


# ------------------------------------------------------------------ numbering
def _pin(b, kind, label, num, media):
    idx = b.counts.get(kind, 0)
    b.counts[kind] = idx + 1
    if idx >= PIN_SLOTS[kind]:
        b.errors.append(f"{kind}: 出力ピン数（{PIN_SLOTS[kind]}）を超えています")
        return
    name = pin_name(kind, idx)
    b.pin_media[name] = media
    b.pin_label[name] = f"<{label} {num}>"


def bundle_refs(refs):
    flat = [r for r in _flatten(refs) if getattr(r, "enabled", True)]
    b = Bundle()
    for r in flat:
        if r.kind not in ALL_KINDS:
            b.errors.append(f"unknown kind: {r.kind}")
    ordered = [r for k in ALL_KINDS for r in flat if r.kind == k]
    if not ordered:
        b.errors.append("参照が1つも接続されていません")

    has_char = any(r.kind == "character" for r in ordered)
    n_subj = 0
    for r in ordered:
        r = replace(r, pics=[], vids=[], auds=[], subj=0)
        for im in r.images:
            b.pictures.append(im)
            r.pics.append(len(b.pictures))
            _pin(b, r.kind, "Picture", len(b.pictures), im)
        for v in r.videos:
            b.videos.append(v)
            r.vids.append(len(b.videos))
            _pin(b, r.kind, "Video", len(b.videos), v)
        for a in r.audios:
            b.audios.append(a)
            r.auds.append(len(b.audios))
            _pin(b, r.kind, "Audio", len(b.audios), a)
        if r.kind in ("character", "male") or (r.kind == "anchor" and not has_char):
            n_subj += 1
            r.subj = n_subj
        b.refs.append(r)

    # ---- validation
    cnt = lambda k: len(b.of(k))  # noqa: E731
    if cnt("anchor") > 1:
        b.errors.append("anchor（先頭/末尾フレーム）は1つまで")
    need = {"character": "pics", "anchor": "pics", "costume": "pics", "stage": "pics",
            "motion": "vids", "audio": "auds"}
    for r in b.refs:
        if r.kind in need and not getattr(r, need[r.kind]):
            b.errors.append(f"{r.kind}: 参照メディアがありません（画像/動画/音声が未接続）")
    if len(b.pictures) > MAX_IMAGES:
        b.errors.append(f"画像が上限超過 ({len(b.pictures)}/{MAX_IMAGES})")
    if len(b.videos) > MAX_VIDEOS:
        b.errors.append(f"動画が上限超過 ({len(b.videos)}/{MAX_VIDEOS})")
    if len(b.audios) > MAX_AUDIOS:
        b.errors.append(f"音声が上限超過 ({len(b.audios)}/{MAX_AUDIOS})")

    has_s1 = any(r.subj == 1 and r.kind in ("character", "anchor") for r in b.refs)
    dependents = [k for k in ("male", "costume", "audio") if cnt(k)]
    if dependents and not has_s1:
        b.errors.append(f"{' / '.join(dependents)} には character か anchor（最初の参照画像）が必要")
    elif not has_s1:
        b.warnings.append("character / anchor が無いため <Subject 1> は定義されません")
    if cnt("audio") and not (b.pictures or b.videos):
        b.errors.append("音声には視覚アンカー（画像か動画）が必要")
    if cnt("costume") and cnt("anchor"):
        b.warnings.append("anchor 画像の服装と costume が競合する可能性")
    return b


def describe(b):
    """例: 'character_1→<Picture 1>, male_1→<Picture 2>, motion_1→<Video 1>'"""
    return ", ".join(f"{p}→{l}" for p, l in b.pin_label.items()) or "no refs"


# ------------------------------------------------------------------ rendering
def _tags(prefix, nums):
    return ", ".join(f"<{prefix} {n}>" for n in nums)


def _x(text):
    t = normalize_tags((text or "").strip())
    return f" {t}" if t else ""


def _shots(r, names, shot_map, warns):
    allnums = list(range(1, len(names) + 1))
    if not r.shots:
        return allnums
    nums = sorted({shot_map[i] for i in r.shots if i in shot_map})
    if not nums:
        warns.append(f"{r.kind}: 指定Shotが全て無効のため全Shot扱い")
        return allnums
    return nums


def _lab(names, nums):
    return ", ".join(names[n - 1] for n in nums if 0 < n <= len(names))


def render_sections(b, names, shot_map, subject_type, action, texts):
    """戻り値: ({'defs','summary','rets'}, warnings)"""
    warns = []
    poss = POSSESSIVE.get(subject_type, "their")
    chars, males, costumes = b.of("character"), b.of("male"), b.of("costume")
    stages, motions, audios = b.of("stage"), b.of("motion"), b.of("audio")
    anchor = (b.of("anchor") or [None])[0]
    final = names[-1]
    all_lab = ", ".join(names)
    has_s1 = any(r.subj == 1 for r in b.refs)
    atype = anchor.opts.get("anchor_type", "first") if anchor else None
    first = atype == "first"
    ap = _tags("Picture", anchor.pics) if anchor else ""
    cpic = _tags("Picture", chars[0].pics[:1]) if chars else ""
    cos_tags = ", ".join(_tags("Picture", c.pics) for c in costumes)
    defs, rets = [], []

    # ---- character
    for c in chars:
        clothing = c.opts.get("include_clothing", True) and not costumes
        lock = "face, hair, " + ("clothing, " if clothing else "") + "skin tone, and body proportions"
        defs.append(f"<Subject {c.subj}> is the {subject_type} depicted in the character reference images "
                    f"({_tags('Picture', c.pics)}, showing full-body and close-up face views), maintaining the "
                    f"exact {lock} of the reference.{_x(c.desc)}")
        defs.append(f"The reference images provide the definitive character design sheet for <Subject {c.subj}>.")
        lab = _lab(names, _shots(c, names, shot_map, warns))
        tail = f" Clothing follows {cos_tags}." if costumes else ""
        rets.append(f"<Subject {c.subj}> (appears in {lab}): {c.retention or 'fully_preserved'} - the exact {lock} "
                    "from the character reference images remain entirely consistent across all active shots; "
                    f"only expressions, poses, and movements change as described.{tail}")

    # ---- anchor
    if anchor:
        if not chars:
            clothing = "" if costumes else "the exact clothing, "
            defs.append(f"<Subject 1> is the {subject_type} in {ap}, with the exact face, the exact hair, "
                        f"{clothing}skin tone, and body proportions of {ap}.{_x(anchor.desc)}")
        who = f"<Subject 1> (the same person as defined in {cpic})" if chars else "<Subject 1>"
        if first:
            defs.append(f"{ap} is the first frame of [Shot 1], showing {who} in the exact pose, framing, and "
                        f"composition of the {'starting ' if chars else ''}reference image.")
        else:
            defs.append(f"{ap} is the last frame of {final}, showing {who} in the exact final pose, framing, "
                        f"composition, and lighting of the {'ending ' if chars else ''}reference image.")
        if not chars:
            tail = ("only expression, pose, and movement change as described." if first else
                    "only expression, pose, and movement change as described until the final frame.")
            rets.append(f"<Subject 1> (appears in {all_lab}): {anchor.retention or 'fully_preserved'} - {poss} "
                        f"face, hair, {'' if costumes else 'clothing, '}skin tone, and body from {ap} remain "
                        f"constant throughout; {tail}")
        what = ("composition, pose, framing, and lighting" if chars else
                "composition, pose, framing, lighting, and subject appearance")
        excl = " (excluding subject identity mapping)" if chars else ""
        where = "[Shot 1] first frame" if first else f"{final} last frame"
        rets.append(f"{ap} ({where}): fully_preserved - the target video {'opens on' if first else 'ends on'} "
                    f"the exact {what} of {ap}{excl}.")

    # ---- male
    for m in males:
        role = m.role.strip() or DEFAULT_MALE_ROLE
        if m.pics:
            defs.append(f"<Subject {m.subj}> is the {role} in {_tags('Picture', m.pics)}.{_x(m.desc)}")
            default_ret = (f"partially_preserved - the appearance of <Subject {m.subj}> from "
                           f"{_tags('Picture', m.pics)} is retained, interacting naturally with <Subject 1>")
        elif anchor:
            intro = "(or introduced from the image)" if first else "(or introduced earlier)"
            defs.append(f"<Subject {m.subj}> is the {role} visible in {ap} {intro}.{_x(m.desc)}")
            default_ret = DEFAULT_MALE_RETENTION
        else:
            defs.append(f"<Subject {m.subj}> is the {role}.{_x(m.desc)}")
            default_ret = DEFAULT_MALE_RETENTION
        nums = _shots(m, names, shot_map, warns)
        rets.append(f"<Subject {m.subj}> (appears in {_lab(names, nums)}): "
                    f"{normalize_tags(m.retention.strip()) or default_ret}")
        for n in nums:
            if n - 1 < len(texts) and f"<Subject {m.subj}>" not in texts[n - 1]:
                warns.append(f"{names[n - 1]} の本文に <Subject {m.subj}> が見当たりません")

    # ---- costume（対象は常に最初の参照画像 = <Picture 1> / <Subject 1>）
    for c in costumes:
        parts = c.opts.get("parts", "whole outfit")
        what = "outfit" if parts == "whole outfit" else parts
        t = _tags("Picture", c.pics)
        defs.append(f"<Subject 1> wears the {what} shown in {t}, replacing the clothing of <Picture 1>; the face, "
                    f"hair, skin tone, and body proportions remain those of <Picture 1>.{_x(c.desc)}")
        rets.append(f"{t} (clothing of <Subject 1>): {c.retention or 'attribute_transfer'} - clothing only "
                    f"({what}); face, hair, and body are not taken from it.")

    # ---- stage
    for s in stages:
        t = _tags("Picture", s.pics)
        defs.append(f"{t} is the stage reference: use only the environment, lighting, layout, and atmosphere "
                    f"of {t}; ignore any people in it.{_x(s.desc)}")
        lab = _lab(names, _shots(s, names, shot_map, warns))
        rets.append(f"{t} (environment, used in {lab}): {s.retention or 'partially_preserved'} - the setting, "
                    "layout, lighting, and atmosphere are retained; no subject identity is taken from it.")

    # ---- motion video
    for v in motions:
        t = _tags("Video", v.vids)
        swap = has_s1 and v.opts.get("replace_appearance", True)
        defs.append(f"{t} is the motion reference video that provides the body movement, timing, camera "
                    f"movement, and action rhythm to be followed by <Subject 1>.{_x(v.desc)}"
                    + (f" The face, hair, clothing, and body of any person in {t} are not used." if swap else ""))
        rets.append(f"{t} (motion, timing, and camera structure): partially_preserved - the body movements, "
                    "action timing, and overall rhythm are followed as closely as possible, while "
                    + ("the original person's appearance is completely discarded from the very first frame and "
                       "replaced by <Subject 1>." if swap else "the character appearance is replaced by <Subject 1>."))

    # ---- audio（話者は常に <Subject 1>）
    for a in audios:
        t = _tags("Audio", a.auds)
        role = a.opts.get("audio_role", "reference")
        text = AUDIO_ROLE_TEXT.get(role, AUDIO_ROLE_TEXT["reference"]).format(a=t)
        lip = "; the lip movements of <Subject 1> are synchronized to it" if a.opts.get("lipsync", True) else ""
        defs.append(f"{t} is the voice of <Subject 1>.{_x(a.desc)}")
        lab = _lab(names, _shots(a, names, shot_map, warns))
        rets.append(f"{t} (voice of <Subject 1>, used in {lab}): {role} - {text}{lip}.")

    # ---- summary
    tasks = []
    if chars:
        tasks.append("character consistency generation")
    if anchor:
        tasks.append("keyframe completion")
    if males or costumes or stages or motions or audios:
        tasks.append("reference generation")
    tag = f"[{' + '.join(tasks)}]" if tasks else ""
    if has_s1:
        a_str = f" of <Subject 1> {action}" if action else " of <Subject 1>"
    else:
        a_str = f" {action}" if action else ""
    if anchor and first:
        body = f"The target video starts from the exact first frame of {ap} and continues forward as a continuous video{a_str}."
    elif anchor:
        body = ("The target video begins from a plausible preceding state and develops as a continuous video"
                f"{a_str}, gradually converging so that the final moment of {final} lands exactly on {ap}.")
    else:
        body = f"The target video generates a sequence{a_str}" + (
            " based on the character reference images." if chars else ".")
    extra = []
    if chars:
        extra.append("The appearance, facial features, and styling of <Subject 1> are strictly locked to the "
                     f"character reference ({cpic})" + (f", except for the clothing, which follows {cos_tags}." if costumes else "."))
    if anchor and chars:
        extra.append(f"The {'initial' if first else 'final'} pose, framing, and composition are locked to {ap}.")
    elif anchor:
        extra.append(f"The appearance, pose, and framing of <Subject 1> at the {'beginning' if first else 'end'} "
                     f"are locked to {ap}.")
    if stages:
        extra.append(f"The environment, lighting, and layout follow {', '.join(_tags('Picture', s.pics) for s in stages)}.")
    if motions:
        extra.append(f"The movements, timing, and camera work are guided by {', '.join(_tags('Video', v.vids) for v in motions)}.")
    if motions and has_s1 and any(v.opts.get("replace_appearance", True) for v in motions):
        extra.append("From the very first frame, <Subject 1> replaces the person in "
                     f"{', '.join(_tags('Video', v.vids) for v in motions)}; the original person's appearance "
                     "never appears in the target video.")
    if audios:
        extra.append(f"The voice of <Subject 1> follows {', '.join(_tags('Audio', a.auds) for a in audios)}.")
    summary = " ".join([tag, body] + extra).strip()
    return {"defs": "\n".join(defs), "summary": summary, "rets": "\n".join(rets)}, warns


# ------------------------------------------------------------------ entry
def _shot_map(kw):
    """元スロット番号 → 実際に出力される Shot 番号（無効Shotは詰められるため）。"""
    m, n = {}, 0
    for i in range(1, MAX_SHOTS + 1):
        if kw.get(f"shot{i}_enable") and normalize_and_strip_shot(kw.get(f"shot{i}_text", "")):
            n += 1
            m[i] = n
    return m


def generate_from_bundle(b, summary_action, camera_style, soundscape, enable_music, music_text,
                         shot_kwargs, auto_speech_cue=True, subject_type="woman"):
    """戻り値: (prompt, dialogue, status)。ERROR がある場合は ValueError。"""
    if b.errors:
        raise ValueError("H3 refs ERROR: " + " / ".join(b.errors))
    anchor = (b.of("anchor") or [None])[0]
    motions = b.of("motion")

    suffix = skip = None
    if motions and motions[0].opts.get("auto_append_video_ref", True):
        skip = _tags("Video", motions[0].vids[:1])
        if motions[0].opts.get("replace_appearance", True):
            suffix = (f"(following only the motion and timing of {skip}; the appearance is that of "
                      "<Subject 1> from the first frame)")
        else:
            suffix = f"(following the motion and timing of {skip})"
    formatted, names, fdlg, total, warns = collect_from_kwargs(
        shot_kwargs, auto_speech_cue, append_suffix=suffix, skip_if_contains=skip)
    dialogue = dialogue_lines_only(fdlg)
    shot_map = _shot_map(shot_kwargs)

    ap = _tags("Picture", anchor.pics) if anchor else ""
    atype = anchor.opts.get("anchor_type", "first") if anchor else None
    if not formatted:
        if atype == "first":
            fb = f"[Shot 1] The shot begins from {ap}."
        elif atype == "last":
            fb = "[Shot 1] The scene develops toward the final composition."
        else:
            fb = "[Shot 1] <Subject 1> appears in the scene."
            if motions:
                fb = "[Shot 1] <Subject 1> appears in the scene from the first frame."
                fb += f" The movement follows {_tags('Video', motions[0].vids[:1])}."
        formatted, names = [fb], ["[Shot 1]"]
        shot_map = {i: 1 for i in range(1, MAX_SHOTS + 1)}
        warns.append("有効なShotがないためフォールバック文を使用")
    if atype == "last" and ap not in formatted[-1]:
        formatted[-1] += f" The shot ends exactly on the composition, pose, framing, and lighting of {ap}."

    action = normalize_tags((summary_action or "").strip())
    sec, rw = render_sections(b, names, shot_map, subject_type, action, formatted)
    warns += rw + b.warnings

    shots_content = "\n\n".join(formatted)
    camera = (camera_style or "").strip()
    camera_line = f"The target video is filmed in {camera}.\n" if camera else ""
    prompt = f"""subject_definitions:
{sec['defs']}

summary: {sec['summary']}

retention_analysis:
{sec['rets']}

detailed_description:
{camera_line}{shots_content}

overall_soundscape: {(soundscape or '').strip() or 'N/A'}
non_diegetic_music: {resolve_music(enable_music, music_text)}"""

    head = f"[refs] {describe(b)} | total {total:.1f}s"
    status = f"OK {head}" if not warns else f"WARN {head} | " + " / ".join(warns)
    return clean_prompt(prompt), dialogue, status


# ------------------------------------------------------------------ size helper
SIZE_STEP = 32


def fit_size(w, h, megapixels, step=SIZE_STEP):
    """アスペクト比を保ち、面積が megapixels に近い step の倍数サイズを返す。0 以下ならリサイズなし。"""
    if megapixels <= 0 or w <= 0 or h <= 0:
        return w, h
    scale = (megapixels * 1_000_000 / (w * h)) ** 0.5
    snap = lambda v: max(step, int(round(v * scale / step)) * step)  # noqa: E731
    return snap(w), snap(h)
