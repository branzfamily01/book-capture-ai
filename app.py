import sys
import os
import re
import time
import traceback
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QPoint, Signal, QThread, QSettings
from PySide6.QtGui import QPainter, QColor, QPen, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit,
    QFileDialog, QCheckBox, QProgressBar, QPlainTextEdit, QMessageBox,
    QGroupBox
)

from capture_engine import CaptureConfig, CaptureEngine, list_windows
from pdf_tools import build_outputs, sanitize_filename, tesseract_available


APP_NAME = "Book Capture AI"
ORG_NAME = "LocalTools"


def logical_rect_to_native_region(rect, screen_geometry, dpr):
    """Convert a Qt logical-pixel rectangle to Windows/native pixel coordinates."""
    rx, ry, rw, rh = rect
    sx, sy, sw, sh = screen_geometry
    return (
        int(round(sx + (rx - sx) * dpr)),
        int(round(sy + (ry - sy) * dpr)),
        max(1, int(round(rw * dpr))),
        max(1, int(round(rh * dpr))),
    )


class RegionSelector(QWidget):
    region_selected = Signal(tuple)

    def __init__(self):
        super().__init__()
        self.origin = QPoint()
        self.current = QPoint()
        self.dragging = False

        screens = QGuiApplication.screens()
        if not screens:
            geo = QGuiApplication.primaryScreen().geometry()
        else:
            left = min(s.geometry().left() for s in screens)
            top = min(s.geometry().top() for s in screens)
            right = max(s.geometry().right() for s in screens)
            bottom = max(s.geometry().bottom() for s in screens)
            geo = QRect(left, top, right - left + 1, bottom - top + 1)

        self.virtual_geo = geo
        self.setGeometry(geo)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setCursor(Qt.CrossCursor)

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.origin = event.position().toPoint()
            self.current = self.origin
            self.update()

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            self.current = event.position().toPoint()
            rect = QRect(self.origin, self.current).normalized()
            if rect.width() >= 50 and rect.height() >= 50:
                logical_x = rect.x() + self.virtual_geo.x()
                logical_y = rect.y() + self.virtual_geo.y()
                logical_rect = QRect(logical_x, logical_y, rect.width(), rect.height())

                screen = QGuiApplication.screenAt(logical_rect.center())
                if screen is not None:
                    sg = screen.geometry()
                    clipped = logical_rect.intersected(sg)
                    native = logical_rect_to_native_region(
                        (clipped.x(), clipped.y(), clipped.width(), clipped.height()),
                        (sg.x(), sg.y(), sg.width(), sg.height()),
                        float(screen.devicePixelRatio()),
                    )
                    self.region_selected.emit(native)
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 95))
        if self.dragging:
            rect = QRect(self.origin, self.current).normalized()
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(rect, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(0, 200, 255), 3))
            p.drawRect(rect)
            p.setPen(QColor(255, 255, 255))
            p.drawText(rect.adjusted(8, 8, -8, -8), Qt.AlignTop | Qt.AlignLeft,
                       f"{rect.width()} × {rect.height()}")


