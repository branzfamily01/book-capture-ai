from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME
from app_v4 import MainWindow as V4MainWindow
from capture_engine import list_windows
from pdf_tools import build_outputs, configure_tesseract, make_image_pdf, make_ocr_outputs, sanitize_filename
from split_tools import split_spreads, trim_pages


VERSION = "0.5.2"


class PostProcessThread(QThread):
    """Perform all post-capture disk, PDF and OCR work off the GUI thread."""
    progress = Signal(int, int, str)
    log = Signal(str)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

    def _progress(self, current, total, message):
        self.progress.emit(current, max(total, 1), message)

    def run(self):
        try:
            c = self.config
            source = Path(c["image_dir"])
            processing = source
            count = c["captured_count"]
            if c["split_spreads"]:
                self._progress(0, count, f"分割中 0/{count}画面")
                processing = source.parent / "images-split"
                pages = split_spreads(
                    source, processing, order=c["split_order"],
                    top_crop_pct=c["top_crop"], bottom_crop_pct=c["bottom_crop"],
                    progress=lambda n, total, _: self._progress(n, total, f"分割中 {n}/{total}画面"),
                )
                self.log.emit(f"見開き処理: {count}画面 → {len(pages)}ページ / {c['split_order_label']}")
            elif c["top_crop"] > 0 or c["bottom_crop"] > 0:
                self._progress(0, count, f"トリミング中 0/{count}ページ")
                processing = source.parent / "images-clean"
                trim_pages(
                    source, processing, top_crop_pct=c["top_crop"],
                    bottom_crop_pct=c["bottom_crop"],
                    progress=lambda n, total, _: self._progress(n, total, f"トリミング中 {n}/{total}ページ"),
                )

            page_count = len(list(processing.glob("page-*.png")))
            outputs = []
            if c["make_image_pdf"]:
                image_pdf = source.parent / f"{c['base_name']}.pdf"
                self._progress(0, page_count, "PDF生成中")
                make_image_pdf(processing, image_pdf, progress=self._progress)
                outputs.append(image_pdf)

            ocr_error = None
            if c["make_ocr_pdf"] or c["make_txt"]:
                info = configure_tesseract()
                self.log.emit("OCR診断: " + json.dumps(info, ensure_ascii=False))
                try:
                    if not info["available"]:
                        raise RuntimeError("内蔵Tesseractを起動できません: " + info.get("error", "原因不明"))
                    missing = sorted(set(filter(None, c["ocr_lang"].split("+"))) - set(info["languages"]))
                    if missing:
                        raise RuntimeError("OCR言語データがありません: " + ", ".join(missing))
                    searchable = source.parent / f"{c['base_name']}-searchable.pdf" if c["make_ocr_pdf"] else None
                    text_file = source.parent / f"{c['base_name']}.txt" if c["make_txt"] else None
                    def report_ocr(n, total, message):
                        self._progress(n, total, "検索可能PDF生成中" if n >= total and searchable else message)
                    make_ocr_outputs(processing, searchable, text_file, c["ocr_lang"], progress=report_ocr)
                    outputs.extend(x for x in (searchable, text_file) if x)
                except Exception as exc:
                    ocr_error = str(exc)
                    self.log.emit("OCR失敗: " + traceback.format_exc())

            outputs.extend(x for x in (processing, source) if x not in outputs)
            self._progress(page_count, page_count, "完了")
            self.completed.emit({"outputs": [str(x) for x in outputs], "pages": page_count,
                                 "ocr_error": ocr_error, "top_crop": c["top_crop"],
                                 "bottom_crop": c["bottom_crop"]})
        except Exception:
            self.failed.emit(traceback.format_exc())


