from __future__ import annotations

import ctypes
import sys
import time
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple, List

import mss
import pygetwindow as gw
from PIL import Image, ImageChops, ImageStat


PAGE_KEY_VK = {
    "left": 0x25,      # VK_LEFT
    "right": 0x27,     # VK_RIGHT
    "space": 0x20,     # VK_SPACE
    "pagedown": 0x22,  # VK_NEXT
}
KEYEVENTF_KEYUP = 0x0002


@dataclass
class CaptureConfig:
    window_title: str
    region: Tuple[int, int, int, int]  # x, y, w, h
    turn_key: str = "left"
    max_pages: int = 500
    settle_delay: float = 0.45
    change_timeout: float = 2.5
    diff_threshold: float = 1.8
    same_limit: int = 3
    output_dir: Path = Path("capture-output")


def list_windows(exclude_contains=None) -> List[str]:
    exclude_contains = exclude_contains or []
    titles = []
    try:
        for title in gw.getAllTitles():
            t = (title or "").strip()
            if not t:
                continue
            if any(x.lower() in t.lower() for x in exclude_contains):
                continue
            if t not in titles:
                titles.append(t)
    except Exception:
        pass
    return titles


def virtual_key_for(key: str) -> int:
    normalized = (key or "").strip().lower()
    if normalized not in PAGE_KEY_VK:
        raise ValueError(f"未対応のページ送りキーです: {key}")
    return PAGE_KEY_VK[normalized]


def send_page_turn_key(key: str):
    """Send a page-turn key using the native Windows API.

    This deliberately avoids PyAutoGUI so moving the mouse to a screen corner
    cannot abort a capture. Target-window foreground verification is performed
    separately immediately before this call.
    """
    if sys.platform != "win32":
        raise RuntimeError("ページ送りキー送信はWindows版でのみ利用できます。")

    vk = virtual_key_for(key)
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def image_difference_score(a: Image.Image, b: Image.Image) -> float:
    """Measure both whole-screen and localized content changes.

    A whole-screen mean misses Kindle pages where only a small text or table
    area changes against a large white background.  The average of the eight
    most-changed tiles detects those real page turns while ignoring a cursor or
    caret confined to one tile.
    """
    a = a.convert("L").resize((240, 240))
    b = b.convert("L").resize((240, 240))
    diff = ImageChops.difference(a, b)
    whole_mean = float(ImageStat.Stat(diff).mean[0])
    tile_scores = []
    tile_size = 30
    for y in range(0, 240, tile_size):
        for x in range(0, 240, tile_size):
            tile = diff.crop((x, y, x + tile_size, y + tile_size))
            tile_scores.append(float(ImageStat.Stat(tile).mean[0]))
    localized_mean = sum(sorted(tile_scores, reverse=True)[:8]) / 8
    return max(whole_mean, localized_mean)


