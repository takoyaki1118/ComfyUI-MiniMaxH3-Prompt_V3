"""MiniMax-H3 ノード群（V3）。モードではなく「繋いだ参照の組み合わせ」でプロンプトを自動構成する。

  [Character/Male/Stage/Costume/Anchor/Motion/Audio Ref] ─H3_REF→ [Ref Bundle] ─H3_BUNDLE→ [Ref Prompt Generator]
                                                              └→ picture_1..9 / video_1..3 / audio_1..3 → 本家 Ref2V ノードへ
"""
from comfy_api.latest import io

try:
    from . import refstore as store
    from .common import SUBJECT_TYPES, MAX_SHOTS, DEFAULT_DURATION, MIN_DURATION, MAX_SHOT_DURATION
    from .refs import (Ref, bundle_refs, describe, generate_from_bundle, AUDIO_ROLES,
                       pin_layout, SIZE_STEP, fit_size, describe_placement, SCENE_STRENGTHS)
except ImportError:
    import refstore as store
    from common import SUBJECT_TYPES, MAX_SHOTS, DEFAULT_DURATION, MIN_DURATION, MAX_SHOT_DURATION
    from refs import (Ref, bundle_refs, describe, generate_from_bundle, AUDIO_ROLES,
                      pin_layout, SIZE_STEP, fit_size, describe_placement, SCENE_STRENGTHS)

H3_REF = io.Custom("H3_REF")
H3_BUNDLE = io.Custom("H3_BUNDLE")
CATEGORY = "MiniMaxH3/ref"
MAX_REF_PINS = 8
RETENTIONS = ["partially_preserved", "fully_preserved"]


# ---------------------------------------------------------------- helpers
def _preset_input(cat):
    opts = store.list_presets(cat)
    return io.Combo.Input("preset", options=opts, default=opts[0],
                          tooltip=f"refs/{cat}/ 内のプリセット（追加後は ComfyUI の再起動で一覧に反映）")


def _image_override():
    return io.Image.Input("image", optional=True,
                          tooltip="接続した場合はプリセット画像の代わりにこの画像を使用")


def _shot_flags(suffix, tooltip, first_only=False):
    return [io.Boolean.Input(f"shot{i}_{suffix}", default=(i == 1 or not first_only), tooltip=tooltip)
            for i in range(1, MAX_SHOTS + 1)]


def _pick_shots(kw, suffix):
    sel = [i for i in range(1, MAX_SHOTS + 1) if kw.get(f"shot{i}_{suffix}", True)]
    return [] if len(sel) in (0, MAX_SHOTS) else sel  # 空 = 全Shot


def _images(image, data):
    if image is not None:
        return [image[i:i + 1] for i in range(image.shape[0])]
    return data["images"]


def _join(*parts):
    return " ".join(p.strip() for p in parts if p and p.strip())


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


# ---------------------------------------------------------------- ref nodes
# 全 Ref ノード共通: enable(ON/OFF) 入力と、ref / enabled(Boolean) 出力。
# OFF の Ref は Bundle に接続されていても無視される（ピンの付け替え不要）。
def _enable():
    return io.Boolean.Input("enable", default=True,
                            tooltip="OFF にすると Ref Bundle に接続されていても無視される")


def _outs(*extra):
    return [H3_REF.Output("ref"),
            io.Boolean.Output("enabled", tooltip="この参照が有効か（他ノードのスイッチ用）"),
            *extra]


def _off(kind, *extra):
    return io.NodeOutput(Ref(kind, enabled=False), False, *extra)


class MinimaxH3CharacterRef(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3CharacterRef", display_name="MiniMax-H3 Character Ref", category=CATEGORY,
            description="キャラクターシート参照。<Subject 1> になる（最初の参照画像）",
            inputs=[
                _enable(), _preset_input("characters"), _image_override(),
                io.String.Input("extra", multiline=True, default="", placeholder="【キャラクターの補足】（ref.json の説明に追記）"),
                io.Boolean.Input("include_clothing", default=True, tooltip="服装もキャラシートに合わせる（Costume Ref 接続時は無視）"),
            ],
            outputs=_outs(),
        )

    @classmethod
    def fingerprint_inputs(cls, **kw):
        return store.fingerprint("characters", kw.get("preset"))

    @classmethod
    def execute(cls, enable, preset, extra, include_clothing, image=None):
        if not enable:
            return _off("character")
        d = store.load_preset("characters", preset)
        return io.NodeOutput(Ref("character", images=_images(image, d), name=d["name"],
                                 desc=_join(d["desc"], extra), retention=d["retention"],
                                 opts=dict(include_clothing=include_clothing)), True)


