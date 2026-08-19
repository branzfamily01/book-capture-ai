from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
)

from app import APP_NAME, CaptureThread
from app_v5 import (
    GlobalHotkeyFilter,
    HOTKEY_FINISH_ID,
    HOTKEY_PAUSE_ID,
    HOTKEY_START_ID,
    MainWindow as V5MainWindow,
)
from capture_engine import CaptureConfig
from pdf_tools import sanitize_filename


VERSION = "0.5.8"


class MainWindow(V5MainWindow):
    """Delayed button start, safe cancel and exact fixed-count capture."""

    def build_ui(self):
        super().build_ui()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")

        # v0.5.7 behavior: the start button owns the countdown. F7 start is removed.
        self.start_delay_seconds.setToolTip(
            "キャプチャ開始ボタンを押してから、実際に撮影を始めるまでの待ち時間"
        )
        for label in self.findChildren(QLabel):
            if label.text() == "F7開始までの待ち時間":
                label.setText("開始ボタン後の待ち時間")

        self.hotkey_help.setText(
            "操作: キャプチャ開始 → 設定秒数の間にKindleへ切替　｜　"
            "F8 一時停止／再開　｜　F9を2回 終了してPDF作成"
        )

        capture_box = next(
            (box for box in self.findChildren(QGroupBox) if box.title() == "2. キャプチャ設定"),
            None,
        )
        if capture_box is None or not isinstance(capture_box.layout(), QGridLayout):
            raise RuntimeError("キャプチャ設定UIを初期化できませんでした。")

        cap = capture_box.layout()
        row = cap.rowCount()
        self.capture_mode = QComboBox()
        self.capture_mode.addItem("本の終わりを自動判定（Smart Guard）", "auto")
        self.capture_mode.addItem("固定枚数 — 指定した枚数まで必ず撮る", "fixed")
        self.capture_mode.setToolTip(
            "固定枚数では同一画面判定で停止しません。1回のページ送りにつき1枚を保存します。"
        )
        cap.addWidget(QLabel("撮影終了方法"), row, 0)
        cap.addWidget(self.capture_mode, row, 1, 1, 3)

        self.fixed_count = QSpinBox()
        self.fixed_count.setRange(1, 5000)
        self.fixed_count.setValue(100)
        self.fixed_count.setSuffix(" 画面")
        self.fixed_count.setToolTip(
            "見開き表示なら、322ページの本は通常161画面です。表紙・単ページ表示がある場合は調整してください。"
        )
        hint = QLabel(
            "固定枚数モードでは『同じ画面』を検出しても警告だけ出し、停止しません。"
            "例：見開き322ページ → 撮影枚数161画面。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666;")
        cap.addWidget(QLabel("撮影枚数（見開き画面の数）"), row + 1, 0)
        cap.addWidget(self.fixed_count, row + 1, 1)
        cap.addWidget(hint, row + 1, 2, 1, 2)
        self.capture_mode.currentIndexChanged.connect(self._sync_capture_mode_ui)

        # v0.5.7 behavior: hard cancel stops capture and deliberately skips PDF/OCR.
        self.cancel_btn = QPushButton("キャンセル（PDFを作らず停止）")
        self.cancel_btn.setToolTip(
            "撮影をただちに止めます。PDF/OCRは作成しません。すでに撮影した画像は残します。"
        )
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_capture)

        main = self.centralWidget().layout()
        button_layout = None
        for i in range(main.count()):
            item = main.itemAt(i)
            layout = item.layout()
            if layout is not None and layout.indexOf(self.stop_btn) >= 0:
                button_layout = layout
                break
        if button_layout is None:
            raise RuntimeError("操作ボタン列を初期化できませんでした。")
        button_layout.addWidget(self.cancel_btn, 2)

        self._countdown_token = 0
        self._countdown_active = False
        self._cancel_without_output = False
        self._active_capture_mode = "auto"
        self._active_target_count = self.max_pages.value()
        self._sync_capture_mode_ui()

    def load_settings(self):
        super().load_settings()
        mode = str(self.settings.value("capture_mode", "auto"))
        idx = self.capture_mode.findData(mode)
        self.capture_mode.setCurrentIndex(idx if idx >= 0 else 0)
        self.fixed_count.setValue(int(self.settings.value("fixed_count", 100)))
        self._sync_capture_mode_ui()

    def save_settings(self):
        super().save_settings()
        if hasattr(self, "capture_mode"):
            self.settings.setValue("capture_mode", self.capture_mode.currentData() or "auto")
            self.settings.setValue("fixed_count", self.fixed_count.value())

    def _sync_capture_mode_ui(self):
        if not hasattr(self, "capture_mode"):
            return
        controls_unlocked = self.start_btn.isEnabled() and not self._countdown_active
        is_fixed = (self.capture_mode.currentData() or "auto") == "fixed"
        self.fixed_count.setEnabled(controls_unlocked and is_fixed)
        self.max_pages.setEnabled(controls_unlocked and not is_fixed)
        self.same_limit.setEnabled(controls_unlocked and not is_fixed)
        if is_fixed:
            self.same_limit.setToolTip("固定枚数モードでは同一画面による自動停止を使用しません。")
        else:
            self.same_limit.setToolTip("")

    def _lock_capture_mode_controls(self):
        self.capture_mode.setEnabled(False)
        self.fixed_count.setEnabled(False)
        self.max_pages.setEnabled(False)
        self.same_limit.setEnabled(False)

    def _unlock_capture_mode_controls(self):
        self.capture_mode.setEnabled(True)
        self._sync_capture_mode_ui()

    def register_global_hotkeys(self):
        """Register only F8/F9. F7 start is intentionally removed."""
        if sys.platform != "win32":
            return False
        user32 = ctypes.windll.user32
        no_repeat = 0x4000
        pause_ok = bool(user32.RegisterHotKey(None, HOTKEY_PAUSE_ID, no_repeat, 0x77))
        finish_ok = bool(user32.RegisterHotKey(None, HOTKEY_FINISH_ID, no_repeat, 0x78))
        if pause_ok and finish_ok:
            self.append_log("全画面ホットキー: F8 一時停止/再開、F9を2回 終了")
            return True
        if pause_ok:
            user32.UnregisterHotKey(None, HOTKEY_PAUSE_ID)
        if finish_ok:
            user32.UnregisterHotKey(None, HOTKEY_FINISH_ID)
        self.append_log("警告: F8/F9が他のアプリで使用中のため登録できませんでした。")
        return False

    def unregister_global_hotkeys(self):
        if sys.platform == "win32":
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_PAUSE_ID)
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_FINISH_ID)
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_START_ID)

    def handle_global_hotkey(self, hotkey_id):
        if hotkey_id == HOTKEY_START_ID:
            return
        super().handle_global_hotkey(hotkey_id)

    def start_capture(self):
        """Start a countdown; do not touch the Kindle window until it expires."""
        worker = getattr(self, "capture_thread", None)
        if (worker and worker.isRunning()) or self._countdown_active:
            return

        title = self.window_combo.currentText().strip()
        if not title:
            QMessageBox.warning(self, APP_NAME, "電子書籍ウィンドウを選択してください。")
            return

        self.save_settings()
        self._cancel_without_output = False
        self._countdown_active = True
        self._countdown_token += 1
        token = self._countdown_token
        delay = self.start_delay_seconds.value()

        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._lock_capture_mode_controls()
        self.status_label.setText(f"開始まで {delay}秒 — この間にKindleを全画面にしてください")
        self.append_log(
            f"キャプチャ開始ボタン: {delay}秒のカウントダウンを開始。"
            "この間はBook Capture AIからKindleを操作しません。"
        )
        self._sound()
        QTimer.singleShot(1000, lambda: self._countdown_tick(token, delay - 1))

    def _countdown_tick(self, token: int, remaining: int):
        if token != self._countdown_token or not self._countdown_active:
            return
        if remaining <= 0:
            self._countdown_active = False
            self._start_capture_now()
            return
        self.status_label.setText(f"開始まで {remaining}秒")
        QTimer.singleShot(1000, lambda: self._countdown_tick(token, remaining - 1))

    def _start_capture_now(self):
        try:
            title = self.window_combo.currentText().strip()
            if not title:
                raise RuntimeError("電子書籍ウィンドウが選択されていません。")

            out_root = Path(self.output_dir.text()).expanduser()
            out_root.mkdir(parents=True, exist_ok=True)
            book_name = sanitize_filename(self.book_title.text().strip() or "captured-book")
            session = time.strftime("%Y%m%d-%H%M%S")
            session_dir = out_root / f"{book_name}-{session}"

            mode = self.capture_mode.currentData() or "auto"
            target_count = self.fixed_count.value() if mode == "fixed" else self.max_pages.value()
            self._active_capture_mode = mode
            self._active_target_count = target_count

            config = CaptureConfig(
                window_title=title,
                region=self.region,
                turn_key=self.turn_key.currentData(),
                max_pages=target_count,
                settle_delay=self.settle_delay.value(),
                change_timeout=self.change_timeout.value(),
                diff_threshold=self.diff_threshold.value(),
                same_limit=self.same_limit.value(),
                output_dir=session_dir,
                capture_mode=mode,
            )

            self.log_box.clear()
            self.progress.setRange(0, target_count)
            self.progress.setValue(0)
            if mode == "fixed":
                self.status_label.setText(f"固定枚数モードを開始 — 0/{target_count}画面")
                self.append_log(
                    f"固定枚数モード: {target_count}画面。"
                    "同一画面判定は停止条件に使いません。"
                )
            else:
                self.status_label.setText("自動終了モードを開始…")
                self.append_log(
                    f"自動終了モード: 最大{target_count}画面 / 同一画面{self.same_limit.value()}回で停止"
                )

            self.start_btn.setEnabled(False)
            self.pause_btn.setEnabled(True)
            self.resume_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            self._lock_capture_mode_controls()

            self.capture_thread = CaptureThread(config)
            self.capture_thread.progress.connect(self.on_progress)
            self.capture_thread.log.connect(self.append_log)
            self.capture_thread.status.connect(self.status_label.setText)
            self.capture_thread.failed.connect(self.on_failed)
            self.capture_thread.completed.connect(self.on_capture_completed)
            self.capture_thread.start()
        except Exception:
            self._countdown_active = False
            self.on_failed(traceback.format_exc())

    def on_progress(self, n, path):
        self.progress.setValue(n)
        if self._active_capture_mode == "fixed":
            remaining = max(0, self._active_target_count - n)
            self.status_label.setText(
                f"撮影中 {n}/{self._active_target_count}画面 — 残り{remaining}画面"
            )
        else:
            self.status_label.setText(f"{n} 画面取得済み")
        self.append_log(f"保存: {Path(path).name}")

    def stop_capture(self):
        self._cancel_without_output = False
        super().stop_capture()

    def cancel_capture(self):
        """Stop capture without entering split/PDF/OCR post-processing."""
        if self._countdown_active:
            self._countdown_active = False
            self._countdown_token += 1
            self.append_log("開始カウントダウンをキャンセルしました。")
            self.status_label.setText("開始をキャンセルしました")
            self._sound()
            self.reset_buttons()
            return

        worker = getattr(self, "capture_thread", None)
        if worker and worker.isRunning():
            self._cancel_without_output = True
            worker.stop_capture()
            self.pause_btn.setEnabled(False)
            self.resume_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.cancel_btn.setEnabled(False)
            self.status_label.setText("キャンセル停止中… PDF/OCRは作成しません")
            self.append_log(
                "キャンセル要求: 撮影を停止し、PDF/OCR後処理を実行しません。撮影済み画像は残します。"
            )

    def on_capture_completed(self, result):
        if self._cancel_without_output:
            captured_count = int(result.get("pages", 0))
            image_dir = result.get("image_dir", "")
            self.append_log(f"キャンセル完了: {captured_count}画面")
            self.append_log("PDF/OCRは作成していません。")
            if image_dir:
                self.append_log(f"撮影済み画像: {image_dir}")
            self.status_label.setText(
                f"キャンセル済み — {captured_count}画面保存 / PDF・OCRなし"
            )
            self._cancel_without_output = False
            self.reset_buttons()
            self._sound()
            self._bring_notification_forward()
            QMessageBox.information(
                self,
                APP_NAME,
                f"キャプチャをキャンセルしました。\n\n"
                f"撮影済み: {captured_count}画面\n"
                "PDF・OCRは作成していません。\n"
                "途中までの画像はそのまま残しています。\n\n"
                f"{image_dir}",
            )
            return

        self.cancel_btn.setEnabled(False)
        if result.get("stop_reason") == "fixed-count-reached":
            result = dict(result)
            result["stop_reason"] = "固定枚数に到達"
        super().on_capture_completed(result)

    def reset_buttons(self):
        self._countdown_active = False
        self._countdown_token += 1
        super().reset_buttons()
        if hasattr(self, "cancel_btn"):
            self.cancel_btn.setEnabled(False)
        if hasattr(self, "capture_mode"):
            self._unlock_capture_mode_controls()

    def on_failed(self, detail):
        self._countdown_active = False
        self._countdown_token += 1
        super().on_failed(detail)


