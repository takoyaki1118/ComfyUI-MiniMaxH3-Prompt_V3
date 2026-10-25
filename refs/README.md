# refs/ — 保存済み参照プリセット

    characters/<name>/   キャラクターシート（Character Ref）
    males/<name>/        男性参照（Male Ref）
    stages/<name>/       ステージ・背景（Stage Ref）
    costumes/<name>/     衣装（Costume Ref）

## 最小構成：画像だけでOK

フォルダを作って画像（png / jpg / jpeg / webp）を入れるだけで使えます。`ref.json` は不要です。

    characters/alice/
      sheet.png
      face.png

- フォルダ内の画像は **ファイル名順** に読み込まれ、`<Picture N>` が順に割り当てられます。
- 並び順を指定したい場合は `01_sheet.png` のように番号を付けるか、`ref.json` の `images` で指定します。
- `_` または `.` で始まるフォルダ（`_templates` など）は一覧に表示されません。

## ref.json（任意）

毎回ノードに打っている説明文などを保存しておく設定ファイルです。すべての項目が任意です。
テンプレートは `_templates/` にあります。使うときはプリセットフォルダにコピーして `ref.json` に改名してください。

    _templates/character_ref.json
    _templates/male_ref.json
    _templates/stage_ref.json
    _templates/costume_ref.json

例（characters/alice/ref.json）:

    {
      "subject_desc": "long dark hair, blue eyes, slim build",
      "retention": "fully_preserved",
      "images": ["sheet.png", "face.png"]
    }

### 使われる項目

| 項目 | characters | males | stages | costumes | 内容 |
|---|---|---|---|---|---|
| `subject_desc` | ○ | ○ | ○ | ○ | 説明文。ノードの `extra` と連結してプロンプトに入る |
| `retention` | ○ | ○ | - | - | 保持設定（空なら自動）。Male はノードの `retention_desc` が優先 |
| `role` | - | ○ | - | - | 男性の役割（ノードの `role` が優先） |
| `images` | ○ | ○ | ○ | ○ | 使う画像と順序。省略時はフォルダ内の全画像 |

- JSON はコメントを書けません。末尾のカンマや全角引用符があると読み込みに失敗し、`ref.json` は無視されます（画像は読まれます）。
- 説明文は英語でも日本語でも構いませんが、プロンプトはそのまま英語文に挿入されます。

## 反映について

- フォルダの追加・削除は、ノードの一覧に反映するため ComfyUI の再起動が必要です。
- 既存フォルダ内の画像・`ref.json` の変更は、再起動なしで反映されます。
