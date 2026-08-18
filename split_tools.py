from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from PIL import Image


def _source_images(image_dir: Path) -> List[Path]:
    return sorted(Path(image_dir).glob("page-*.png"))


def _crop_vertical(img: Image.Image, top_crop_pct: float, bottom_crop_pct: float) -> Image.Image:
    if top_crop_pct < 0 or bottom_crop_pct < 0:
        raise ValueError("crop percentages must be >= 0")
    if top_crop_pct + bottom_crop_pct >= 100:
        raise ValueError("top + bottom crop must be < 100%")

    width, height = img.size
    top = int(round(height * top_crop_pct / 100.0))
    bottom = int(round(height * bottom_crop_pct / 100.0))
    y2 = height - bottom
    if y2 <= top:
        raise RuntimeError("上下トリミング量が大きすぎます。")
    return img.crop((0, top, width, y2))


def trim_pages(
    image_dir: Path,
    output_dir: Path,
    top_crop_pct: float = 0.0,
    bottom_crop_pct: float = 0.0,
    progress=None,
) -> List[Path]:
    """Trim header/footer from single-page captures while preserving originals."""
    image_dir = Path(image_dir)
    output_dir = Path(output_dir)
    files = _source_images(image_dir)
    if not files:
        raise RuntimeError("トリミングできる画像がありません。")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for index, source in enumerate(files, start=1):
        with Image.open(source) as img:
            clean = _crop_vertical(img, top_crop_pct, bottom_crop_pct)
            out = output_dir / f"page-{index:04d}.png"
            clean.save(out, "PNG", optimize=True)
            clean.close()
            written.append(out)
        if progress:
            progress(index, len(files), "トリミング中")
    return written


def split_spreads(
    image_dir: Path,
    output_dir: Path,
    order: str = "rtl",
    top_crop_pct: float = 0.0,
    bottom_crop_pct: float = 0.0,
    progress=None,
) -> List[Path]:
    """Trim a captured spread, then split it at the horizontal midpoint.

    Original spread images are never modified. The vertical crop is applied
    before splitting so Kindle header/footer UI disappears from both pages.

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

    for spread_index, source in enumerate(files, start=1):
        with Image.open(source) as img:
            clean = _crop_vertical(img, top_crop_pct, bottom_crop_pct)
            width, height = clean.size
            if width < 2:
                clean.close()
                raise RuntimeError(f"画像幅が小さすぎて分割できません: {source.name}")

            mid = width // 2
            left = clean.crop((0, 0, mid, height))
            right = clean.crop((mid, 0, width, height))
            halves = (right, left) if order == "rtl" else (left, right)

            for half in halves:
                out = output_dir / f"page-{page_no:04d}.png"
                half.save(out, "PNG", optimize=True)
                written.append(out)
                page_no += 1

            left.close()
            right.close()
            clean.close()

        if progress:
            progress(spread_index, len(files), "分割中")

    return written