class MinimaxH3MaleRef(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3MaleRef", display_name="MiniMax-H3 Male Ref", category=CATEGORY,
            description="男性の参照画像。登場Shotを指定可能。画像なし＝アンカー画像に写っている男性",
            inputs=[
                _enable(), _preset_input("males"), _image_override(),
                io.Boolean.Input("pov_hands", default=False,
                                 tooltip="ON: POV の手だけが映る男性として扱う（role が空のとき 'male POV hands'）。OFF: 通常の男性（'man'）"),
                io.String.Input("role", default="", placeholder="【男性の役割】空なら man（例: man / male POV hands）"),
                io.String.Input("retention_desc", default="", placeholder="【保持設定】（空なら自動）"),
                io.String.Input("extra", multiline=True, default="", placeholder="【外見の補足】"),
                *_shot_flags("appears", "このShotに登場"),
            ],
            outputs=_outs(),
        )

    @classmethod
    def fingerprint_inputs(cls, **kw):
        return store.fingerprint("males", kw.get("preset"))

    @classmethod
    def execute(cls, enable, preset, pov_hands, role, retention_desc, extra, image=None, **kw):
        if not enable:
            return _off("male")
        d = store.load_preset("males", preset)
        return io.NodeOutput(Ref("male", images=_images(image, d), name=d["name"],
                                 role=role.strip() or d["role"], desc=_join(d["desc"], extra),
                                 retention=retention_desc.strip() or d["retention"],
                                 shots=_pick_shots(kw, "appears"), opts=dict(pov_hands=pov_hands)), True)


class MinimaxH3StageRef(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3StageRef", display_name="MiniMax-H3 Stage Ref", category=CATEGORY,
            description="背景・照明・空間の参照（人物は無視させる）",
            inputs=[
                _enable(), _preset_input("stages"), _image_override(),
                io.String.Input("extra", multiline=True, default="", placeholder="【ステージの補足】"),
                io.Combo.Input("retention", options=RETENTIONS, default="partially_preserved"),
                *_shot_flags("applies", "このShotで使用"),
            ],
            outputs=_outs(),
        )

    @classmethod
    def fingerprint_inputs(cls, **kw):
        return store.fingerprint("stages", kw.get("preset"))

    @classmethod
    def execute(cls, enable, preset, extra, retention, image=None, **kw):
        if not enable:
            return _off("stage")
        d = store.load_preset("stages", preset)
        return io.NodeOutput(Ref("stage", images=_images(image, d), name=d["name"],
                                 desc=_join(d["desc"], extra), retention=retention,
                                 shots=_pick_shots(kw, "applies")), True)


class MinimaxH3CostumeRef(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3CostumeRef", display_name="MiniMax-H3 Costume Ref", category=CATEGORY,
            description="衣装の差し替え。対象は常に最初の参照画像（<Picture 1> / <Subject 1>）",
            inputs=[
                _enable(), _preset_input("costumes"), _image_override(),
                io.Combo.Input("parts", options=["whole outfit", "top", "bottom", "accessories"],
                               default="whole outfit", tooltip="変更範囲（全体 / 上衣 / 下衣 / アクセサリ）"),
                io.String.Input("extra", multiline=True, default="", placeholder="【衣装の補足】"),
            ],
            outputs=_outs(),
        )

    @classmethod
    def fingerprint_inputs(cls, **kw):
        return store.fingerprint("costumes", kw.get("preset"))

    @classmethod
    def execute(cls, enable, preset, parts, extra, image=None):
        if not enable:
            return _off("costume")
        d = store.load_preset("costumes", preset)
        return io.NodeOutput(Ref("costume", images=_images(image, d), name=d["name"],
                                 desc=_join(d["desc"], extra), opts=dict(parts=parts)), True)


