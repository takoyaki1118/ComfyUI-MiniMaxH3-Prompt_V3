# ComfyUI-MiniMaxH3-Prompt_V3

MiniMax-H3 用プロンプトを「接続した参照の組み合わせ」から自動生成するカスタムノード。
モード選択はありません。参照ノードを繋ぐだけで `<Picture N>` / `<Video N>` / `<Audio N>` / `<Subject N>` の採番と、
subject_definitions / summary / retention_analysis が組み立てられます。

## 構成

    ref ノード（H3_REF を出力）
      Character Ref / Male Ref / Stage Ref / Costume Ref / Anchor Frame Ref / Motion Video Ref / Audio Ref
            ↓ ref1〜ref8
      Ref Bundle ── bundle ──→ Prompt Generator ──→ prompt / dialogue / status
            └ 種類ごとの固定ピン → 本家 Ref2V ノードへ（上から順に接続）
    Dialogue Merger（prompt + dialogue を結合）

## Ref Bundle の出力ピン（種類ごとに固定）

    画像:  character_1, character_2, anchor, male_1, male_2, costume_1, costume_2, stage_1, stage_2
    動画:  motion_1〜3（IMAGE＝24fps フレーム列。本家 ref_videos が IMAGE のため）
    音声:  audio_1〜3

- ピン名は Ref ノードの種類と一致します。他の参照を OFF にしても、各ピンの中身は動きません。
- OFF の参照のピンは `None` を出力します。画像は上の並び順（= `<Picture N>` の採番順）で本家 Ref2V の `ref_images` に接続してください。
- `status` に「ピン名→`<Picture N>`」の対応が出ます。
- 1種類あたりの画像数は上のピン数まで（character 2 / male 2 / costume 2 / stage 2 / anchor 1）。超過は ERROR。

## 全 Ref ノード共通

- `enable` スイッチ: OFF にすると Ref Bundle に接続されていても無視されます（ピンの付け替え不要）。
- `enabled` 出力（Boolean）: 他ノードのスイッチ連動用。

## ノード別のポイント

- **Anchor Frame Ref**: `megapixels` 指定で、アスペクト比を保ち 32 の倍数・nearest-exact にリサイズ。`image` / `width` / `height` を出力（0 でリサイズなし。OFF でもサイズは出力）。
- **Motion Video Ref**: `images`（IMAGE フレーム列）を受けてそのまま出力。`video`（VIDEO）を繋いだ場合はフレーム列に変換。`replace_appearance`（既定 ON）で、参照動画の人物の外見を最初のフレームから `<Subject 1>` に置き換えるよう明記します。
- **Audio / Anchor / Motion**: メディア入力は任意。未接続のまま `enable` を OFF にできます（ON で未接続は Bundle が ERROR）。

## 採番ルール（接続順ではなく kind の優先順）

| 対象 | 順序 |
|---|---|
| Picture | character → anchor → male → costume → stage |
| Video | motion |
| Audio | audio |
| Subject | character → anchor（character が無い場合のみ）→ male |

最初の参照画像 = `<Picture 1>` = `<Subject 1>`。音声の話者と衣装の変更対象は常にこの `<Subject 1>`。

## 検証（Ref Bundle の status）

- 画像 9 / 動画 3 / 音声 3 の上限
- anchor は 1 つまで
- male / costume / audio は character か anchor が必要
- audio は画像か動画が必要
- ERROR のまま Prompt Generator を実行すると例外で停止

## プリセット

`refs/characters|males|stages|costumes/<name>/` に画像（と任意の `ref.json`）を置く。詳細は `refs/README.md`。
フォルダを追加したら ComfyUI を再起動。

## 注意

- 旧 `ComfyUI-MiniMaxH3-Prompt`（モード式）と同時に有効化すると、ノード ID `MinimaxH3DialogueMerger` が衝突します。どちらかを無効化してください。
- 音声の役割文言は `refs.py` の `AUDIO_ROLE_TEXT`。公式書式に合わせて編集してください。
