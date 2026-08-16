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
