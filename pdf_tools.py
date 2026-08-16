from __future__ import annotations

import re
from pathlib import Path
from typing import List

from PIL import Image


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:120] or "captured-book"


def _images(image_dir: Path):
    return sorted(image_dir.glob("page-*.png"))


def make_image_pdf(image_dir: Path, out_path: Path):
    files = _images(image_dir)
    if not files:
        raise RuntimeError("PDFにできる画像がありません。")
    images = [Image.open(p).convert("RGB") for p in files]
    try:
        first, rest = images[0], images[1:]
        first.save(out_path, "PDF", resolution=150.0, save_all=True, append_images=rest)
    finally:
        for img in images:
            img.close()


def tesseract_available():
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def make_ocr_outputs(image_dir: Path, pdf_path: Path | None, txt_path: Path | None, lang: str):
    if not tesseract_available():
        raise RuntimeError(
            "Tesseract OCR が見つかりません。OCR出力を使う場合は Tesseract をインストールし、"
            "日本語言語データを追加してください。画像PDFはOCRなしで作成できます。"
        )

    import pytesseract
    from pypdf import PdfReader, PdfWriter
    from io import BytesIO

    files = _images(image_dir)
    writer = PdfWriter() if pdf_path else None
    text_parts = []

    for idx, path in enumerate(files, start=1):
        img = Image.open(path)
        try:
            if pdf_path:
                page_pdf = pytesseract.image_to_pdf_or_hocr(img, extension="pdf", lang=lang)
                reader = PdfReader(BytesIO(page_pdf))
                for page in reader.pages:
                    writer.add_page(page)
            if txt_path:
                text = pytesseract.image_to_string(img, lang=lang)
                text_parts.append(f"\n\n===== PAGE {idx} =====\n\n{text.strip()}")
        finally:
            img.close()

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
) -> List[Path]:
    session_dir = image_dir.parent
    outputs = []

    if make_image_pdf:
        p = session_dir / f"{base_name}.pdf"
        make_image_pdf_fn = globals()["make_image_pdf"]
        make_image_pdf_fn(image_dir, p)
        outputs.append(p)

    if make_ocr_pdf or make_txt:
        pdf_path = session_dir / f"{base_name}-searchable.pdf" if make_ocr_pdf else None
        txt_path = session_dir / f"{base_name}.txt" if make_txt else None
        make_ocr_outputs(image_dir, pdf_path, txt_path, ocr_lang)
        if pdf_path:
            outputs.append(pdf_path)
        if txt_path:
            outputs.append(txt_path)

    outputs.append(image_dir)
    return outputs
