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

KIND_ORDER = ("character", "anchor", "male", "costume", "stage", "scene")
ALL_KINDS = KIND_ORDER + ("motion", "audio")
MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS = 9, 3, 3

# Bundle の出力ピン。名前は Ref ノードの種類と一致させ、他の参照の ON/OFF で中身が動かない。
# 画像の並びは採番順（character → anchor → male → costume → stage）と同じ。
PIN_SLOTS = {"character": 2, "anchor": 1, "male": 2, "costume": 2, "stage": 2, "scene": 1, "motion": 3, "audio": 3}


def pin_name(kind, idx):
    return kind if PIN_SLOTS[kind] == 1 else f"{kind}_{idx + 1}"


def pin_layout():
    """[(pin名, 'image'|'audio')]。motion は IMAGE（24fps フレーム列）。"""
    out = [(pin_name(k, i), "image") for k in KIND_ORDER for i in range(PIN_SLOTS[k])]
    out += [(pin_name("motion", i), "image") for i in range(PIN_SLOTS["motion"])]
    out += [(pin_name("audio", i), "audio") for i in range(PIN_SLOTS["audio"])]
    return out


# 音声の役割ごとの文言（公式 ref-en.txt 4.2 に準拠）。{a} は <Audio N>。
AUDIO_ROLES = ["reference", "fully_copy", "partially_copy", "weak_reference"]
AUDIO_DEF = {
    "reference": "{a} is the voice-timbre reference for <Subject 1> (S1)",
    "fully_copy": "{a} is the audio track reused as the voice of <Subject 1> (S1)",
    "partially_copy": "{a} is the audio track partly reused for <Subject 1> (S1)",
    "weak_reference": "{a} is a loose voice reference for <Subject 1> (S1)",
}
AUDIO_RET = {
    "fully_copy": "{a} is reused 1:1 as the target video's complete final audio track.",
    "partially_copy": "only part of {a} is copied, and other sounds may be added, removed, or replaced after copying.",
    "reference": "the target speaker follows {a}'s voice timbre and measured delivery without copying the original signal.",
    "weak_reference": "only a broad similarity in the vocal category or atmosphere of {a} is retained.",
}
SCENE_STRENGTHS = ["partially_preserved", "weak_reference"]


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
    sub: dict = field(default_factory=dict)   # scene: {'pose': n, 'scene': n}


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
        r = replace(r, pics=[], vids=[], auds=[], subj=0, sub={})
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
        if r.kind in ("character", "male", "costume", "stage") or (r.kind == "anchor" and not has_char):
            n_subj += 1
            r.subj = n_subj
        elif r.kind == "scene":   # scene は pose / scene の2つの Subject を持てる
            for key, opt in (("pose", "inherit_pose"), ("scene", "inherit_scene")):
                if r.opts.get(opt, True):
                    n_subj += 1
                    r.sub[key] = n_subj
        b.refs.append(r)

    # ---- validation
    cnt = lambda k: len(b.of(k))  # noqa: E731
    if cnt("anchor") > 1:
        b.errors.append("anchor（先頭/末尾フレーム）は1つまで")
    need = {"character": "pics", "anchor": "pics", "costume": "pics", "stage": "pics", "scene": "pics",
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
    dependents = [k for k in ("male", "costume", "scene", "audio") if cnt(k)]
    if dependents and not has_s1:
        b.errors.append(f"{' / '.join(dependents)} には character か anchor（最初の参照画像）が必要")
    elif not has_s1:
        b.warnings.append("character / anchor が無いため <Subject 1> は定義されません")
    if cnt("audio") and not (b.pictures or b.videos):
        b.errors.append("音声には視覚アンカー（画像か動画）が必要")
    if cnt("scene") and cnt("anchor"):
        b.warnings.append("anchor と scene の両方が構図を指定しています")
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
    if t and t[-1] not in ".!?。！？":
        t += "."
    return f" {t}" if t else ""


def _with(word, text):
    """説明文を名詞句として ', with ...' / ', featuring ...' の形で挿入する（空なら空文字）。"""
    t = normalize_tags((text or "").strip()).rstrip(".!?。！？ ")
    return f", {word} {t}" if t else ""


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
    """戻り値: ({'defs','summary','rets'}, warnings)。書式は公式 ref-en ガイドに準拠。"""
    warns = []
    poss = POSSESSIVE.get(subject_type, "their")
    chars, males, costumes = b.of("character"), b.of("male"), b.of("costume")
    stages, scenes = b.of("stage"), b.of("scene")
    motions, audios = b.of("motion"), b.of("audio")
    anchor = (b.of("anchor") or [None])[0]
    final = names[-1]
    all_lab = ", ".join(names)
    has_s1 = any(r.subj == 1 and r.kind in ("character", "anchor") for r in b.refs)
    atype = anchor.opts.get("anchor_type", "first") if anchor else None
    first = atype == "first"
    ap = _tags("Picture", anchor.pics) if anchor else ""
    cpic = _tags("Picture", chars[0].pics[:1]) if chars else ""
    cos_subj = ", ".join(f"<Subject {c.subj}>" for c in costumes)
    vt_all = ", ".join(_tags("Video", v.vids) for v in motions)
    move_line = f"The body movement and action rhythm of <Subject 1> come from {vt_all}." if motions else ""
    defs, rets = [], []

    # ---- character
    for c in chars:
        clothing = c.opts.get("include_clothing", True) and not costumes
        lock = "face, hair, " + ("clothing, " if clothing else "") + "skin tone, and body proportions"
        defs.append(f"<Subject {c.subj}> is the {subject_type} depicted in the character reference images "
                    f"({_tags('Picture', c.pics)}, showing full-body and close-up face views){_with('with', c.desc)}, "
                    f"maintaining the exact {lock} of the reference.")
        defs.append(f"The reference images provide the definitive character design sheet for <Subject {c.subj}>.")
        if c.subj == 1 and move_line:
            defs.append(move_line)
        lab = _lab(names, _shots(c, names, shot_map, warns))
        tail = f" Clothing follows {cos_subj}." if costumes else ""
        rets.append(f"<Subject {c.subj}> (appears in {lab}): {c.retention or 'fully_preserved'} - the exact {lock} "
                    "from the character reference images remain entirely consistent across all active shots; "
                    f"only expressions, poses, and movements change as described.{tail}")

    # ---- anchor
    if anchor:
        if not chars:
            clothing = "" if costumes else "the exact clothing, "
            defs.append(f"<Subject 1> is the {subject_type} in {ap}, with the exact face, the exact hair, "
                        f"{clothing}skin tone, and body proportions of {ap}.{_x(anchor.desc)}")
            if move_line:
                defs.append(move_line)
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

    # ---- male（既定は通常の男性。POV の手だけを出したい場合は pov_hands）
    for m in males:
        pov = m.opts.get("pov_hands", False)
        role = m.role.strip() or ("male POV hands" if pov else "man")
        pt = _tags("Picture", m.pics)
        if m.pics:
            defs.append(f"<Subject {m.subj}> is the {role} in {pt}{_with('with', m.desc)}.")
            default_ret = (f"partially_preserved - the hands from {pt} are retained, interacting naturally with <Subject 1>"
                           if pov else f"fully_preserved - the {role}'s face, hair, clothing, and build from {pt} are "
                                       "retained, interacting naturally with <Subject 1>")
        else:
            intro = "(or introduced from the image)" if first else "(or introduced earlier)"
            defs.append(f"<Subject {m.subj}> is the {role} visible in {ap} {intro}.{_x(m.desc)}")
            default_ret = ("partially_preserved - hands interacting naturally with <Subject 1>" if pov else
                           f"partially_preserved - the {role}'s appearance follows {ap}, interacting naturally with <Subject 1>")
        nums = _shots(m, names, shot_map, warns)
        rets.append(f"<Subject {m.subj}> (appears in {_lab(names, nums)}): "
                    f"{normalize_tags(m.retention.strip()) or default_ret}")
        for n in nums:
            if n - 1 < len(texts) and f"<Subject {m.subj}>" not in texts[n - 1]:
                warns.append(f"{names[n - 1]} の本文に <Subject {m.subj}> が見当たりません")

    # ---- costume（Subject として定義し、最初の参照画像の人物 = <Subject 1> へ attribute_transfer）
    for c in costumes:
        parts = c.opts.get("parts", "whole outfit")
        what = "outfit" if parts == "whole outfit" else parts
        t = _tags("Picture", c.pics)
        defs.append(f"<Subject {c.subj}> is the {what} in {t}{_with('featuring', c.desc)}.")
        rets.append(f"<Subject {c.subj}> (appears in {all_lab}): {c.retention or 'attribute_transfer'} - the {what} "
                    f"is transferred onto <Subject 1>; the wearer's face, hair, and body in {t} are not retained.")

    # ---- stage（環境は Subject）
    for st in stages:
        t = _tags("Picture", st.pics)
        defs.append(f"<Subject {st.subj}> is the environment in {t}{_with('featuring', st.desc)}. "
                    f"Any people in {t} are not part of this subject.")
        lab = _lab(names, _shots(st, names, shot_map, warns))
        rets.append(f"<Subject {st.subj}> (appears in {lab}): {st.retention or 'partially_preserved'} - the "
                    "setting, layout, lighting, and atmosphere are retained.")

    # ---- scene composition（ストーリーボード参照。キーフレームではない）
    for sc in scenes:
        t = _tags("Picture", sc.pics)
        lab = _lab(names, _shots(sc, names, shot_map, warns))
        pose_n, env_n = sc.sub.get("pose"), sc.sub.get("scene")
        place = sc.opts.get("placement", "")
        planning = "viewpoint, camera angle, framing, " + ("subject placement, and pose" if pose_n else "and subject placement")
        strength = sc.retention or "partially_preserved"
        defs.append(f"{t} is a storyboard reference for {lab}, defining its {planning}; the person shown in {t} "
                    f"is replaced by <Subject 1>.{_x(place)}{_x(sc.desc)}")
        if pose_n:
            defs.append(f"<Subject {pose_n}> is the pose and body posture of the person in {t}.")
        if env_n:
            defs.append(f"<Subject {env_n}> is the environment, lighting, and art style of {t}, excluding the person.")
        rets.append(f"{t} (storyboard reference for {lab}): {strength} - only the composition, camera angle, and "
                    "subject placement are followed; it is not a first or last frame, and the video does not need "
                    "to land on it.")
        if pose_n:
            rets.append(f"<Subject {pose_n}> (appears in {lab}): attribute_transfer - the pose and body placement "
                        f"are transferred onto <Subject 1>; the person's identity, face, hair, and clothing from "
                        f"{t} are not retained.")
        if env_n:
            rets.append(f"<Subject {env_n}> (appears in {lab}): {strength} - the setting, lighting, and art style "
                        "are retained; the person is not.")

    # ---- motion video（カメラ・間・アクションの時間構造）
    for v in motions:
        t = _tags("Video", v.vids)
        swap = has_s1 and v.opts.get("replace_appearance", True)
        defs.append(f"{t} is the reference video for the camera movement, cuts, pacing, and action timing of the "
                    f"target video.{_x(v.desc)}"
                    + (f" The face, hair, clothing, and body of any person in {t} are not used." if swap else ""))
        rets.append(f"{t} (camera movement, pacing, and action structure): partially_preserved - the camera "
                    "movement, action timing, and overall rhythm are followed as closely as possible, while "
                    + ("the original person's appearance is completely discarded from the very first frame and "
                       "replaced by <Subject 1>." if swap else "the character appearance is replaced by <Subject 1>."))

    # ---- audio（話者は <Subject 1> (S1)。retention に (Sx) は書かない）
    for a in audios:
        t = _tags("Audio", a.auds)
        role = a.opts.get("audio_role", "reference")
        lip = " The lip movements of <Subject 1> are synchronized to it." if a.opts.get("lipsync", True) else ""
        defs.append(f"{AUDIO_DEF.get(role, AUDIO_DEF['reference']).format(a=t)}.{lip}{_x(a.desc)}")
        scope = f" It applies to {_lab(names, _shots(a, names, shot_map, warns))}." if a.shots else ""
        rets.append(f"{t}: {role} - {AUDIO_RET.get(role, AUDIO_RET['reference']).format(a=t)}{scope}")

    # ---- summary（タスク種別は公式の6種類のみ）
    tasks = []
    if anchor:
        tasks.append("keyframe completion")
    if chars or males or costumes or stages or scenes or motions:
        tasks.append("reference generation")
    roles = {a.opts.get("audio_role", "reference") for a in audios}
    if roles & {"fully_copy", "partially_copy"}:
        tasks.append("audio reuse")
    if roles & {"reference", "weak_reference"}:
        tasks.append("audio reference")
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
                     f"character reference ({cpic})" + (f", except for the clothing, which follows {cos_subj}." if costumes else "."))
    if anchor and chars:
        extra.append(f"The {'initial' if first else 'final'} pose, framing, and composition are locked to {ap}.")
    elif anchor:
        extra.append(f"The appearance, pose, and framing of <Subject 1> at the {'beginning' if first else 'end'} "
                     f"are locked to {ap}.")
    for st in stages:
        extra.append(f"The environment follows <Subject {st.subj}>.")
    for sc in scenes:
        extra.append(f"The composition and camera angle follow {_tags('Picture', sc.pics)}, with <Subject 1> "
                     "replacing the person in it.")
    if motions:
        extra.append(f"The movements, timing, and camera work are guided by {vt_all}.")
    if motions and has_s1 and any(v.opts.get("replace_appearance", True) for v in motions):
        extra.append(f"From the very first frame, <Subject 1> replaces the person in {vt_all}; the original "
                     "person's appearance never appears in the target video.")
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

    shots_content = "\n".join(formatted)
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


def describe_placement(x0, y0, x1, y1, W, H):
    """マスクのバウンディングボックス → 人物の配置を表す英文。"""
    cx, cy = (x0 + x1) / 2 / W, (y0 + y1) / 2 / H
    hz = "left" if cx < 0.4 else "right" if cx > 0.6 else "center"
    vt = "upper" if cy < 0.4 else "lower" if cy > 0.6 else "middle"
    pos = "center" if (hz == "center" and vt == "middle") else (hz if vt == "middle" else vt if hz == "center" else f"{vt} {hz}")
    where = (f"on the {pos} side of the frame" if pos in ("left", "right") else
             f"in the {pos} part of the frame" if pos in ("upper", "lower") else f"in the {pos} of the frame")
    return (f"The person is placed {where}, spanning about {round((y1 - y0) / H * 100)}% "
            f"of its height and {round((x1 - x0) / W * 100)}% of its width.")
