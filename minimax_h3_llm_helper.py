import json
import re

from comfy_api.latest import io

try:
    from .common import normalize_tags, strip_shot_prefix, clean_dialogue_text
    from .core import MODES
except ImportError:
    from common import normalize_tags, strip_shot_prefix, clean_dialogue_text
    from core import MODES

MAX_SHOTS = 4
DEFAULT_DURATION = 3.0

STYLE_PRESETS = {
    "auto (infer from action)": "",
    "photorealistic live-action": (
        "photorealistic live-action footage with natural skin texture, realistic lighting "
        "and real lens behavior (depth of field, subtle motion blur)"
    ),
    "cinematic live-action": (
        "cinematic live-action film look with dramatic lighting, shallow depth of field, "
        "color grading and deliberate camera movement"
    ),
    "anime (2D)": (
        "high-quality 2D Japanese anime with clean line art, cel shading, vivid colors "
        "and expressive, stylized motion"
    ),
    "3D CG animation": (
        "polished 3D CG animation with soft global illumination, smooth rigged motion "
        "and clean stylized materials"
    ),
    "illustration / painterly": (
        "painterly illustrated look with visible brush texture, soft edges and a "
        "hand-crafted color palette"
    ),
    "claymation / stop-motion": (
        "tactile stop-motion / claymation look with handmade textures and slightly "
        "stepped, charming motion"
    ),
    "custom (use style_note only)": "",
}


MODE_RULES = {
    "first_frame": (
        "MODE: first_frame (keyframe completion)\n"
        "- <Picture 1> is the first frame of [Shot 1]. Shot 1 must START from that exact "
        "pose, framing and composition, then continue forward naturally.\n"
        "- Refer to the person only as <Subject 1>."
    ),
    "char_ref": (
        "MODE: char_ref (character reference generation)\n"
        "- <Subject 1> is a character defined by character reference images.\n"
        "- Do not describe the character's face, hair or clothing; the reference defines them."
    ),
    "char_ref_motion": (
        "MODE: char_ref_motion (character reference + motion reference video)\n"
        "- <Subject 1> is the character from the reference images. <Video 1> is a motion reference video.\n"
        "- Do not describe the character's appearance. You may mention <Video 1>; "
        "if omitted, the system appends it automatically."
    ),
    "char_ref_first_frame": (
        "MODE: char_ref_first_frame (character reference sheet + first frame composition)\n"
        "- <Picture 1> is the character sheet. <Picture 2> is the exact first frame of [Shot 1].\n"
        "- Shot 1 must START from the pose, framing and composition of <Picture 2>."
    ),
    "char_ref_last_frame": (
        "MODE: char_ref_last_frame (character reference sheet + last frame composition)\n"
        "- <Picture 1> is the character sheet. <Picture 2> is the exact LAST frame of the final shot.\n"
        "- The video starts from a plausible preceding state and the final shot must END on the pose, framing "
        "and composition of <Picture 2>. Mention <Picture 2> only in the final shot as the ending composition."
    ),
    "last_frame": (
        "MODE: last_frame (L2VA: last-frame completion)\n"
        "- <Picture 1> is the LAST frame of the final shot. The video starts from a plausible preceding "
        "state and the final shot must END on that exact pose, framing and composition.\n"
        "- Mention <Picture 1> only in the final shot (as the ending composition). "
        "Do not describe it as a starting point."
    ),
}

MEDIA_OPTIONS = ["none", "image", "video", "image+video", "character_ref + first_frame (2 images)"]

MEDIA_HINTS = {
    "image": "one image (<Picture 1>)",
    "video": "one reference video (<Video 1>)",
    "image+video": "one image (<Picture 1>) and one reference video (<Video 1>)",
    "character_ref + first_frame (2 images)": (
        "two images (<Picture 1> = character reference sheet, <Picture 2> = first frame)"
    ),
}