class CaptureThread(QThread):
    progress = Signal(int, str)
    log = Signal(str)
    status = Signal(str)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, config: CaptureConfig):
        super().__init__()
        self.config = config
        self.engine = CaptureEngine(config)
        self.engine.on_progress = lambda n, p: self.progress.emit(n, p)
        self.engine.on_log = lambda s: self.log.emit(s)
        self.engine.on_status = lambda s: self.status.emit(s)

    def run(self):
        try:
            result = self.engine.run()
            self.completed.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())

    def pause_capture(self):
        self.engine.pause()

    def resume_capture(self):
        self.engine.resume()

    def stop_capture(self):
        self.engine.stop()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.capture_thread = None
        self.region = (
            int(self.settings.value("region_x", 100)),
            int(self.settings.value("region_y", 100)),
            int(self.settings.value("region_w", 900)),
            int(self.settings.value("region_h", 1200)),
        )
        self._selector = None

        self.setWindowTitle(APP_NAME)
        self.resize(900, 760)
        self.build_ui()
        self.refresh_windows()
        self.load_settings()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setSpacing(12)

        title = QLabel("Book Capture AI")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")
        subtitle = QLabel("画面表示できる電子書籍を、自動ページ送り → キャプチャ → PDF/OCR化")
        subtitle.setStyleSheet("color: #666;")
        main.addWidget(title)
        main.addWidget(subtitle)

        target_box = QGroupBox("1. 対象")
        target_layout = QGridLayout(target_box)
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(420)
        refresh_btn = QPushButton("ウィンドウ更新")
        refresh_btn.clicked.connect(self.refresh_windows)
        target_layout.addWidget(QLabel("電子書籍ウィンドウ"), 0, 0)
        target_layout.addWidget(self.window_combo, 0, 1)
        target_layout.addWidget(refresh_btn, 0, 2)

        self.region_label = QLabel()
        self.update_region_label()
        region_btn = QPushButton("画面範囲を選択")
        region_btn.clicked.connect(self.select_region)
        target_layout.addWidget(QLabel("キャプチャ範囲"), 1, 0)
        target_layout.addWidget(self.region_label, 1, 1)
        target_layout.addWidget(region_btn, 1, 2)

        self.book_title = QLineEdit()
        self.book_title.setPlaceholderText("例：英語学習の本")
        target_layout.addWidget(QLabel("保存名"), 2, 0)
        target_layout.addWidget(self.book_title, 2, 1, 1, 2)
        main.addWidget(target_box)

        capture_box = QGroupBox("2. キャプチャ設定")
        cap = QGridLayout(capture_box)
        self.turn_key = QComboBox()
        self.turn_key.addItem("← 左キー", "left")
        self.turn_key.addItem("→ 右キー", "right")
        self.turn_key.addItem("Space", "space")
        self.turn_key.addItem("PageDown", "pagedown")
        cap.addWidget(QLabel("ページ送り"), 0, 0)
        cap.addWidget(self.turn_key, 0, 1)

        self.max_pages = QSpinBox()
        self.max_pages.setRange(1, 5000)
        self.max_pages.setValue(500)
        cap.addWidget(QLabel("最大ページ数"), 0, 2)
        cap.addWidget(self.max_pages, 0, 3)

        self.settle_delay = QDoubleSpinBox()
        self.settle_delay.setRange(0.15, 3.0)
        self.settle_delay.setSingleStep(0.05)
        self.settle_delay.setValue(0.45)
        self.settle_delay.setSuffix(" 秒")
        cap.addWidget(QLabel("表示安定待ち"), 1, 0)
        cap.addWidget(self.settle_delay, 1, 1)

        self.change_timeout = QDoubleSpinBox()
        self.change_timeout.setRange(0.5, 10.0)
        self.change_timeout.setSingleStep(0.25)
        self.change_timeout.setValue(2.5)
        self.change_timeout.setSuffix(" 秒")
        cap.addWidget(QLabel("ページ変化待ち"), 1, 2)
        cap.addWidget(self.change_timeout, 1, 3)

        self.diff_threshold = QDoubleSpinBox()
        self.diff_threshold.setRange(0.1, 30.0)
        self.diff_threshold.setSingleStep(0.2)
        self.diff_threshold.setValue(1.8)
        cap.addWidget(QLabel("Smart Guard感度"), 2, 0)
        cap.addWidget(self.diff_threshold, 2, 1)

        self.same_limit = QSpinBox()
        self.same_limit.setRange(3, 10)
        self.same_limit.setValue(3)
        cap.addWidget(QLabel("同一画面で自動停止"), 2, 2)
        cap.addWidget(self.same_limit, 2, 3)

        main.addWidget(capture_box)

        output_box = QGroupBox("3. 出力")
        out = QGridLayout(output_box)
        self.output_dir = QLineEdit(str(Path.home() / "Downloads"))
        choose_dir = QPushButton("選択")
        choose_dir.clicked.connect(self.choose_output)
        out.addWidget(QLabel("保存先"), 0, 0)
        out.addWidget(self.output_dir, 0, 1)
        out.addWidget(choose_dir, 0, 2)

        self.make_pdf = QCheckBox("画像PDFを作る")
        self.make_pdf.setChecked(True)
        self.make_ocr_pdf = QCheckBox("検索可能PDFを作る（Tesseract必要）")
        self.make_txt = QCheckBox("OCRテキスト(.txt)を作る")
        self.ocr_lang = QLineEdit("jpn+eng")
        self.ocr_lang.setMaximumWidth(120)

        out.addWidget(self.make_pdf, 1, 0, 1, 2)
        out.addWidget(self.make_ocr_pdf, 2, 0, 1, 2)
        out.addWidget(self.make_txt, 3, 0, 1, 2)
        out.addWidget(QLabel("OCR言語"), 2, 2)
        out.addWidget(self.ocr_lang, 3, 2)
        main.addWidget(output_box)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("キャプチャ開始")
        self.start_btn.setMinimumHeight(48)
        self.start_btn.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.pause_btn = QPushButton("一時停止")
        self.resume_btn = QPushButton("再開")
        self.stop_btn = QPushButton("終了してPDF作成")
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_capture)
        self.pause_btn.clicked.connect(self.pause_capture)
        self.resume_btn.clicked.connect(self.resume_capture)
        self.stop_btn.clicked.connect(self.stop_capture)
        buttons.addWidget(self.start_btn, 2)
        buttons.addWidget(self.pause_btn)
        buttons.addWidget(self.resume_btn)
        buttons.addWidget(self.stop_btn, 2)
        main.addLayout(buttons)

        self.status_label = QLabel("待機中")
        self.status_label.setStyleSheet("font-weight: 600;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(1000)
        self.log_box.setMinimumHeight(160)
        main.addWidget(self.status_label)
        main.addWidget(self.progress)
        main.addWidget(self.log_box)

        note = QLabel(
            "※ DRM解除やキャプチャ防止の回避は行いません。"
            "画面に通常表示できるコンテンツを、権利・利用規約の範囲で使用してください。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #777; font-size: 12px;")
        main.addWidget(note)

    def refresh_windows(self):
        current = self.window_combo.currentText()
        self.window_combo.clear()
        titles = list_windows(exclude_contains=[APP_NAME])
        self.window_combo.addItems(titles)
        if current:
            idx = self.window_combo.findText(current)
            if idx >= 0:
                self.window_combo.setCurrentIndex(idx)

    def update_region_label(self):
        x, y, w, h = self.region
        self.region_label.setText(f"x={x}, y={y}, {w}×{h} px")

    def select_region(self):
        self.hide()
        QApplication.processEvents()
        time.sleep(0.2)
        self._selector = RegionSelector()
        self._selector.region_selected.connect(self.region_chosen)
        self._selector.destroyed.connect(self.show)
        self._selector.show()

    def region_chosen(self, region):
        self.region = region
        self.update_region_label()
        self.show()
        self.raise_()

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "保存先を選択", self.output_dir.text())
        if path:
            self.output_dir.setText(path)

    def load_settings(self):
        def set_combo_by_data(combo, value):
            for i in range(combo.count()):
                if combo.itemData(i) == value:
                    combo.setCurrentIndex(i)
                    return

        set_combo_by_data(self.turn_key, self.settings.value("turn_key", "left"))
        self.max_pages.setValue(int(self.settings.value("max_pages", 500)))
        self.settle_delay.setValue(float(self.settings.value("settle_delay", 0.45)))
        self.change_timeout.setValue(float(self.settings.value("change_timeout", 2.5)))
        self.diff_threshold.setValue(float(self.settings.value("diff_threshold", 1.8)))
        self.same_limit.setValue(max(3, int(self.settings.value("same_limit", 3))))
        self.output_dir.setText(self.settings.value("output_dir", str(Path.home() / "Downloads")))
        self.ocr_lang.setText(self.settings.value("ocr_lang", "jpn+eng"))
        self.make_ocr_pdf.setChecked(self.settings.value("ocr_pdf", False, type=bool))
        self.make_txt.setChecked(self.settings.value("ocr_txt", False, type=bool))

    def save_settings(self):
        x, y, w, h = self.region
        self.settings.setValue("region_x", x)
        self.settings.setValue("region_y", y)
        self.settings.setValue("region_w", w)
        self.settings.setValue("region_h", h)
        self.settings.setValue("turn_key", self.turn_key.currentData())
        self.settings.setValue("max_pages", self.max_pages.value())
        self.settings.setValue("settle_delay", self.settle_delay.value())
        self.settings.setValue("change_timeout", self.change_timeout.value())
        self.settings.setValue("diff_threshold", self.diff_threshold.value())
        self.settings.setValue("same_limit", self.same_limit.value())
        self.settings.setValue("output_dir", self.output_dir.text())
        self.settings.setValue("ocr_lang", self.ocr_lang.text())
        self.settings.setValue("ocr_pdf", self.make_ocr_pdf.isChecked())
        self.settings.setValue("ocr_txt", self.make_txt.isChecked())

    def start_capture(self):
        if self.capture_thread and self.capture_thread.isRunning():
            return
        title = self.window_combo.currentText().strip()
        if not title:
            QMessageBox.warning(self, APP_NAME, "電子書籍ウィンドウを選択してください。")
            return
        out_root = Path(self.output_dir.text()).expanduser()
        out_root.mkdir(parents=True, exist_ok=True)

        book_name = sanitize_filename(self.book_title.text().strip() or "captured-book")
        session = time.strftime("%Y%m%d-%H%M%S")
        session_dir = out_root / f"{book_name}-{session}"

        config = CaptureConfig(
            window_title=title,
            region=self.region,
            turn_key=self.turn_key.currentData(),
            max_pages=self.max_pages.value(),
            settle_delay=self.settle_delay.value(),
            change_timeout=self.change_timeout.value(),
            diff_threshold=self.diff_threshold.value(),
            same_limit=self.same_limit.value(),
            output_dir=session_dir,
        )
        self.save_settings()
        self.log_box.clear()
        self.progress.setRange(0, self.max_pages.value())
        self.progress.setValue(0)
        self.status_label.setText("開始準備中…")
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)

        self.capture_thread = CaptureThread(config)
        self.capture_thread.progress.connect(self.on_progress)
        self.capture_thread.log.connect(self.append_log)
        self.capture_thread.status.connect(self.status_label.setText)
        self.capture_thread.failed.connect(self.on_failed)
        self.capture_thread.completed.connect(self.on_capture_completed)
        self.capture_thread.start()

    def on_progress(self, n, path):
        self.progress.setValue(n)
        self.status_label.setText(f"{n} ページ取得済み")
        self.append_log(f"保存: {Path(path).name}")

    def append_log(self, text):
        self.log_box.appendPlainText(text)

    def pause_capture(self):
        if self.capture_thread:
            self.capture_thread.pause_capture()
            self.pause_btn.setEnabled(False)
            self.resume_btn.setEnabled(True)
            self.status_label.setText("一時停止中")

    def resume_capture(self):
        if self.capture_thread:
            self.capture_thread.resume_capture()
            self.pause_btn.setEnabled(True)
            self.resume_btn.setEnabled(False)
            self.status_label.setText("再開中…")

    def stop_capture(self):
        if self.capture_thread:
            self.capture_thread.stop_capture()
            self.stop_btn.setEnabled(False)
            self.status_label.setText("終了処理中…")

    def on_capture_completed(self, result):
        self.append_log(f"キャプチャ完了: {result['pages']}ページ")
        self.status_label.setText("PDF/OCR生成中…")
        try:
            wants_ocr = self.make_ocr_pdf.isChecked() or self.make_txt.isChecked()
            ocr_ready = tesseract_available() if wants_ocr else True
            if wants_ocr and not ocr_ready:
                self.append_log(
                    "OCRはスキップしました: Tesseract OCRが見つかりません。画像PDFは作成します。"
                )

            outputs = build_outputs(
                image_dir=Path(result["image_dir"]),
                base_name=sanitize_filename(self.book_title.text().strip() or "captured-book"),
                make_image_pdf=self.make_pdf.isChecked(),
                make_ocr_pdf=self.make_ocr_pdf.isChecked() and ocr_ready,
                make_txt=self.make_txt.isChecked() and ocr_ready,
                ocr_lang=self.ocr_lang.text().strip() or "jpn+eng",
            )
            for item in outputs:
                self.append_log(f"出力: {item}")
            self.status_label.setText(f"完了 — {result['pages']}ページ")
            QMessageBox.information(
                self, APP_NAME,
                "処理が完了しました。\n\n" + "\n".join(str(x) for x in outputs)
            )
        except Exception:
            self.on_failed(traceback.format_exc())
        finally:
            self.reset_buttons()

    def on_failed(self, detail):
        self.append_log(detail)
        self.status_label.setText("エラー")
        QMessageBox.critical(self, APP_NAME, "処理中にエラーが発生しました。\n\nログを確認してください。")
        self.reset_buttons()

    def reset_buttons(self):
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

    def closeEvent(self, event):
        if self.capture_thread and self.capture_thread.isRunning():
            self.capture_thread.stop_capture()
            self.capture_thread.wait(2500)
        self.save_settings()
        event.accept()