def run_self_test(output_path: str) -> int:
    report = {"ok": False, "checks": {}, "python": sys.version, "version": VERSION}
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        qt_app = QApplication.instance() or QApplication([])
        win = MainWindow()
        win.resize(900, 650)
        win.show()
        qt_app.processEvents()

        report["checks"]["mainwindow_constructed"] = bool(win.windowTitle())
        report["checks"]["version_in_title"] = VERSION in win.windowTitle()
        report["checks"]["f7_removed_from_help"] = "F7" not in win.hotkey_help.text()
        report["checks"]["start_delay_range"] = [
            win.start_delay_seconds.minimum(),
            win.start_delay_seconds.maximum(),
        ]
        report["checks"]["cancel_button_exists"] = "PDFを作らず" in win.cancel_btn.text()
        report["checks"]["fixed_mode_exists"] = win.capture_mode.findData("fixed") >= 0

        fixed_idx = win.capture_mode.findData("fixed")
        win.capture_mode.setCurrentIndex(fixed_idx)
        win.start_btn.setEnabled(True)
        win._sync_capture_mode_ui()
        report["checks"]["fixed_count_enabled"] = win.fixed_count.isEnabled()
        report["checks"]["auto_max_disabled_in_fixed"] = not win.max_pages.isEnabled()
        report["checks"]["same_stop_disabled_in_fixed"] = not win.same_limit.isEnabled()

        win._countdown_active = True
        win.cancel_btn.setEnabled(True)
        win.cancel_capture()
        report["checks"]["countdown_cancel_resets"] = (
            not win._countdown_active
            and win.start_btn.isEnabled()
            and not win.cancel_btn.isEnabled()
        )

        win.close()
        qt_app.processEvents()

        report["ok"] = all(
            [
                report["checks"]["mainwindow_constructed"],
                report["checks"]["version_in_title"],
                report["checks"]["f7_removed_from_help"],
                report["checks"]["start_delay_range"] == [3, 30],
                report["checks"]["cancel_button_exists"],
                report["checks"]["fixed_mode_exists"],
                report["checks"]["fixed_count_enabled"],
                report["checks"]["auto_max_disabled_in_fixed"],
                report["checks"]["same_stop_disabled_in_fixed"],
                report["checks"]["countdown_cancel_resets"],
            ]
        )
    except Exception:
        report["error"] = traceback.format_exc()

    Path(output_path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if report["ok"] else 1


def main():
    if "--self-test" in sys.argv:
        idx = sys.argv.index("--self-test")
        output_path = (
            sys.argv[idx + 1]
            if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("--")
            else str(Path.cwd() / "bookcapture-v058-selftest.json")
        )
        raise SystemExit(run_self_test(output_path))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    hotkey_filter = GlobalHotkeyFilter(win)
    app.installNativeEventFilter(hotkey_filter)
    win._hotkey_filter = hotkey_filter
    win.register_global_hotkeys()
    app.aboutToQuit.connect(win.unregister_global_hotkeys)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
