"""モード仕様とプロンプト組み立てのコア（ComfyUI 非依存・単体テスト可能）。"""
try:
    from .common import (
        collect_from_kwargs, dialogue_lines_only, resolve_music, clean_prompt,
        normalize_tags, POSSESSIVE,
    )
except ImportError:
    from common import (
        collect_from_kwargs, dialogue_lines_only, resolve_music, clean_prompt,
        normalize_tags, POSSESSIVE,
    )

# char_ref : キャラクターシート有無 (有りなら <Picture 1>)
# anchor   : 'first' / 'last' / None（アンカー画像。char_ref 有りなら <Picture 2>、無しなら <Picture 1>）
# motion   : 参照動画 <Video 1> の有無
MODE_SPECS = {
    "first_frame":          dict(char_ref=False, anchor="first", motion=False),
    "last_frame":           dict(char_ref=False, anchor="last",  motion=False),
    "char_ref":             dict(char_ref=True,  anchor=None,    motion=False),
    "char_ref_motion":      dict(char_ref=True,  anchor=None,    motion=True),
    "char_ref_first_frame": dict(char_ref=True,  anchor="first", motion=False),
    "char_ref_last_frame":  dict(char_ref=True,  anchor="last",  motion=False),
}
MODES = list(MODE_SPECS.keys())

VIDEO_REF_SUFFIX = "(following the motion and timing of <Video 1>)"


def refs_summary(mode):
    """そのモードで接続すべき参照メディアの説明（status 表示用）。"""
    s = MODE_SPECS[mode]
    parts = []
    n = 1
    if s["char_ref"]:
        parts.append(f"<Picture {n}>=character sheet"); n += 1
    if s["anchor"]:
        parts.append(f"<Picture {n}>={s['anchor']} frame")
    if s["motion"]:
        parts.append("<Video 1>=motion video")
    return ", ".join(parts)


def _landing(pic):
    return f"The shot ends exactly on the composition, pose, framing, and lighting of <{pic}>."


# ------------------------------------------------------------------ builders
def _build_frame_only(anchor, c, opts):
    """first_frame / last_frame（キャラシートなし）。アンカーは <Picture 1>。"""
    names, shots_content = c["names"], c["shots_content"]
    shot_list = ", ".join(names)
    final = names[-1]
    poss = POSSESSIVE.get(c["subject_type"], "their")
    extra = normalize_tags((opts.get("subject1_extra") or "").strip())
    extra_s = f" {extra}" if extra else ""

    if opts.get("has_male"):
        role = (opts.get("male_role") or "").strip() or "male POV hands visible in frame"
        retention = (opts.get("male_retention_desc") or "").strip() or \
            "partially_preserved - hands interacting naturally with <Subject 1>"
        where = "(or introduced from the image)" if anchor == "first" else "(or introduced earlier)"
        male_def = f"\n<Subject 2> is the {role} visible in <Picture 1> {where}."
        male_ret = f"\n<Subject 2> (appears in {shot_list}): {retention}"
    else:
        male_def = male_ret = ""

    a, camera_line = c["action_str"], c["camera_line"]
    if anchor == "first":
        pic_def = ("<Picture 1> is the first frame of [Shot 1], showing <Subject 1> in the exact pose, "
                   "framing, and composition of the reference image.")
        summary = ("[keyframe completion + reference generation] The target video starts from the exact first "
                   f"frame of <Picture 1> and continues forward as a continuous video{a}. The appearance, pose, "
                   "and framing of <Subject 1> at the beginning are locked to <Picture 1>.")
        tail = "only expression, pose, and movement change as described."
        pic_ret = ("<Picture 1> ([Shot 1] first frame): fully_preserved - the target video opens on the exact "
                   "composition, pose, framing, lighting, and subject appearance of <Picture 1>.")
    else:
        pic_def = (f"<Picture 1> is the last frame of {final}, showing <Subject 1> in the exact final pose, "
                   "framing, composition, and lighting of the reference image.")
        summary = ("[keyframe completion] The target video begins from a plausible preceding state and develops "
                   f"as a continuous video{a}, gradually converging so that the final moment of {final} lands exactly on "
                   "<Picture 1>. The appearance, pose, and framing of <Subject 1> at the end are locked to <Picture 1>.")
        tail = "only expression, pose, and movement change as described until the final frame."
        pic_ret = (f"<Picture 1> ({final} last frame): fully_preserved - the target video ends on the exact "
                   "composition, pose, framing, lighting, and subject appearance of <Picture 1>.")

    return f"""subject_definitions:
<Subject 1> is the {c['subject_type']} in <Picture 1>, with the exact face, the exact hair, the exact clothing, skin tone, and body proportions of <Picture 1>.{extra_s}
{pic_def}{male_def}

summary: {summary}

retention_analysis:
<Subject 1> (appears in {shot_list}): fully_preserved - {poss} face, hair, clothing, skin tone, and body from <Picture 1> remain constant throughout; {tail}
{pic_ret}{male_ret}

detailed_description:
{camera_line}{shots_content}

overall_soundscape: {c['sound']}
non_diegetic_music: {c['music']}"""