def _resize_nearest(image, w, h):
    """[B,H,W,C] を nearest-exact で (h, w) にリサイズ。"""
    import torch.nn.functional as F
    if image.shape[1] == h and image.shape[2] == w:
        return image
    x = F.interpolate(image.movedim(-1, 1), size=(h, w), mode="nearest-exact")
    return x.movedim(1, -1)


class MinimaxH3AnchorRef(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3AnchorRef", display_name="MiniMax-H3 Anchor Frame Ref", category=CATEGORY,
            description="先頭 / 末尾フレーム画像。Character Ref が無い場合は <Subject 1> になる。"
                        "メガピクセル指定で 32 の倍数にリサイズし、幅・高さを出力する",
            inputs=[
                _enable(),
                io.Image.Input("image", optional=True),
                io.Combo.Input("anchor_type", options=["first", "last"], default="first"),
                io.Float.Input("megapixels", default=1.0, min=0.0, max=8.0, step=0.05,
                               tooltip=f"目標メガピクセル。アスペクト比維持・{SIZE_STEP} の倍数・nearest-exact。0 でリサイズなし"),
                io.String.Input("extra", multiline=True, default="", placeholder="【人物の補足（任意）】"),
            ],
            outputs=_outs(io.Image.Output("image"), io.Int.Output("width"), io.Int.Output("height")),
        )

    @classmethod
    def execute(cls, enable, anchor_type, megapixels, extra, image=None):
        if image is None:
            return io.NodeOutput(Ref("anchor", enabled=enable, opts=dict(anchor_type=anchor_type)), enable, None, 0, 0)
        w, h = fit_size(int(image.shape[2]), int(image.shape[1]), megapixels)
        if not enable:  # OFF でもサイズは出力する（動画サイズの入力に使えるように）
            return io.NodeOutput(Ref("anchor", enabled=False), False, image, w, h)
        image = _resize_nearest(image, w, h)
        ref = Ref("anchor", images=[image[i:i + 1] for i in range(image.shape[0])],
                  desc=extra, opts=dict(anchor_type=anchor_type))
        return io.NodeOutput(ref, True, image, w, h)