# few-shot 用のサンプル Shot（モード別）
_EXAMPLE_SHOTS = {
    "first_frame": [
        "Starting exactly from the pose in <Picture 1>, <Subject 1> slowly lifts a cup and glances out the window with a gentle smile. Medium shot with a slight push-in.",
        "Close-up of <Subject 1> turning toward the camera and laughing softly as warm light shifts across the face.",
    ],
    "char_ref": [
        "<Subject 1> sits by a cafe window and slowly lifts a cup, glancing outside with a gentle smile. Medium shot with a slight push-in.",
        "Close-up of <Subject 1> turning toward the camera and laughing softly as warm light shifts across the face.",
    ],
    "char_ref_motion": [
        "<Subject 1> sits by a cafe window and slowly lifts a cup, glancing outside with a gentle smile. Medium shot with a slight push-in.",
        "Close-up of <Subject 1> turning toward the camera and laughing softly as warm light shifts across the face.",
    ],
    "char_ref_first_frame": [
        "Starting exactly from the pose in <Picture 2>, <Subject 1> slowly lifts a cup and glances out the window with a gentle smile. Medium shot with a slight push-in.",
        "Close-up of <Subject 1> turning toward the camera and laughing softly as warm light shifts across the face.",
    ],
    "char_ref_last_frame": [
        "<Subject 1> walks along a quiet street at dusk, glancing at the shop windows. Tracking shot from the side.",
        "<Subject 1> stops, turns toward the camera and settles into the final pose of <Picture 2> with a soft smile.",
    ],
    "last_frame": [
        "<Subject 1> walks along a quiet street at dusk, glancing at the shop windows. Tracking shot from the side.",
        "<Subject 1> stops, turns toward the camera and settles into the final pose of <Picture 1> with a soft smile.",
    ],
}


def _template_json(shot_count, include_soundscape, include_music, has_dialogue):
    t = {"summary_action": "...", "camera_style": "..."}
    if include_soundscape:
        t["soundscape"] = "..."
    if include_music:
        t["music"] = "..."
    t["shots"] = ["..."] * shot_count
    t["durations"] = [DEFAULT_DURATION] * shot_count
    if has_dialogue:
        t["dialogues"] = ["..."] * shot_count
    return json.dumps(t, ensure_ascii=False, indent=2)


def _example_json(mode, shot_count, include_soundscape, include_music, has_dialogue):
    shots = _EXAMPLE_SHOTS[mode][:shot_count]
    ex = {
        "summary_action": "<Subject 1> enjoys a quiet moment at a cafe and reacts warmly to the camera.",
        "camera_style": "Cinematic live-action look, soft window light, shallow depth of field.",
    }
    if include_soundscape:
        ex["soundscape"] = "Quiet cafe ambience, faint clinking of cups."
    if include_music:
        ex["music"] = "Soft acoustic guitar, gentle and warm."
    ex["shots"] = shots
    ex["durations"] = [3.0, 2.5][:len(shots)]
    if has_dialogue:
        ex["dialogues"] = ["", "ふふ、ありがとう。"][:len(shots)]
    return json.dumps(ex, ensure_ascii=False, indent=2)


def build_prompts(mode, action_idea, style, style_note, shot_count,
              include_soundscape, include_music, dialogue_idea="", extra_notes="",
              duration_sec=0, attached_media="none", few_shot_example=False):

            has_dialogue = bool(dialogue_idea.strip())

            field_lines = [
                "- 'summary_action': ONE sentence describing what <Subject 1> does.",
                "- 'camera_style': camera, lighting and look in one sentence.",
            ]
            if include_soundscape:
                field_lines.append("- 'soundscape': one sentence of diegetic sounds.")
            if include_music:
                field_lines.append("- 'music': one sentence describing the background music.")
            field_lines.append(f"- 'shots': array of exactly {shot_count} string(s), one per shot.")
            field_lines.append(
                "- 'durations': array of numbers (seconds, 0.5 to 15, in 0.5 steps), one per shot, "
                "in the same order as 'shots'."
            )
            if has_dialogue:
                field_lines.append(
                    "- 'dialogues': array of strings, one per shot, containing ONLY the spoken Japanese line "
                    "(no tags, no quotation marks, no '[Japanese]'). Use \"\" for shots without speech. "
                    "The system wraps the line into the required <d> format."
                )
            fields_block = "\n".join(field_lines)

            omitted = []
            if not include_soundscape:
                omitted.append("'soundscape'")
            if not include_music:
                omitted.append("'music'")
            omit_rule = f"\n- Omit these keys entirely: {', '.join(omitted)}." if omitted else ""

            media_block = ""
            if attached_media in MEDIA_HINTS:
                media_block = (
                    f"\n\nATTACHED MEDIA\n- The user also attached {MEDIA_HINTS[attached_media]}. "
                    "Use it only to understand the context; refer to it only through its tag."
                )

            example_block = ""
            if few_shot_example:
                example_block = (
                    "\n\nEXAMPLE (the number of shots in your answer must follow the instruction, "
                    "not this example)\n"
                    + _example_json(mode, shot_count, include_soundscape, include_music, has_dialogue)
                )

            system_prompt = f"""You are a professional prompt writer for the MiniMax-H3 video generation model.
The user gives a short idea. Expand it into a complete, production-ready JSON prompt.

{MODE_RULES[mode]}

GENERAL RULES
- Write ALL field values in English (except the Japanese lines in 'dialogues', if used).
- Each shot = ONE continuous camera setup.
- Do NOT write "[Shot N]" prefixes or timestamps (like "At 00:03.500,") inside the shot text; the system adds them from 'durations'.
- Refer to people only through tags such as <Subject 1>.{omit_rule}
- Return ONLY a single valid JSON object, no code fences.

FIELDS
{fields_block}

OUTPUT FORMAT
{_template_json(shot_count, include_soundscape, include_music, has_dialogue)}{media_block}{example_block}"""

            lines = [f"Action idea: {action_idea.strip() or '(none given)'}"]
            if has_dialogue:
                lines.append(f"Dialogue idea: {dialogue_idea.strip()}")
            preset = STYLE_PRESETS.get(style, "")
            if preset:
                lines.append(f"Visual style: {preset}")
            if style_note.strip():
                lines.append(f"Style note: {style_note.strip()}")
            lines.append(f"Number of shots: exactly {shot_count}")
            if duration_sec > 0:
                lines.append(f"Approximate total duration: {duration_sec} seconds (the 'durations' should sum to about this)")
            if extra_notes.strip():
                lines.append(f"Extra notes: {extra_notes.strip()}")
            lines.append("\nReturn the JSON object now.")

            return (system_prompt, "\n".join(lines))


