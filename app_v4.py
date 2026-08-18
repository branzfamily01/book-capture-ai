from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QGridLayout, QGroupBox, QLabel, QMessageBox

from app import APP_NAME, MainWindow as BaseMainWindow, logical_rect_to_native_region
from capture_engine import list_windows
from pdf_tools import build_outputs, sanitize_filename, tesseract_available
from split_tools import split_spreads


VERSION = "0.4"


class MainWindow(BaseMainWindow):
    def build_ui(self):
        super().build_ui()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.resize(940, 860)

        main = self.centralWidget().layout()
        split_box = QGroupBox("3.5 見開き自動分割")
        grid = QGridLayout(split_box)

        self.split_spreads = QCheckBox("見開きを中央から2ページに自動分割する")
        self.split_spreads.setChecked(True)
        self.split_order = QComboBox()
        self.split_order.addItem("右 → 左（日本語の本向け）", "rtl")
        self.split_order.addItem("左 → 右（横書き洋書など）", "ltr")
        explain = QLabel(
            "元の見開き画像は images/ に残し、分割後の1ページ画像を images-split/ に保存します。\n"
            "PDF・検索可能PDF・OCRテキストは、分割後の画像を使って作成します。"
        )
        explain.setWordWrap(True)
        explain.setStyleSheet("color: #666;")

        grid.addWidget(self.split_spreads, 0, 0, 1, 2)
        grid.addWidget(QLabel("ページ順"), 1, 0)
        grid.addWidget(self.split_order, 1, 1)
        grid.addWidget(explain, 2, 0, 1, 2)

        # Base UI index 5 is immediately before the control-button row.
        main.insertWidget(5, split_box)
        self.make_ocr_pdf.setText("検索可能PDFを作る（OCR内蔵）")

    def load_settings(self):
        super().load_settings()
        self.split_spreads.setChecked(self.settings.value("split_spreads", True, type=bool))
        wanted = self.settings.value("split_order", "rtl")
        idx = self.split_order.findData(wanted)
        if idx >= 0:
            self.split_order.setCurrentIndex(idx)

    def save_settings(self):
        super().save_settings()
        if hasattr(self, "split_spreads"):
            self.settings.setValue("split_spreads", self.split_spreads.isChecked())
            self.settings.setValue("split_order", self.split_order.currentData())

    def on_capture_completed(self, result):
        spread_count = int(result["pages"])
        self.append_log(f"キャプチャ完了: {spread_count}画面")
        self.status_label.setText("PDF/OCR生成中…")

        try:
            source_dir = Path(result["image_dir"])
            processing_dir = source_dir
            output_page_count = spread_count

            if self.split_spreads.isChecked():
                split_dir = source_dir.parent / "images-split"
                self.status_label.setText("見開きを1ページずつに分割中…")
                split_files = split_spreads(
                    source_dir,
                    split_dir,
                    order=self.split_order.currentData() or "rtl",
                )
                processing_dir = split_dir
                output_page_count = len(split_files)
                self.append_log(
                    f"見開き分割: {spread_count}画面 → {output_page_count}ページ "
                    f"({self.split_order.currentText()})"
                )
                self.append_log(f"元画像: {source_dir}")
                self.append_log(f"分割画像: {split_dir}")

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

            # Always surface the original spreads when split mode is active.
            if self.split_spreads.isChecked() and source_dir not in outputs:
                outputs.append(source_dir)

            for item in outputs:
                self.append_log(f"出力: {item}")

            self.status_label.setText(f"完了 — {output_page_count}ページ")
            QMessageBox.information(
                self,
                APP_NAME,
                f"処理が完了しました。\n\n完成ページ数: {output_page_count}\n\n"
                + "\n".join(str(x) for x in outputs),
            )
        except Exception:
            self.on_failed(traceback.format_exc())
        finally:
            self.reset_buttons()


def run_self_test(output_path: str) -> int:
    """Packaged EXE self-test including spread split and normal PDF output."""
    from PIL import Image
    from pypdf import PdfReader

    report = {"ok": False, "checks": {}, "python": sys.version, "version": VERSION}
    try:
        with tempfile.TemporaryDirectory(prefix="bookcapture-v4-selftest-") as td:
            base = Path(td)
            spreads = base / "images"
            spreads.mkdir()

            # 6 synthetic spreads -> 12 final pages. Odd width tests midpoint safety.
            for i in range(1, 7):
                img = Image.new("RGB", (901, 600), "white")
                img.save(spreads / f"page-{i:04d}.png", "PNG")

            split_dir = base / "images-split"
            split_files = split_spreads(spreads, split_dir, order="rtl")
            report["checks"]["split_pages"] = len(split_files)
            report["checks"]["split_first_size"] = list(Image.open(split_files[0]).size)
            report["checks"]["split_second_size"] = list(Image.open(split_files[1]).size)

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
            report["checks"]["mainwindow_constructed"] = bool(win.windowTitle())
            report["checks"]["split_default_on"] = win.split_spreads.isChecked()
            report["checks"]["split_default_order"] = win.split_order.currentData()
            win.close()
            qt_app.processEvents()

            report["ok"] = (
                report["checks"]["split_pages"] == 12
                and report["checks"]["split_first_size"] == [451, 600]
                and report["checks"]["split_second_size"] == [450, 600]
                and report["checks"]["pdf_exists"]
                and report["checks"]["pdf_pages"] == 12
                and report["checks"]["window_enumeration"]
                and report["checks"]["mainwindow_constructed"]
                and report["checks"]["split_default_on"]
                and report["checks"]["split_default_order"] == "rtl"
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
            else str(Path.cwd() / "bookcapture-v4-selftest.json")
        )
        raise SystemExit(run_self_test(output_path))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
