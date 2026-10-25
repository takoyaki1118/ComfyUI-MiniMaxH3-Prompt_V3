# ComfyUI-MiniMaxH3-Prompt_V3

MiniMax-H3 用プロンプトを「接続した参照の組み合わせ」から自動生成するカスタムノード。
モード選択はありません。参照ノードを繋ぐだけで `<Picture N>` / `<Video N>` / `<Audio N>` / `<Subject N>` の採番と、
subject_definitions / summary / retention_analysis が組み立てられます。

## 構成

    ref ノード（H3_REF を出力）
      Character Ref / Male Ref / Stage Ref / Costume Ref / Anchor Frame Ref / Motion Video Ref / Audio Ref
            ↓ ref1〜ref8
      Ref Bundle ── bundle ──→ Prompt Generator ──→ prompt / dialogue / status
            └ picture_1..9 / video_1..3 / audio_1..3 → 本家 Ref2V ノードへ（番号順に接続）
    Dialogue Merger（prompt + dialogue を結合）

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
