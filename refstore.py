"""カスタムノードフォルダ内 refs/ からの参照プリセット読み込み。

refs/
  characters/<name>/{ref.json, *.png}
  males/<name>/ ...   stages/<name>/ ...   costumes/<name>/ ...

ref.json（すべて任意）:
  {"subject_desc": "long dark hair, blue eyes", "retention": "fully_preserved",
   "role": "male lead", "images": ["sheet.png", "face.png"]}
  images を省略するとフォルダ内の画像を名前順にすべて読む。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "refs"
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
NONE = "(none)"


def _dir(cat, name):
    base = (ROOT / cat).resolve()
    d = (base / name).resolve()
    return d if d.is_dir() and d.parent == base else None  # パストラバーサル防止


def list_presets(cat):
    base = ROOT / cat
    names = sorted(p.name for p in base.iterdir()
                   if p.is_dir() and not p.name.startswith(("_", "."))) if base.is_dir() else []
    return [NONE] + names


def _to_tensor(path):
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return torch.from_numpy(np.asarray(img).astype("float32") / 255.0)[None,]  # [1,H,W,3]


def load_preset(cat, name):
    out = dict(images=[], desc="", retention="", role="", name="")
    d = _dir(cat, name) if name and name != NONE else None
    if d is None:
        return out
    meta = {}
    jf = d / "ref.json"
    if jf.is_file():
        try:
            meta = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    files = [Path(f).name for f in meta.get("images", [])] or \
        sorted(f.name for f in d.iterdir() if f.suffix.lower() in IMG_EXT)
    out["images"] = [_to_tensor(d / f) for f in files if (d / f).is_file()]
    out["desc"] = str(meta.get("subject_desc", "")).strip()
    out["retention"] = str(meta.get("retention", "")).strip()
    out["role"] = str(meta.get("role", "")).strip()
    out["name"] = name
    return out


def fingerprint(cat, name):
    """フォルダ内容が変わったらキャッシュを無効化するための値。"""
    d = _dir(cat, name) if name and name != NONE else None
    if d is None:
        return ""
    return str(max((f.stat().st_mtime for f in d.iterdir()), default=0.0))