class CaptureEngine:
    def __init__(self, config: CaptureConfig):
        self.config = config
        self._stop = threading.Event()
        self._pause = threading.Event()
        self.on_progress: Optional[Callable[[int, str], None]] = None
        self.on_log: Optional[Callable[[str], None]] = None
        self.on_status: Optional[Callable[[str], None]] = None

    def log(self, text: str):
        if self.on_log:
            self.on_log(text)

    def status(self, text: str):
        if self.on_status:
            self.on_status(text)

    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def stop(self):
        self._stop.set()

    def wait_if_paused(self):
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.15)

    def activate_target(self):
        windows = gw.getWindowsWithTitle(self.config.window_title)
        if not windows:
            raise RuntimeError(f"対象ウィンドウが見つかりません: {self.config.window_title}")

        win = windows[0]
        hwnd = getattr(win, "_hWnd", None)
        last_error = None

        for attempt in range(1, 4):
            try:
                if win.isMinimized:
                    win.restore()
                    time.sleep(0.2)
                win.activate()
            except Exception as e:
                last_error = e

            time.sleep(0.18)

            if hwnd:
                try:
                    user32 = ctypes.windll.user32
                    if user32.GetForegroundWindow() == hwnd:
                        return

                    SW_RESTORE = 9
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                    time.sleep(0.18)
                    if user32.GetForegroundWindow() == hwnd:
                        return
                except Exception as e:
                    last_error = e
            else:
                try:
                    active = gw.getActiveWindow()
                    if active and active.title == win.title:
                        return
                except Exception as e:
                    last_error = e

            self.log(f"対象ウィンドウ切替を再試行しています ({attempt}/3)")

        detail = f" ({last_error})" if last_error else ""
        raise RuntimeError(
            "対象の電子書籍ウィンドウを前面にできませんでした。"
            "誤って別アプリへページ送りキーを送らないため処理を停止します。"
            + detail
        )

    def capture(self, sct: mss.mss) -> Image.Image:
        x, y, w, h = self.config.region
        shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def save_image(self, img: Image.Image, image_dir: Path, index: int) -> Path:
        path = image_dir / f"page-{index:04d}.png"
        img.save(path, "PNG", optimize=True)
        return path

    def wait_for_stable_frame(self, sct: mss.mss) -> Image.Image:
        """Wait for page-turn animation/rendering to settle before saving."""
        time.sleep(self.config.settle_delay)
        previous = self.capture(sct)
        stable_hits = 0
        stability_threshold = max(0.35, self.config.diff_threshold * 0.25)
        deadline = time.time() + max(1.0, self.config.change_timeout)

        while time.time() < deadline and not self._stop.is_set():
            self.wait_if_paused()
            time.sleep(0.12)
            current = self.capture(sct)
            motion = image_difference_score(previous, current)

            if motion <= stability_threshold:
                stable_hits += 1
                if stable_hits >= 2:
                    return current
            else:
                stable_hits = 0

            previous = current

        self.log("警告: ページ表示の完全な安定を確認できなかったため、最新フレームを保存します。")
        return previous

    def run(self):
        output_dir = Path(self.config.output_dir)
        image_dir = output_dir / "images"
        image_dir.mkdir(parents=True, exist_ok=True)

        metadata = {
            "window_title": self.config.window_title,
            "region": list(self.config.region),
            "turn_key": self.config.turn_key,
            "max_pages": self.config.max_pages,
            "settle_delay": self.config.settle_delay,
            "change_timeout": self.config.change_timeout,
            "diff_threshold": self.config.diff_threshold,
            "same_limit": self.config.same_limit,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        (output_dir / "session.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        self.status("対象ウィンドウを準備中…")
        self.activate_target()
        time.sleep(0.5)

        same_count = 0
        # A saved value of 1 can stop a long capture on one sparse/slow page.
        # Always retry at least three turns before deciding the book ended.
        effective_same_limit = max(3, self.config.same_limit)
        stop_reason = "maximum-pages" if self.config.max_pages <= 1 else "completed"
        page_count = 0
        prev = None

        with mss.mss() as sct:
            self.wait_if_paused()
            if self._stop.is_set():
                return {"pages": 0, "image_dir": str(image_dir)}

            first = self.capture(sct)
            page_count = 1
            path = self.save_image(first, image_dir, page_count)
            prev = first
            if self.on_progress:
                self.on_progress(page_count, str(path))

            while page_count < self.config.max_pages and not self._stop.is_set():
                self.wait_if_paused()
                if self._stop.is_set():
                    break

                self.status(f"{page_count}ページ取得済み — 次ページへ")
                self.activate_target()
                send_page_turn_key(self.config.turn_key)

                changed = False
                start = time.time()
                candidate = None

                while time.time() - start < self.config.change_timeout:
                    if self._stop.is_set():
                        break
                    self.wait_if_paused()
                    time.sleep(0.12)
                    candidate = self.capture(sct)
                    score = image_difference_score(prev, candidate)
                    if score >= self.config.diff_threshold:
                        changed = True
                        self.log(f"画面変化検出: score={score:.2f}")
                        break

                if self._stop.is_set():
                    break

                if not changed:
                    same_count += 1
                    self.log(
                        f"画面変化なし — ページ送りを再試行します "
                        f"({same_count}/{effective_same_limit})"
                    )
                    if same_count >= effective_same_limit:
                        self.status("同じ画面が続いたため自動停止")
                        stop_reason = "same-screen-limit"
                        break
                    continue

                stable = self.wait_for_stable_frame(sct)
                final_score = image_difference_score(prev, stable)

                if final_score < self.config.diff_threshold:
                    same_count += 1
                    self.log(
                        f"安定後に前ページとほぼ同一: score={final_score:.2f} "
                        f"({same_count}/{effective_same_limit})"
                    )
                    if same_count >= effective_same_limit:
                        self.status("同じ画面が続いたため自動停止")
                        stop_reason = "same-screen-limit"
                        break
                    continue

                same_count = 0
                page_count += 1
                path = self.save_image(stable, image_dir, page_count)
                prev = stable
                if self.on_progress:
                    self.on_progress(page_count, str(path))

        self.status("キャプチャ終了")
        if self._stop.is_set():
            stop_reason = "manual-stop"
        elif page_count >= self.config.max_pages:
            stop_reason = "maximum-pages"
        self.log(f"終了理由: {stop_reason}")
        return {"pages": page_count, "image_dir": str(image_dir), "stop_reason": stop_reason}
