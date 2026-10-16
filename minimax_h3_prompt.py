"""MiniMax-H3 統合プロンプトジェネレーター（V3 / DynamicCombo でモード切替）。"""
from comfy_api.latest import io

try:
    from .common import SUBJECT_TYPES, MAX_SHOTS, DEFAULT_DURATION, MIN_DURATION, MAX_SHOT_DURATION
    from .core import MODES, generate
except ImportError:
    from common import SUBJECT_TYPES, MAX_SHOTS, DEFAULT_DURATION, MIN_DURATION, MAX_SHOT_DURATION
    from core import MODES, generate


def _male_inputs():
    return [
        io.Boolean.Input("has_male", default=False, tooltip="男性（POV の手など）が登場する場合"),
        io.String.Input("male_role", default="", placeholder="【男性の役割】"),
        io.String.Input("male_retention_desc", default="", placeholder="【男性の保持設定】"),
    ]


def _frame_only_inputs():
    return [io.String.Input("subject1_extra", multiline=True, default="", placeholder="【人物の補足（任意）】")] \
        + _male_inputs()


def _char_extra():
    return io.String.Input("character_extra", multiline=True, default="", placeholder="【キャラクターの補足】")


def _clothing_combo(frame_key):
    return io.Combo.Input(
        "clothing_source", options=["character_ref", frame_key, "ignore"], default="character_ref",
        tooltip=f"character_ref: 服装も <Picture 1> に合わせる / {frame_key}: 服装は <Picture 2>、"
                "顔・髪は <Picture 1> / ignore: 服装に言及しない",
    )


def _mode_options():
    O = io.DynamicCombo.Option
    return [
        O("first_frame", _frame_only_inputs()),
        O("last_frame", _frame_only_inputs()),
        O("char_ref", [
            io.Boolean.Input("include_clothing", default=True, tooltip="服装もキャラシートに合わせる"),
            _char_extra(),
        ]),
        O("char_ref_motion", [
            io.Boolean.Input("include_clothing", default=True, tooltip="服装もキャラシートに合わせる"),
            io.String.Input("motion_video_desc", multiline=True, default="", placeholder="【参照動画の動き】"),
            io.Boolean.Input("auto_append_video_ref", default=True,
                             tooltip="各Shotに <Video 1> への参照を自動付与"),
            _char_extra(),
        ]),
        O("char_ref_first_frame", [_clothing_combo("first_frame"), _char_extra()]),
        O("char_ref_last_frame", [_clothing_combo("last_frame"), _char_extra()]),
    ]


def _shot_inputs():
    items = []
    for i in range(1, MAX_SHOTS + 1):
        items += [
            io.Boolean.Input(f"shot{i}_enable", default=(i == 1)),
            io.String.Input(f"shot{i}_text", multiline=True, default="", placeholder=f"【Shot {i}】本文"),
            io.Float.Input(f"shot{i}_duration", default=DEFAULT_DURATION,
                           min=MIN_DURATION, max=MAX_SHOT_DURATION, step=0.5),
            io.String.Input(f"shot{i}_dialogue", default="", placeholder=f"【Shot {i}】セリフ"),
        ]
    return items


class MinimaxH3PromptGenerator(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        assert [o.key for o in _mode_options()] == MODES  # core.MODE_SPECS との同期チェック
        return io.Schema(
            node_id="MinimaxH3PromptGenerator",
            display_name="MiniMax-H3 Prompt Generator",
            category="MiniMaxH3",
            description="first/last frame・character ref・motion の各モードを1ノードで切り替えて MiniMax-H3 用プロンプトを生成",
            inputs=[
                io.DynamicCombo.Input("mode", options=_mode_options(),
                                      tooltip="参照メディアの構成。選択に応じて追加入力が変わる"),
                io.String.Input("summary_action", multiline=True, default="", placeholder="【動画全体の動き】"),
                io.String.Input("camera_style", multiline=True, default="", placeholder="【カメラ・画作り】"),
                io.String.Input("soundscape", multiline=True, default="", placeholder="【環境音・効果音】"),
                io.Boolean.Input("enable_music", default=False),
                io.String.Input("music_text", multiline=True, default="", placeholder="【BGM】"),
                io.Combo.Input("subject_type", options=SUBJECT_TYPES, default="woman"),
                io.Boolean.Input("auto_speech_cue", default=True,
                                 tooltip="セリフがあるShotに発話キューを自動付与"),
                *_shot_inputs(),
            ],
            outputs=[
                io.String.Output("prompt"),
                io.String.Output("dialogue"),
                io.String.Output("status", tooltip="モード・必要な参照メディア・合計秒数・警告"),
            ],
        )

    @classmethod
    def execute(cls, mode, summary_action, camera_style, soundscape, enable_music, music_text,
                subject_type, auto_speech_cue, **kw):
        selected = mode["mode"]
        prompt, dialogue, status = generate(
            selected, mode, summary_action, camera_style, soundscape, enable_music, music_text,
            kw, auto_speech_cue=auto_speech_cue, subject_type=subject_type,
        )
        return io.NodeOutput(prompt, dialogue, status)