def _mask_scene(image, mask, mode):
    """人物マスク（白=人物）から配置の英文を作り、mode に応じて人物領域を塗りつぶす/ぼかす。"""
    import torch
    import torch.nn.functional as F
    B, H, W, C = image.shape
    m = mask.float()
    if m.dim() == 2:
        m = m.unsqueeze(0)
    if tuple(m.shape[1:]) != (H, W):
        m = F.interpolate(m.unsqueeze(1), size=(H, W), mode="nearest-exact").squeeze(1)
    m = (m[:1] > 0.5).float()                      # [1,H,W]
    placement = ""
    ys, xs = torch.nonzero(m[0], as_tuple=True)
    if ys.numel():
        placement = describe_placement(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, W, H)
    if mode == "none":
        return image, placement
    mm = m.unsqueeze(-1)                           # [1,H,W,1]
    if mode == "gray_fill":
        fill = torch.full_like(image, 0.5)
    else:                                          # blur: 縮小→拡大（計算量は画素数に比例。大きな箱型ぼかしは CPU で極端に遅い）
        f = max(4, min(H, W) // 32)
        x = image.movedim(-1, 1)                   # [B,C,H,W]
        small = F.interpolate(x, size=(max(1, H // f), max(1, W // f)), mode="area")
        fill = F.interpolate(small, size=(H, W), mode="bilinear", align_corners=False).movedim(1, -1)
    return image * (1 - mm) + fill * mm, placement


class MinimaxH3SceneRef(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3SceneRef", display_name="MiniMax-H3 Scene Composition Ref", category=CATEGORY,
            description="場面イラストの構図・ポーズ・画風を継承し、人物だけを <Subject 1>（Character Ref）に差し替える。"
                        "キーフレームではなくストーリーボード参照として扱うため、その画像に着地しない",
            inputs=[
                _enable(),
                io.Image.Input("image", optional=True, tooltip="構図・ポーズ・画風・背景・照明・カメラアングルを継承したい場面イラスト"),
                io.Mask.Input("mask", optional=True,
                              tooltip="人物部分のマスク（白=人物。背景ではなく人物を指定）。配置の説明文の自動生成と、人物領域の加工に使う"),
                io.Combo.Input("mask_mode", options=["none", "gray_fill", "blur"], default="none",
                               tooltip="none: 画像はそのまま（配置の文章だけ生成） / gray_fill: 人物領域を灰色で塗る / "
                                       "blur: 人物領域を強くぼかす（縮小→拡大方式。元の人物の顔・服が参照画像から漏れるのを抑える）"),
                io.Boolean.Input("inherit_pose", default=True, tooltip="人物のポーズ・姿勢を <Subject 1> に移す"),
                io.Boolean.Input("inherit_scene", default=True, tooltip="背景・照明・画風を継承する"),
                io.Combo.Input("strength", options=SCENE_STRENGTHS, default="partially_preserved",
                               tooltip="partially_preserved: 構図とポーズをできるだけ維持 / weak_reference: 大まかな雰囲気だけ"),
                io.String.Input("extra", multiline=True, default="", placeholder="【場面の補足（任意）】"),
                *_shot_flags("applies", "この Shot で構図を参照する（既定は Shot 1 のみ）", first_only=True),
            ],
            outputs=_outs(io.Image.Output("image")),
        )

    @classmethod
    def execute(cls, enable, mask_mode, inherit_pose, inherit_scene, strength, extra,
                image=None, mask=None, **kw):
        if not enable:
            return _off("scene", image)
        out, placement = image, ""
        if image is not None and mask is not None:
            out, placement = _mask_scene(image, mask, mask_mode)
        ref = Ref("scene", images=[out[:1]] if out is not None else [], desc=extra, retention=strength,
                  shots=_pick_shots(kw, "applies"),
                  opts=dict(inherit_pose=inherit_pose, inherit_scene=inherit_scene, placement=placement))
        return io.NodeOutput(ref, True, out)


def _frames_from_video(video):
    """VIDEO → IMAGE フレーム列 [N,H,W,C]（本家 ref_videos は IMAGE 入力のため）。"""
    return video.get_components().images


class MinimaxH3MotionRef(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3MotionRef", display_name="MiniMax-H3 Motion Video Ref", category=CATEGORY,
            description="動き・タイミング・カメラワークの参照。IMAGE（フレーム列）を受け、そのまま motion ピンに出力する",
            inputs=[
                _enable(),
                io.Image.Input("images", optional=True, tooltip="参照動画のフレーム列（24fps・2〜15秒）"),
                io.Video.Input("video", optional=True, tooltip="images が未接続のときに使用（フレーム列に変換して出力）"),
                io.String.Input("motion_video_desc", multiline=True, default="", placeholder="【参照動画の動き】"),
                io.Boolean.Input("auto_append_video_ref", default=True, tooltip="各Shotに <Video N> への参照を自動付与"),
                io.Boolean.Input("replace_appearance", default=True,
                                 tooltip="参照動画の人物の外見を最初のフレームから <Subject 1> に置き換えるよう明記する"),
            ],
            outputs=_outs(),
        )

    @classmethod
    def execute(cls, enable, motion_video_desc, auto_append_video_ref, replace_appearance,
                images=None, video=None):
        if not enable:
            return _off("motion")
        if images is None and video is not None:
            images = _frames_from_video(video)
        return io.NodeOutput(Ref("motion", videos=[images] if images is not None else [], desc=motion_video_desc,
                                 opts=dict(auto_append_video_ref=auto_append_video_ref,
                                           replace_appearance=replace_appearance)), True)


class MinimaxH3AudioRef(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3AudioRef", display_name="MiniMax-H3 Audio Ref", category=CATEGORY,
            description="音声参照。話者は常に最初の参照画像（<Subject 1>）。視覚アンカー（画像/動画）が必須",
            inputs=[
                _enable(),
                io.Audio.Input("audio", optional=True),
                io.Combo.Input("audio_role", options=AUDIO_ROLES, default="reference"),
                io.Boolean.Input("lipsync", default=True, tooltip="<Subject 1> の口の動きを音声に同期させる"),
                io.String.Input("extra", multiline=True, default="", placeholder="【音声の補足】"),
                *_shot_flags("applies", "このShotで使用"),
            ],
            outputs=_outs(),
        )

    @classmethod
    def execute(cls, enable, audio_role, lipsync, extra, audio=None, **kw):
        if not enable:
            return _off("audio")
        return io.NodeOutput(Ref("audio", audios=[audio] if audio is not None else [], desc=extra,
                                 shots=_pick_shots(kw, "applies"),
                                 opts=dict(audio_role=audio_role, lipsync=lipsync)), True)


# ---------------------------------------------------------------- bundle
_PIN_OUT = {"image": io.Image.Output, "audio": io.Audio.Output}


class MinimaxH3RefBundle(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3RefBundle", display_name="MiniMax-H3 Ref Bundle", category=CATEGORY,
            description="参照を集めて採番。出力ピンは参照の種類ごとに固定（他の参照を OFF にしても動かない）。"
                        "本家 Ref2V ノードには画像ピンを上から順に接続する",
            inputs=[H3_REF.Input(f"ref{i}", optional=True) for i in range(1, MAX_REF_PINS + 1)],
            outputs=[
                H3_BUNDLE.Output("bundle"),
                io.String.Output("status", tooltip="ピン名→<Picture N> の対応・エラー・警告"),
                *[_PIN_OUT[t](name) for name, t in pin_layout()],
            ],
        )

    @classmethod
    def execute(cls, **kw):
        b = bundle_refs([kw.get(f"ref{i}") for i in range(1, MAX_REF_PINS + 1)])
        status = describe(b)
        if b.errors:
            status = "ERROR: " + " / ".join(b.errors) + " | " + status
        elif b.warnings:
            status = "WARN: " + " / ".join(b.warnings) + " | " + status
        else:
            status = "OK " + status
        return io.NodeOutput(b, status, *[b.pin_media.get(name) for name, _ in pin_layout()])


# ---------------------------------------------------------------- prompt generator
class MinimaxH3RefPromptGenerator(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MinimaxH3RefPromptGenerator", display_name="MiniMax-H3 Prompt Generator",
            category="MiniMaxH3",
            description="Ref Bundle の内容からモード不要でプロンプトを自動生成",
            inputs=[
                H3_BUNDLE.Input("bundle"),
                io.String.Input("summary_action", multiline=True, default="", placeholder="【動画全体の動き】"),
                io.String.Input("camera_style", multiline=True, default="", placeholder="【カメラ・画作り】"),
                io.String.Input("soundscape", multiline=True, default="", placeholder="【環境音・効果音】"),
                io.Boolean.Input("enable_music", default=False),
                io.String.Input("music_text", multiline=True, default="", placeholder="【BGM】"),
                io.Combo.Input("subject_type", options=SUBJECT_TYPES, default="woman"),
                io.Boolean.Input("auto_speech_cue", default=True, tooltip="セリフがあるShotに発話キューを自動付与"),
                *_shot_inputs(),
            ],
            outputs=[
                io.String.Output("prompt"),
                io.String.Output("dialogue"),
                io.String.Output("status"),
            ],
        )

    @classmethod
    def execute(cls, bundle, summary_action, camera_style, soundscape, enable_music, music_text,
                subject_type, auto_speech_cue, **kw):
        prompt, dialogue, status = generate_from_bundle(
            bundle, summary_action, camera_style, soundscape, enable_music, music_text,
            kw, auto_speech_cue=auto_speech_cue, subject_type=subject_type)
        return io.NodeOutput(prompt, dialogue, status)


NODES = [
    MinimaxH3CharacterRef, MinimaxH3MaleRef, MinimaxH3StageRef, MinimaxH3CostumeRef,
    MinimaxH3AnchorRef, MinimaxH3SceneRef, MinimaxH3MotionRef, MinimaxH3AudioRef,
    MinimaxH3RefBundle, MinimaxH3RefPromptGenerator,
]