def run_self_test(output_path: str) -> int:
    """Exercise bundled dependencies, PDF generation and a headless Qt window."""
    import json
    import tempfile
    from PIL import Image
    from pypdf import PdfReader

    report = {
        "ok": False,
        "checks": {},
        "python": sys.version,
    }

    try:
        with tempfile.TemporaryDirectory(prefix="bookcapture-selftest-") as td:
            base = Path(td)
            image_dir = base / "images"
            image_dir.mkdir()

            for i in range(1, 13):
                img = Image.new("RGB", (900, 1200), "white")
                img.save(image_dir / f"page-{i:04d}.png", "PNG")

            outputs = build_outputs(
                image_dir=image_dir,
                base_name="self-test",
                make_image_pdf=True,
                make_ocr_pdf=False,
                make_txt=False,
            )
            pdf_file = base / "self-test.pdf"
            reader = PdfReader(str(pdf_file))
            report["checks"]["pdf_exists"] = pdf_file.exists()
            report["checks"]["pdf_pages"] = len(reader.pages)
            report["checks"]["window_enumeration"] = isinstance(list_windows(), list)

            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            qt_app = QApplication.instance() or QApplication([])
            win = MainWindow()
            report["checks"]["mainwindow_constructed"] = bool(win.windowTitle())
            win.close()
            qt_app.processEvents()

            report["ok"] = (
                report["checks"]["pdf_exists"]
                and report["checks"]["pdf_pages"] == 12
                and report["checks"]["window_enumeration"]
                and report["checks"]["mainwindow_constructed"]
            )
            report["outputs"] = [str(x) for x in outputs]

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
            else str(Path.cwd() / "bookcapture-selftest.json")
        )
        raise SystemExit(run_self_test(output_path))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
