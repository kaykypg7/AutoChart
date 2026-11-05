import io
from pathlib import Path
from typing import Optional

import requests
from PIL import Image


def _save_square_jpg(img_bytes: bytes, dst: Path, size: int = 500) -> Path:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "JPEG", quality=90)
    return dst


def fetch_art(dst: Path, *, direct_url: Optional[str] = None,
              itunes_query: Optional[str] = None) -> Optional[Path]:
    if direct_url:
        try:
            r = requests.get(direct_url, timeout=15)
            if r.ok and r.content:
                return _save_square_jpg(r.content, dst)
        except requests.RequestException:
            pass
    if itunes_query:
        try:
            r = requests.get(
                "https://itunes.apple.com/search",
                params={"term": itunes_query, "media": "music", "limit": 1},
                timeout=15,
            )
            if r.ok:
                data = r.json()
                if data.get("results"):
                    art_url = (data["results"][0].get("artworkUrl100") or "").replace("100x100", "600x600")
                    if art_url:
                        rr = requests.get(art_url, timeout=15)
                        if rr.ok and rr.content:
                            return _save_square_jpg(rr.content, dst)
        except requests.RequestException:
            pass
    return None