class MainWindow(V4MainWindow):
    def build_ui(self):
        super().build_ui()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")

        main = self.centralWidget().layout()
        clean_box = QGroupBox("3.6 KindleクリーンPDF（ヘッダー・フッター除去）")
        grid = QGridLayout(clean_box)

        self.trim_header = QCheckBox("上部のKindleヘッダーを除去")
        self.trim_header.setChecked(True)
        self.header_pct = QDoubleSpinBox()
        self.header_pct.setRange(0.0, 30.0)
        self.header_pct.setDecimals(1)
        self.header_pct.setSingleStep(0.5)
        self.header_pct.setSuffix(" %")
        self.header_pct.setValue(8.0)

        self.trim_footer = QCheckBox("下部のKindleフッターを除去")
        self.trim_footer.setChecked(True)
        self.footer_pct = QDoubleSpinBox()
        self.footer_pct.setRange(0.0, 30.0)
        self.footer_pct.setDecimals(1)
        self.footer_pct.setSingleStep(0.5)
        self.footer_pct.setSuffix(" %")
        self.footer_pct.setValue(6.0)

        explain = QLabel(
            "添付テストPDFで残っていた上部の書名表示と、下部の『読書速度を学習中…／位置表示』を"
            "PDF・OCRの前に切り落とします。元のスクリーンショットは images/ に必ず残ります。"
        )
        explain.setWordWrap(True)
        explain.setStyleSheet("color: #666;")

        grid.addWidget(self.trim_header, 0, 0)
        grid.addWidget(self.header_pct, 0, 1)
        grid.addWidget(self.trim_footer, 1, 0)
        grid.addWidget(self.footer_pct, 1, 1)
        grid.addWidget(explain, 2, 0, 1, 2)

        # Insert after v0.4 spread-split settings and before fixed controls.
        main.insertWidget(6, clean_box)
        self._make_layout_responsive()

    def _make_layout_responsive(self):
        """Scroll settings while keeping capture controls permanently visible."""
        main = self.centralWidget().layout()

        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(10)

        # At this point indices 0..6 are title/subtitle/target/capture/output/split/clean.
        # Move only those into the scroll area. The button row, progress and log stay fixed.
        for _ in range(7):
            item = main.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                settings_layout.addWidget(widget)

        settings_layout.addStretch(1)

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setObjectName("settingsScroll")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settings_scroll.setWidget(settings_widget)
        main.insertWidget(0, self.settings_scroll, 1)

        # Preserve enough room for the fixed controls on short laptop displays.
        self.log_box.setMinimumHeight(80)
        self.log_box.setMaximumHeight(110)
        self.resize(960, 760)
        self.setMinimumSize(700, 540)

    def load_settings(self):
        super().load_settings()
        self.trim_header.setChecked(self.settings.value("trim_header", True, type=bool))
        self.trim_footer.setChecked(self.settings.value("trim_footer", True, type=bool))
        self.header_pct.setValue(float(self.settings.value("header_pct", 8.0)))
        self.footer_pct.setValue(float(self.settings.value("footer_pct", 6.0)))

    def save_settings(self):
        super().save_settings()
        if hasattr(self, "trim_header"):
            self.settings.setValue("trim_header", self.trim_header.isChecked())
            self.settings.setValue("trim_footer", self.trim_footer.isChecked())
            self.settings.setValue("header_pct", self.header_pct.value())
            self.settings.setValue("footer_pct", self.footer_pct.value())

    def _crop_values(self):
        top = self.header_pct.value() if self.trim_header.isChecked() else 0.0
        bottom = self.footer_pct.value() if self.trim_footer.isChecked() else 0.0
        return top, bottom

    def _legacy_on_capture_completed(self, result):
        captured_count = int(result["pages"])
        self.append_log(f"キャプチャ完了: {captured_count}画面")
        self.status_label.setText("クリーン処理 / PDF / OCR生成中…")

        try:
            source_dir = Path(result["image_dir"])
            processing_dir = source_dir
            output_page_count = captured_count
            top_crop, bottom_crop = self._crop_values()

            if self.split_spreads.isChecked():
                split_dir = source_dir.parent / "images-split"
                self.status_label.setText("ヘッダー・フッター除去 → 見開き分割中…")
                split_files = split_spreads(
                    source_dir,
                    split_dir,
                    order=self.split_order.currentData() or "rtl",
                    top_crop_pct=top_crop,
                    bottom_crop_pct=bottom_crop,
                )
                processing_dir = split_dir
                output_page_count = len(split_files)
                self.append_log(
                    f"見開き処理: {captured_count}画面 → {output_page_count}ページ / "
                    f"上{top_crop:.1f}% 下{bottom_crop:.1f}% / {self.split_order.currentText()}"
                )
                self.append_log(f"元画像: {source_dir}")
                self.append_log(f"クリーン分割画像: {split_dir}")
            elif top_crop > 0 or bottom_crop > 0:
                clean_dir = source_dir.parent / "images-clean"
                self.status_label.setText("ヘッダー・フッター除去中…")
                clean_files = trim_pages(
                    source_dir,
                    clean_dir,
                    top_crop_pct=top_crop,
                    bottom_crop_pct=bottom_crop,
                )
                processing_dir = clean_dir
                output_page_count = len(clean_files)
                self.append_log(
                    f"1ページクリーン処理: 上{top_crop:.1f}% 下{bottom_crop:.1f}%"
                )
                self.append_log(f"元画像: {source_dir}")
                self.append_log(f"クリーン画像: {clean_dir}")

            wants_ocr = self.make_ocr_pdf.isChecked() or self.make_txt.isChecked()
            ocr_ready = tesseract_available() if wants_ocr else True
            if wants_ocr and not ocr_ready:
                self.append_log("OCRはスキップしました: 内蔵OCRを起動できません。画像PDFは作成します。")

            outputs = build_outputs(
                image_dir=processing_dir,
                base_name=sanitize_filename(self.book_title.text().strip() or "captured-book"),
                make_image_pdf=self.make_pdf.isChecked(),
                make_ocr_pdf=self.make_ocr_pdf.isChecked() and ocr_ready,
                make_txt=self.make_txt.isChecked() and ocr_ready,
                ocr_lang=self.ocr_lang.text().strip() or "jpn+eng",
            )

            if source_dir not in outputs:
                outputs.append(source_dir)
            for item in outputs:
                self.append_log(f"出力: {item}")

            self.status_label.setText(f"完了 — {output_page_count}ページ")
            QMessageBox.information(
                self,
                APP_NAME,
                f"処理が完了しました。\n\n完成ページ数: {output_page_count}\n"
                f"上トリミング: {top_crop:.1f}% / 下トリミング: {bottom_crop:.1f}%\n\n"
                + "\n".join(str(x) for x in outputs),
            )
        except Exception:
            self.on_failed(traceback.format_exc())
        finally:
            self.reset_buttons()

    def on_capture_completed(self, result):
        """Snapshot UI options and immediately hand expensive work to QThread."""
        captured_count = int(result["pages"])
        self.append_log(f"キャプチャ完了: {captured_count}画面")
        top_crop, bottom_crop = self._crop_values()
        self.progress.setRange(0, max(captured_count, 1))
        self.progress.setValue(0)
        self.status_label.setText("後処理を開始中…")
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        config = {
            "image_dir": result["image_dir"], "captured_count": captured_count,
            "top_crop": top_crop, "bottom_crop": bottom_crop,
            "split_spreads": self.split_spreads.isChecked(),
            "split_order": self.split_order.currentData() or "rtl",
            "split_order_label": self.split_order.currentText(),
            "base_name": sanitize_filename(self.book_title.text().strip() or "captured-book"),
            "make_image_pdf": self.make_pdf.isChecked(),
            "make_ocr_pdf": self.make_ocr_pdf.isChecked(),
            "make_txt": self.make_txt.isChecked(),
            "ocr_lang": self.ocr_lang.text().strip() or "jpn+eng",
        }
        self.post_process_thread = PostProcessThread(config, self)
        self.post_process_thread.progress.connect(self.on_post_process_progress)
        self.post_process_thread.log.connect(self.append_log)
        self.post_process_thread.failed.connect(self.on_post_process_failed)
        self.post_process_thread.completed.connect(self.on_post_process_completed)
        self.post_process_thread.start()

    def on_post_process_progress(self, current, total, message):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        self.status_label.setText(message)

    def on_post_process_failed(self, detail):
        self.post_process_thread = None
        self.on_failed(detail)

    def on_post_process_completed(self, result):
        self.post_process_thread = None
        for item in result["outputs"]:
            self.append_log(f"出力: {item}")
        self.status_label.setText(f"完了 — {result['pages']}ページ")
        self.reset_buttons()
        message = (
            f"処理が完了しました。\n\n完成ページ数: {result['pages']}\n"
            f"上トリミング: {result['top_crop']:.1f}% / 下トリミング: {result['bottom_crop']:.1f}%\n\n"
            + "\n".join(result["outputs"])
        )
        if result["ocr_error"]:
            message += "\n\nOCRだけ失敗しました。画像PDFと元画像は保存済みです。\n理由: " + result["ocr_error"]
            QMessageBox.warning(self, APP_NAME, message)
        else:
            QMessageBox.information(self, APP_NAME, message)

    def closeEvent(self, event):
        worker = getattr(self, "post_process_thread", None)
        if worker and worker.isRunning():
            QMessageBox.information(self, APP_NAME, "後処理中です。完了してから閉じてください。")
            event.ignore()
            return
        super().closeEvent(event)


