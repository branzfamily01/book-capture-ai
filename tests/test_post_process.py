from pathlib import Path

from PIL import Image

import app_v5


def _images(folder: Path, count=2):
    folder.mkdir(parents=True)
    for n in range(1, count + 1):
        Image.new("RGB", (200, 120), "white").save(folder / f"page-{n:04d}.png")


def _config(images: Path, **overrides):
    config = {
        "image_dir": str(images), "captured_count": 2,
        "top_crop": 0.0, "bottom_crop": 0.0,
        "split_spreads": False, "split_order": "rtl",
        "split_order_label": "右 → 左", "base_name": "thread-test",
        "make_image_pdf": True, "make_ocr_pdf": False,
        "make_txt": False, "ocr_lang": "jpn+eng",
    }
    config.update(overrides)
    return config


def test_post_process_worker_creates_pdf_and_reports_stages(tmp_path):
    images = tmp_path / "images"
    _images(images)
    worker = app_v5.PostProcessThread(_config(images))
    progress = []
    completed = []
    worker.progress.connect(lambda n, total, message: progress.append(message))
    worker.completed.connect(completed.append)
    worker.run()
    assert completed and completed[0]["ocr_error"] is None
    assert (tmp_path / "thread-test.pdf").exists()
    assert "PDF生成中" in progress
    assert progress[-1] == "完了"


def test_ocr_failure_keeps_image_pdf_and_reports_reason(tmp_path, monkeypatch):
    images = tmp_path / "images"
    _images(images)
    monkeypatch.setattr(app_v5, "configure_tesseract", lambda: {
        "available": False, "languages": [], "error": "tesseract.exe missing"
    })
    worker = app_v5.PostProcessThread(_config(images, make_ocr_pdf=True))
    completed = []
    worker.completed.connect(completed.append)
    worker.run()
    assert (tmp_path / "thread-test.pdf").exists()
    assert "tesseract.exe missing" in completed[0]["ocr_error"]
    assert not (tmp_path / "thread-test-searchable.pdf").exists()
