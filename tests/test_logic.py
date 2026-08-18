from PIL import Image
from capture_engine import image_difference_score, virtual_key_for
from pdf_tools import sanitize_filename
from app import logical_rect_to_native_region
from split_tools import split_spreads


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


def test_page_turn_virtual_keys():
    assert virtual_key_for("left") == 0x25
    assert virtual_key_for("right") == 0x27
    assert virtual_key_for("space") == 0x20
    assert virtual_key_for("pagedown") == 0x22


def test_split_spread_rtl_odd_width(tmp_path):
    src = tmp_path / "images"
    out = tmp_path / "images-split"
    src.mkdir()
    img = Image.new("RGB", (901, 100), "red")
    for x in range(450, 901):
        for y in range(100):
            img.putpixel((x, y), (0, 0, 255))
    img.save(src / "page-0001.png")

    files = split_spreads(src, out, order="rtl")
    assert len(files) == 2
    with Image.open(files[0]) as first, Image.open(files[1]) as second:
        assert first.size == (451, 100)
        assert second.size == (450, 100)
        assert first.getpixel((10, 10)) == (0, 0, 255)
        assert second.getpixel((10, 10)) == (255, 0, 0)


def test_split_spread_ltr_order(tmp_path):
    src = tmp_path / "images"
    out = tmp_path / "images-split"
    src.mkdir()
    img = Image.new("RGB", (800, 100), "red")
    for x in range(400, 800):
        for y in range(100):
            img.putpixel((x, y), (0, 0, 255))
    img.save(src / "page-0001.png")

    files = split_spreads(src, out, order="ltr")
    with Image.open(files[0]) as first, Image.open(files[1]) as second:
        assert first.getpixel((10, 10)) == (255, 0, 0)
        assert second.getpixel((10, 10)) == (0, 0, 255)


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


def test_build_outputs_creates_image_pdf(tmp_path):
    from pypdf import PdfReader
    from pdf_tools import build_outputs

    image_dir = tmp_path / "images"
    image_dir.mkdir()
    for i in range(1, 6):
        Image.new("RGB", (320, 480), "white").save(
            image_dir / f"page-{i:04d}.png"
        )

    outputs = build_outputs(
        image_dir=image_dir,
        base_name="book",
        make_image_pdf=True,
        make_ocr_pdf=False,
        make_txt=False,
    )
    pdf_path = tmp_path / "book.pdf"
    assert pdf_path in outputs
    assert pdf_path.exists()
    assert len(PdfReader(str(pdf_path)).pages) == 5


def test_dpi_region_conversion_150_percent():
    assert logical_rect_to_native_region(
        (100, 120, 800, 1000),
        (0, 0, 1280, 720),
        1.5,
    ) == (150, 180, 1200, 1500)


def test_dpi_region_conversion_secondary_screen_origin_is_preserved():
    assert logical_rect_to_native_region(
        (2000, 100, 400, 300),
        (1920, 0, 1280, 720),
        1.25,
    ) == (2020, 125, 500, 375)