def _build_char_ref(motion, c, opts):
    """char_ref / char_ref_motion。"""
    shot_list = ", ".join(c["names"])
    clothing = ", clothing" if opts.get("include_clothing", True) else ""
    extra = normalize_tags((opts.get("character_extra") or "").strip())
    extra_s = f" {extra}" if extra else ""
    a = c["action_str"]

    if motion:
        desc = normalize_tags((opts.get("motion_video_desc") or "").strip())
        desc_s = f" {desc}" if desc else ""
        video_def = (
            "\n<Video 1> is the motion reference video that provides the body movement, "
            "timing, camera movement, and action rhythm to be followed by <Subject 1>." + desc_s
        )
        task = "[character consistency generation + reference generation]"
        summary_body = (f"The target video features <Subject 1> performing the actions{a} based on the character "
                        f"reference images, strictly locking the appearance, face, hair{clothing}, and body to the "
                        "reference, while the movements, timing, and camera work are guided by <Video 1>.")
        ret_video = (
            "\n<Video 1> (motion, timing, and camera structure): partially_preserved - the body movements, "
            "action timing, and overall rhythm of <Video 1> are followed as closely as possible, while the "
            "character appearance is replaced by <Subject 1>."
        )
    else:
        video_def = ret_video = ""
        task = "[character consistency generation]"
        summary_body = (f"The target video generates a sequence featuring <Subject 1> based on the character "
                        f"reference images{a}. The appearance, facial features, and styling of <Subject 1> are "
                        "strictly locked to the character reference.")

    return f"""subject_definitions:
<Subject 1> is the {c['subject_type']} depicted in the character reference images (<Picture 1>, showing full-body and close-up face views), maintaining the exact face, hair{clothing}, skin tone, and body proportions of the reference.{extra_s}
The reference images provide the definitive character design sheet for <Subject 1>.{video_def}

summary: {task} {summary_body}

retention_analysis:
<Subject 1> (appears in {shot_list}): fully_preserved - the exact face, hair{clothing}, and body proportions from the character reference images remain entirely consistent across all active shots; only expressions, poses, and movements change as described.{ret_video}

detailed_description:
{c['camera_line']}{c['shots_content']}

overall_soundscape: {c['sound']}
non_diegetic_music: {c['music']}"""


