from PIL import Image

import capture_engine
from capture_engine import CaptureConfig, CaptureEngine


class FakeMSS:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fixed_count_saves_exact_target_even_when_frames_look_identical(tmp_path, monkeypatch):
    target = 5
    config = CaptureConfig(
        window_title="Fake Kindle",
        region=(0, 0, 100, 100),
        turn_key="left",
        max_pages=target,
        settle_delay=0.0,
        change_timeout=0.01,
        diff_threshold=1.8,
        same_limit=3,
        output_dir=tmp_path / "session",
        capture_mode="fixed",
    )
    engine = CaptureEngine(config)

    turns = []
    logs = []
    progress = []

    monkeypatch.setattr(capture_engine.mss, "mss", lambda: FakeMSS())
    monkeypatch.setattr(capture_engine.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(capture_engine, "send_page_turn_key", lambda key: turns.append(key))
    engine.activate_target = lambda: None
    engine.capture = lambda _sct: Image.new("RGB", (100, 100), "white")
    engine.wait_for_stable_frame = lambda _sct: Image.new("RGB", (100, 100), "white")
    engine.on_log = logs.append
    engine.on_progress = lambda n, path: progress.append((n, path))

    result = engine.run()

    saved = sorted((tmp_path / "session" / "images").glob("page-*.png"))
    assert result["pages"] == target
    assert result["target_count"] == target
    assert result["capture_mode"] == "fixed"
    assert result["stop_reason"] == "fixed-count-reached"
    assert len(saved) == target
    assert len(turns) == target - 1
    assert [n for n, _path in progress] == [1, 2, 3, 4, 5]
    assert any("固定枚数モードのため停止せず" in line for line in logs)


def test_auto_mode_remains_default():
    config = CaptureConfig(window_title="x", region=(0, 0, 10, 10))
    assert config.capture_mode == "auto"
