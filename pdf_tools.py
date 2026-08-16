from __future__ import annotations

import os
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Callable, List, Optional

from PIL import Image


ProgressCallback = Optional[Callable[[int, int, str], None]]


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:120] or "captured-book"


def _images(image_dir: Path):
    return sorted(image_dir.glob("page-*.png"))


def app_directory() -> Path:
    """Directory beside the executable in frozen builds, otherwise source directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_ocr_directory() -> Path:
    return app_directory() / "ocr"


def configure_tesseract() -> dict:
    """Prefer the bundled OCR runtime and fall back to a system installation."""
    import pytesseract

    ocr_dir = bundled_ocr_directory()
    bundled_exe = ocr_dir / "tesseract.exe"
    source = "system"

    if bundled_exe.exists():
        pytesseract.pytesseract.tesseract_cmd = str(bundled_exe)
        # Tesseract expects TESSDATA_PREFIX to point directly at the directory
        # containing *.traineddata for normal OCR invocation.
        tessdata_dir = ocr_dir / "tessdata"
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
        source = "bundled"

    try:
        version = str(pytesseract.get_tesseract_version())

        if source == "bundled":
            # Do not use pytesseract.get_languages() here. pytesseract caches that
            # result, so an early lookup can leave a stale empty list even after
            # TESSDATA_PREFIX is configured. The bundle contents are authoritative.
            tessdata_dir = ocr_dir / "tessdata"
            langs = sorted(
                path.stem for path in tessdata_dir.glob("*.traineddata") if path.is_file()
            )
        else:
            langs = sorted(pytesseract.get_languages(config=""))

        return {
            "available": True,
            "source": source,
            "version": version,
            "languages": langs,
            "executable": str(getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")),
        }
    except Exception as exc:
        return {
            "available": False,
            "source": source,
            "version": None,
            "languages": [],
            "executable": str(bundled_exe if bundled_exe.exists() else "tesseract"),
            "error": str(exc),
        }


def tesseract_available(required_languages=None) -> bool:
    info = configure_tesseract()
    if not info["available"]:
        return False
    if required_languages:
        needed = set(required_languages)
        return needed.issubset(set(info["languages"]))
    return True


def make_image_pdf(image_dir: Path, out_path: Path, progress: ProgressCallback = None):
    """Create an image-only PDF without holding every decoded page in RAM."""
    files = _images(image_dir)
    if not files:
        raise RuntimeError("PDFにできる画像がありません。")
    import img2pdf
    if progress:
        progress(0, len(files), "画像PDFを作成中")
    with open(out_path, "wb") as f:
        img2pdf.convert([str(p) for p in files], outputstream=f)
    if progress:
        progress(len(files), len(files), "画像PDFを作成しました")


def make_ocr_outputs(
    image_dir: Path,
    pdf_path: Path | None,
    txt_path: Path | None,
    lang: str,
    progress: ProgressCallback = None,
):
    required = [x for x in lang.split("+") if x]
    info = configure_tesseract()
    if not info["available"]:
        raise RuntimeError("内蔵OCRを起動できません。配布ファイルが壊れている可能性があります。")

    missing = sorted(set(required) - set(info["languages"]))
    if missing:
        raise RuntimeError("OCR言語データがありません: " + ", ".join(missing))

    import pytesseract
    from pypdf import PdfReader, PdfWriter

    files = _images(image_dir)
    writer = PdfWriter() if pdf_path else None
    text_parts = []
    total = len(files)

    for idx, path in enumerate(files, start=1):
        if progress:
            progress(idx - 1, total, f"OCR {idx}/{total}ページ")
        img = Image.open(path)
        try:
            # tessdata_fast requires the LSTM OCR engine (--oem 1).
            config = "--oem 1"
            if pdf_path:
                page_pdf = pytesseract.image_to_pdf_or_hocr(
                    img, extension="pdf", lang=lang, config=config
                )
                reader = PdfReader(BytesIO(page_pdf))
                for page in reader.pages:
                    writer.add_page(page)
            if txt_path:
                text = pytesseract.image_to_string(img, lang=lang, config=config)
                text_parts.append(f"\n\n===== PAGE {idx} =====\n\n{text.strip()}")
        finally:
            img.close()

        if progress:
            progress(idx, total, f"OCR {idx}/{total}ページ")

    if pdf_path and writer:
        with open(pdf_path, "wb") as f:
            writer.write(f)

    if txt_path:
        txt_path.write_text("".join(text_parts).lstrip(), encoding="utf-8")


def build_outputs(
    image_dir: Path,
    base_name: str,
    make_image_pdf: bool = True,
    make_ocr_pdf: bool = False,
    make_txt: bool = False,
    ocr_lang: str = "jpn+eng",
    progress: ProgressCallback = None,
) -> List[Path]:
    session_dir = image_dir.parent
    outputs = []

    if make_image_pdf:
        p = session_dir / f"{base_name}.pdf"
        # The boolean argument above intentionally keeps the public API name;
        # resolve the generator explicitly so it cannot shadow the function.
        globals()["make_image_pdf"](image_dir, p, progress=progress)
        outputs.append(p)

    if make_ocr_pdf or make_txt:
        pdf_path = session_dir / f"{base_name}-searchable.pdf" if make_ocr_pdf else None
        txt_path = session_dir / f"{base_name}.txt" if make_txt else None
        make_ocr_outputs(
            image_dir,
            pdf_path,
            txt_path,
            ocr_lang,
            progress=progress,
        )
        if pdf_path:
            outputs.append(pdf_path)
        if txt_path:
            outputs.append(txt_path)

    outputs.append(image_dir)
    return outputs