def _build_char_ref_anchor(anchor, c, opts):
    """char_ref_first_frame / char_ref_last_frame。<Picture 1>=キャラシート, <Picture 2>=アンカー。"""
    names = c["names"]
    shot_list = ", ".join(names)
    final = names[-1]
    src = (opts.get("clothing_source") or "character_ref")
    frame_word = "starting" if anchor == "first" else "final"

    if src.startswith("character_ref"):
        cdef = "face, hair, clothing, skin tone, and body proportions of the reference character (<Picture 1>)"
        cret = "face, hair, clothing, skin tone, and body proportions from <Picture 1>"
    elif src == "ignore":
        cdef = "face, hair, skin tone, and body proportions of the reference (<Picture 1>)"
        cret = "face, hair, and body proportions from <Picture 1>"
    else:  # first_frame / last_frame
        cdef = (f"face, hair, and skin tone of the reference (<Picture 1>), but the clothing of the "
                f"{frame_word} frame (<Picture 2>)")
        cret = "face, hair, and body from <Picture 1>, and clothing from <Picture 2>"

    extra = normalize_tags((opts.get("character_extra") or "").strip())
    extra_s = f" {extra}" if extra else ""
    a = c["action_str"]

    if anchor == "first":
        pic_def = ("<Picture 2> is the first frame of [Shot 1], showing <Subject 1> (the same person as defined "
                   "in <Picture 1>) in the exact pose, framing, and composition of the starting reference image.")
        summary = ("[character consistency generation + keyframe completion] The target video starts from the "
                   f"exact first frame of <Picture 2> and continues forward as a continuous video{a}. The facial "
                   "features and styling of <Subject 1> are locked to the character reference (<Picture 1>), while "
                   "the initial pose, framing, and composition are locked to <Picture 2>.")
        pic_ret = ("<Picture 2> ([Shot 1] first frame): fully_preserved - the target video opens on the exact "
                   "composition, pose, framing, and lighting of <Picture 2> (excluding subject identity mapping).")
    else:
        pic_def = (f"<Picture 2> is the last frame of {final}, showing <Subject 1> (the same person as defined "
                   "in <Picture 1>) in the exact final pose, framing, composition, and lighting of the ending "
                   "reference image.")
        summary = ("[character consistency generation + keyframe completion] The target video begins from a "
                   f"plausible preceding state and develops as a continuous video{a}, gradually converging so that the "
                   f"final moment of {final} lands exactly on <Picture 2>. The facial features and styling of "
                   "<Subject 1> are locked to the character reference (<Picture 1>), while the final pose, "
                   "framing, and composition are locked to <Picture 2>.")
        pic_ret = (f"<Picture 2> ({final} last frame): fully_preserved - the target video ends on the exact "
                   "composition, pose, framing, and lighting of <Picture 2> (excluding subject identity mapping).")

    return f"""subject_definitions:
<Subject 1> is the {c['subject_type']} depicted in the character reference images (<Picture 1>, showing full-body and close-up face views), maintaining the exact {cdef}.{extra_s}
<Picture 1> provides the definitive character design sheet for <Subject 1>.
{pic_def}

summary: {summary}

retention_analysis:
<Subject 1> (appears in {shot_list}): fully_preserved - the exact {cret} remain entirely consistent across all active shots; only expressions, poses, and movements change as described.
{pic_ret}

detailed_description:
{c['camera_line']}{c['shots_content']}

overall_soundscape: {c['sound']}
non_diegetic_music: {c['music']}"""


# ------------------------------------------------------------------ entry
def generate(mode, opts, summary_action, camera_style, soundscape, enable_music, music_text,
             shot_kwargs, auto_speech_cue=True, subject_type="woman"):
    """全モード共通の入口。戻り値: (prompt, dialogue, status)"""
    if mode not in MODE_SPECS:
        raise ValueError(f"unknown mode: {mode}")
    spec = MODE_SPECS[mode]
    opts = opts or {}

    suffix = None
    if spec["motion"] and opts.get("auto_append_video_ref", True):
        suffix = VIDEO_REF_SUFFIX
    formatted, names, fdlg, total, warns = collect_from_kwargs(
        shot_kwargs, auto_speech_cue, append_suffix=suffix, skip_if_contains="<Video 1>")
    dialogue = dialogue_lines_only(fdlg)

    anchor_pic = "Picture 2" if spec["char_ref"] else "Picture 1"
    if not formatted:
        if spec["anchor"] == "first":
            fb = f"[Shot 1] The shot begins from <{anchor_pic}>."
        elif spec["anchor"] == "last":
            fb = "[Shot 1] The scene develops toward the final composition."
        else:
            fb = "[Shot 1] <Subject 1> appears in the scene."
            if spec["motion"]:
                fb += " The movement follows <Video 1>."
        formatted, names = [fb], ["[Shot 1]"]
        warns.append("有効なShotがないためフォールバック文を使用")

    if spec["anchor"] == "last" and f"<{anchor_pic}>" not in formatted[-1]:
        formatted[-1] = f"{formatted[-1]} {_landing(anchor_pic)}"

    action = normalize_tags((summary_action or "").strip())
    camera = (camera_style or "").strip()
    c = dict(
        names=names,
        shots_content="\n\n".join(formatted),
        subject_type=subject_type,
        action_str=f" of <Subject 1> {action}" if action else " of <Subject 1>",
        camera_line=f"The target video is filmed in {camera}.\n" if camera else "",
        sound=(soundscape or "").strip() or "N/A",
        music=resolve_music(enable_music, music_text),
    )

    if not spec["char_ref"]:
        prompt = _build_frame_only(spec["anchor"], c, opts)
    elif spec["anchor"]:
        prompt = _build_char_ref_anchor(spec["anchor"], c, opts)
    else:
        prompt = _build_char_ref(spec["motion"], c, opts)

    head = f"[{mode}] {refs_summary(mode)} | total {total:.1f}s"
    status = f"OK {head}" if not warns else f"WARN {head} | " + " / ".join(warns)
    return clean_prompt(prompt), dialogue, status