# ---------------------------------------------------------------- parsing
_STR = r'"(?:[^"\\]|\\.)*"'


def _unescape(s):
    return s.replace('\\"', '"').replace("\\n", " ").replace("\\\\", "\\")


def _extract_json(text):
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    text = re.sub(r"```(?:json)?", "", text, flags=re.I).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1:
        return None
    candidate = text[start:end + 1] if end > start else text[start:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return _lenient_parse(candidate)


def _lenient_parse(text):
    """壊れた JSON から取れるものだけ救出する。配列は閉じ括弧／途中切れまでで止める。"""
    data = {}
    for key in ("summary_action", "camera_style", "soundscape", "music", "motion_video_desc"):
        m = re.search(r'"%s"\s*:\s*(%s)' % (key, _STR), text, flags=re.S)
        if m:
            data[key] = _unescape(m.group(1)[1:-1])
    for key_arr in ("shots", "dialogues"):
        m = re.search(r'"%s"\s*:\s*\[((?:\s*%s\s*,?)*)' % (key_arr, _STR), text, flags=re.S)
        if m:
            data[key_arr] = [_unescape(x) for x in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))]
    m = re.search(r'"durations"\s*:\s*\[([^\]]*)', text)
    if m:
        data["durations"] = re.findall(r"-?\d+(?:\.\d+)?", m.group(1))
    if data:
        data["_lenient"] = True
        return data
    return None


