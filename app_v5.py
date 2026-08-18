from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtWidgets import QApplication, QCheckBox, QDoubleSpinBox, QGridLayout, QGroupBox, QLabel, QMessageBox

from app import APP_NAME
from app_v4 import MainWindow as V4MainWindow
from capture_engine import list_windows
from pdf_tools import build_outputs, sanitize_filename, tesseract_available
from split_tools import split_spreads, trim_pages


VERSION = "0.5"


class MainWindow(V4MainWindow):
    def build_ui(self):
        super().build_ui()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.resize(960, 930)

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

        # Insert after v0.4 spread-split settings and before control buttons.
        main.insertWidget(6, clean_box)

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

    def on_capture_completed(self, result):
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
            report["checks"]["mainwindow_constructed"] = bool(win.windowTitle())
            report["checks"]["split_default_on"] = win.split_spreads.isChecked()
            report["checks"]["header_default"] = win.trim_header.isChecked()
            report["checks"]["footer_default"] = win.trim_footer.isChecked()
            report["checks"]["header_pct"] = win.header_pct.value()
            report["checks"]["footer_pct"] = win.footer_pct.value()
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
