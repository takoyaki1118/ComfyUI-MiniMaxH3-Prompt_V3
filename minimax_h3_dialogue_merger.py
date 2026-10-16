import re

from comfy_api.latest import io

try:
    from .common import clean_prompt
except ImportError:
    from common import clean_prompt

_SECTION_START = "detailed_description:"
_SECTION_END = "overall_soundscape:"

# モードごとの挿入行の接頭辞
_PREFIX = {
    "official_format": "  ",
    "insert_into_shots": "  - Dialogue: ",
}


def _insert_dialogues(prompt, d_lines, prefix):
    """detailed_description 内の各 [Shot N] 行の直下にセリフ行を挿入する。

    戻り値: (新しいプロンプト, 処理できた行数)
    """
    start = prompt.find(_SECTION_START)
    head, body = (prompt[:start], prompt[start:]) if start != -1 else ("", prompt)
    end = body.find(_SECTION_END)
    body, tail = (body[:end], body[end:]) if end != -1 else (body, "")

    handled = 0
    for line in d_lines:
        m = re.match(r"^\[Shot (\d+)\]\s*(.*)$", line)
        if not m:
            continue
        num, content = m.group(1), m.group(2).strip()
        if not content:
            continue
        if content in body:  # 既に挿入済み（再実行対策）
            handled += 1
            continue
        m_shot = re.search(rf"(?m)^\[Shot {num}\][^\n]*", body)
        if not m_shot:
            continue
        body = body[:m_shot.end()] + f"\n{prefix}{content}" + body[m_shot.end():]
        handled += 1

    return head + body + tail, handled


def merge_dialogue(prompt, dialogue, merge_mode, dialogue_label):
    p_text = (prompt or "").strip()
    d_text = (dialogue or "").strip()
    label = (dialogue_label or "dialogues").strip() or "dialogues"
    if not d_text:
        return p_text
    if merge_mode == "append_to_end":
        return clean_prompt(f"{p_text}\n\n{label}:\n{d_text}")
    d_lines = [l.strip() for l in d_text.split("\n") if l.strip()]
    prefix = _PREFIX.get(merge_mode, _PREFIX["official_format"])
    merged, handled = _insert_dialogues(p_text, d_lines, prefix)
    if handled == 0:  # 1行も挿入できなければ末尾に退避
        merged = f"{p_text}\n\n{label}:\n{d_text}"
    return clean_prompt(merged)


class MinimaxH3DialogueMerger(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3DialogueMerger",
            display_name="MiniMax-H3 Dialogue Merger",
            category="MiniMaxH3",
            inputs=[
                io.String.Input("prompt", multiline=True, default="", force_input=True),
                io.String.Input("dialogue", multiline=True, default="", force_input=True),
                io.Combo.Input("merge_mode", options=["official_format", "append_to_end", "insert_into_shots"],
                               default="official_format",
                               tooltip="official_format: 各Shot直下にセリフ行を挿入 / append_to_end: 末尾に見出し付きで追加 / "
                                       "insert_into_shots: 各Shot直下に '- Dialogue:' 付きで挿入"),
                io.String.Input("dialogue_label", default="dialogues",
                                tooltip="末尾追加（append_to_end・挿入失敗時のフォールバック）の見出しラベル"),
            ],
            outputs=[io.String.Output("merged_prompt")],
        )

    @classmethod
    def execute(cls, prompt, dialogue, merge_mode, dialogue_label):
        return io.NodeOutput(merge_dialogue(prompt, dialogue, merge_mode, dialogue_label))