def _to_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _to_duration(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return DEFAULT_DURATION
    return min(15.0, max(0.5, round(v * 2) / 2))


def parse_llm_text(llm_text):
    data = _extract_json(llm_text)
    default_durs = (DEFAULT_DURATION,) * 4
    empty = ("", "", "", "", "", "", "", "", "", "", "", "", False, False, False, False, "", 0)

    if not isinstance(data, dict):
        return empty + ("ERROR: JSON を解析できませんでした。",) + default_durs

    def s(key):
        v = data.get(key, "")
        return normalize_tags(str(v).strip()) if v else ""

    shots_raw = _to_list(data.get("shots", []))
    dlg_raw = _to_list(data.get("dialogues", []))
    dur_raw = _to_list(data.get("durations", []))

    # shots / durations / dialogues のインデックスを揃えたまま、空 shot だけ除外する
    items = []
    for idx, x in enumerate(shots_raw):
        body = normalize_tags(strip_shot_prefix(str(x).strip())) if x is not None else ""
        if not body:
            continue
        dur = _to_duration(dur_raw[idx]) if idx < len(dur_raw) else DEFAULT_DURATION
        dlg = clean_dialogue_text(str(dlg_raw[idx])) if idx < len(dlg_raw) and dlg_raw[idx] is not None else ""
        items.append((body, dur, dlg))

    warnings = []
    if len(items) > MAX_SHOTS:
        warnings.append(f"shots が {len(items)} 個あります（先頭{MAX_SHOTS}個のみ使用）")
    items = items[:MAX_SHOTS]
    count = len(items)
    if count == 0:
        warnings.append("shots が空です")
    if dur_raw and len(dur_raw) != len(shots_raw):
        warnings.append("durations と shots の数が一致しません")
    if data.get("_lenient"):
        warnings.append("JSONが不完全です")

    pad = MAX_SHOTS - count
    shots = [i[0] for i in items] + [""] * pad
    durs = [i[1] for i in items] + [DEFAULT_DURATION] * pad
    dlgs = [i[2] for i in items] + [""] * pad

    music = s("music")
    status = "OK" if not warnings else "WARN: " + " / ".join(warnings)

    return (
        s("summary_action"), s("camera_style"), s("soundscape"), music,
        shots[0], shots[1], shots[2], shots[3],
        dlgs[0], dlgs[1], dlgs[2], dlgs[3],
        bool(music), shots[1] != "", shots[2] != "", shots[3] != "",
        s("motion_video_desc"), count, status,
        durs[0], durs[1], durs[2], durs[3],
    )


class MinimaxH3SystemPromptBuilder(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3SystemPromptBuilder",
            display_name="MiniMax-H3 System Prompt Builder",
            category="MiniMaxH3",
            inputs=[
                io.Combo.Input("mode", options=MODES, default="char_ref"),
                io.String.Input("action_idea", multiline=True, default="", placeholder="【動作・アイデア】"),
                io.Combo.Input("style", options=list(STYLE_PRESETS.keys()), default="auto (infer from action)"),
                io.String.Input("style_note", default="", placeholder="【スタイル補足】"),
                io.Int.Input("shot_count", default=2, min=1, max=MAX_SHOTS, step=1),
                io.Boolean.Input("include_soundscape", default=True),
                io.Boolean.Input("include_music", default=False),
                io.String.Input("dialogue_idea", multiline=True, default="", optional=True,
                                placeholder="【セリフのアイデア】例: 1個目のカットで「こんにちは！」と言う"),
                io.String.Input("extra_notes", multiline=True, default="", optional=True, placeholder="【追加指示】"),
                io.Int.Input("duration_sec", default=0, min=0, max=60, step=1, optional=True),
                io.Combo.Input("attached_media", options=MEDIA_OPTIONS, default="none", optional=True),
                io.Boolean.Input("few_shot_example", default=False, optional=True),
            ],
            outputs=[io.String.Output("system_prompt"), io.String.Output("user_prompt")],
        )

    @classmethod
    def execute(cls, mode, action_idea, style, style_note, shot_count, include_soundscape, include_music,
                dialogue_idea="", extra_notes="", duration_sec=0, attached_media="none", few_shot_example=False):
        return io.NodeOutput(*build_prompts(
            mode, action_idea, style, style_note, shot_count, include_soundscape, include_music,
            dialogue_idea or "", extra_notes or "", duration_sec, attached_media, few_shot_example))


class MinimaxH3LLMOutputParser(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3LLMOutputParser",
            display_name="MiniMax-H3 LLM Output Parser",
            category="MiniMaxH3",
            inputs=[io.String.Input("llm_text", multiline=True, default="", force_input=True)],
            outputs=[
                io.String.Output("summary_action"),
                io.String.Output("camera_style"),
                io.String.Output("soundscape"),
                io.String.Output("music_text"),
                io.String.Output("shot1_text"),
                io.String.Output("shot2_text"),
                io.String.Output("shot3_text"),
                io.String.Output("shot4_text"),
                io.String.Output("dialogue1"),
                io.String.Output("dialogue2"),
                io.String.Output("dialogue3"),
                io.String.Output("dialogue4"),
                io.Boolean.Output("enable_music"),
                io.Boolean.Output("shot2_enable"),
                io.Boolean.Output("shot3_enable"),
                io.Boolean.Output("shot4_enable"),
                io.String.Output("motion_video_desc"),
                io.Int.Output("shot_count"),
                io.String.Output("status"),
                io.Float.Output("duration1"),
                io.Float.Output("duration2"),
                io.Float.Output("duration3"),
                io.Float.Output("duration4"),
            ],
        )

    @classmethod
    def execute(cls, llm_text):
        return io.NodeOutput(*parse_llm_text(llm_text))