def run_self_test(output_path: str) -> int:
    from PIL import Image
    from pypdf import PdfReader

    report = {"ok": False, "checks": {}, "python": sys.version, "version": VERSION}
    try:
        with tempfile.TemporaryDirectory(prefix="bookcapture-v5-selftest-") as td:
            base = Path(td)
            spreads = base / "images"
            spreads.mkdir()

            # Synthetic 1000x1000 spreads: red top 8%, blue bottom 6%, white body.
            for i in range(1, 7):
                img = Image.new("RGB", (1000, 1000), "white")
                for y in range(0, 80):
                    for x in range(1000):
                        img.putpixel((x, y), (255, 0, 0))
                for y in range(940, 1000):
                    for x in range(1000):
                        img.putpixel((x, y), (0, 0, 255))
                img.save(spreads / f"page-{i:04d}.png", "PNG")

            split_dir = base / "images-split"
            split_files = split_spreads(
                spreads, split_dir, order="rtl", top_crop_pct=8.0, bottom_crop_pct=6.0
            )
            report["checks"]["split_pages"] = len(split_files)
            with Image.open(split_files[0]) as first:
                report["checks"]["clean_size"] = list(first.size)
                report["checks"]["top_pixel"] = list(first.getpixel((10, 0)))
                report["checks"]["bottom_pixel"] = list(first.getpixel((10, first.height - 1)))

            outputs = build_outputs(
                image_dir=split_dir,
                base_name="self-test",
                make_image_pdf=True,
                make_ocr_pdf=False,
                make_txt=False,
            )
            pdf_file = base / "self-test.pdf"
            report["checks"]["pdf_exists"] = pdf_file.exists()
            report["checks"]["pdf_pages"] = len(PdfReader(str(pdf_file)).pages)
            report["checks"]["window_enumeration"] = isinstance(list_windows(), list)

            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            qt_app = QApplication.instance() or QApplication([])
            win = MainWindow()
            win.resize(900, 600)
            win.show()
            qt_app.processEvents()

            start_bottom = win.start_btn.mapTo(
                win.centralWidget(), QPoint(0, win.start_btn.height())
            ).y()
            report["checks"]["mainwindow_constructed"] = bool(win.windowTitle())
            report["checks"]["split_default_on"] = win.split_spreads.isChecked()
            report["checks"]["header_default"] = win.trim_header.isChecked()
            report["checks"]["footer_default"] = win.trim_footer.isChecked()
            report["checks"]["header_pct"] = win.header_pct.value()
            report["checks"]["footer_pct"] = win.footer_pct.value()
            report["checks"]["settings_scroll_exists"] = win.settings_scroll is not None
            report["checks"]["start_button_visible_600px"] = (
                win.start_btn.isVisible()
                and start_bottom <= win.centralWidget().height()
            )
            report["checks"]["central_height"] = win.centralWidget().height()
            report["checks"]["start_button_bottom"] = start_bottom
            win.close()
            qt_app.processEvents()

            report["ok"] = (
                report["checks"]["split_pages"] == 12
                and report["checks"]["clean_size"] == [500, 860]
                and report["checks"]["top_pixel"] == [255, 255, 255]
                and report["checks"]["bottom_pixel"] == [255, 255, 255]
                and report["checks"]["pdf_exists"]
                and report["checks"]["pdf_pages"] == 12
                and report["checks"]["window_enumeration"]
                and report["checks"]["mainwindow_constructed"]
                and report["checks"]["split_default_on"]
                and report["checks"]["header_default"]
                and report["checks"]["footer_default"]
                and report["checks"]["header_pct"] == 8.0
                and report["checks"]["footer_pct"] == 6.0
                and report["checks"]["settings_scroll_exists"]
                and report["checks"]["start_button_visible_600px"]
            )
            report["outputs"] = [str(x) for x in outputs]
    except Exception:
        report["error"] = traceback.format_exc()

    Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def main():
    if "--self-test" in sys.argv:
        idx = sys.argv.index("--self-test")
        output_path = (
            sys.argv[idx + 1]
            if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("--")
            else str(Path.cwd() / "bookcapture-v5-selftest.json")
        )
        raise SystemExit(run_self_test(output_path))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
