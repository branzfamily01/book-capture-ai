from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from PIL import Image


def _source_images(image_dir: Path) -> List[Path]:
    return sorted(Path(image_dir).glob("page-*.png"))


def split_spreads(
    image_dir: Path,
    output_dir: Path,
    order: str = "rtl",
) -> List[Path]:
    """Split each captured spread at the horizontal midpoint.

    The original spread images are never modified. Split pages are written as
    page-0001.png, page-0002.png, ... in reading order.

    order="rtl": right page first, then left page (Japanese book default)
    order="ltr": left page first, then right page
    """
    image_dir = Path(image_dir)
    output_dir = Path(output_dir)
    files = _source_images(image_dir)
    if not files:
        raise RuntimeError("見開き分割できる画像がありません。")
    if order not in {"rtl", "ltr"}:
        raise ValueError("order must be 'rtl' or 'ltr'")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    page_no = 1

    for source in files:
        with Image.open(source) as img:
            width, height = img.size
            if width < 2:
                raise RuntimeError(f"画像幅が小さすぎて分割できません: {source.name}")

            mid = width // 2
            left = img.crop((0, 0, mid, height))
            right = img.crop((mid, 0, width, height))
            halves = (right, left) if order == "rtl" else (left, right)

            for half in halves:
                out = output_dir / f"page-{page_no:04d}.png"
                half.save(out, "PNG", optimize=True)
                written.append(out)
                page_no += 1

            left.close()
            right.close()

    return written
