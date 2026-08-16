from PIL import Image
from capture_engine import image_difference_score
from pdf_tools import sanitize_filename


def test_same_image_score_is_zero():
    a = Image.new("RGB", (100, 100), "white")
    b = Image.new("RGB", (100, 100), "white")
    assert image_difference_score(a, b) == 0.0


def test_changed_image_score_is_large():
    a = Image.new("RGB", (100, 100), "white")
    b = Image.new("RGB", (100, 100), "black")
    assert image_difference_score(a, b) > 100


def test_sanitize_filename():
    assert sanitize_filename('a:b/c*?') == 'a-b-c--'


def test_image_pdf_generation_is_multipage(tmp_path):
    from pypdf import PdfReader
    from pdf_tools import make_image_pdf

    image_dir = tmp_path / "images"
    image_dir.mkdir()
    for i in range(1, 21):
        Image.new("RGB", (400, 600), "white").save(
            image_dir / f"page-{i:04d}.png"
        )

    out = tmp_path / "book.pdf"
    make_image_pdf(image_dir, out)
    assert out.exists()
    assert len(PdfReader(str(out)).pages) == 20
