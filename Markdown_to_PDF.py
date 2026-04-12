#!/usr/bin/env python3
"""
Markdown -> PDF converter for cheat sheets / technical docs.

Features:
- Colored code blocks (Pygments) with a consistent dark-blue background
- Wrapped code lines / URLs / table cells so nothing clips off the page
- Configurable page size (Letter/Legal/Tabloid/A3/A4/A5) and orientation
- Text scale control (50-300%) via CSS `zoom` — scales everything together
- Larger headings, no table of contents
- Optional intermediate HTML output
- PyQt6 GUI with drag-and-drop, in-place editor, and live rendered PDF preview

Pipeline:
1) python-markdown converts .md -> HTML with Pygments syntax highlighting
2) An @page rule, html{zoom}, and the app CSS are injected into a standalone HTML
3) Chrome/Chromium renders the final PDF headlessly (--headless=new --print-to-pdf)

Requires:
    pip install PyQt6 PyMuPDF markdown pygments
    Chrome/Chromium installed (auto-detected on macOS /Applications or $PATH)

CLI examples:
    python Markdown_to_PDF.py input.md
    python Markdown_to_PDF.py input.md -o out.pdf --keep-html
    python Markdown_to_PDF.py input.md --page-size A4 --orientation portrait --scale 1.25

GUI:
    python Markdown_to_PDF.py            # launch GUI (no args)
    python Markdown_to_PDF.py --gui      # force GUI
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HTML_TEMPLATE_INSERT = "</head>"

PAGE_SIZES = ["Letter", "Legal", "Tabloid", "A3", "A4", "A5"]
ORIENTATIONS = ["landscape", "portrait"]
DEFAULT_PAGE_SIZE = "Letter"
DEFAULT_ORIENTATION = "landscape"


def _page_css(page_size: str, orientation: str, scale: float = 1.0) -> str:
    return (
        "@page {\n"
        f"  size: {page_size} {orientation};\n"
        "  margin: 0.55in 0.6in 0.65in 0.6in;\n"
        "}\n"
        "html { zoom: " + f"{scale:.3f}" + "; }\n"
        "body, p, li, td, th, pre, code, h1, h2, h3, h4, h5, h6, blockquote {\n"
        "  overflow-wrap: anywhere;\n"
        "  word-break: break-word;\n"
        "}\n"
        "img, svg, video { max-width: 100%; height: auto; }\n"
        "table { table-layout: auto; word-break: break-word; }\n"
    )


EXTRA_CSS = r"""

html {
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

body {
  font-family: Inter, Arial, Helvetica, sans-serif;
  font-size: 10px;
  line-height: 1.34;
  color: #1f2937;
}

main, body {
  max-width: none;
}

h1, h2, h3, h4, h5, h6 {
  color: #0f172a;
  line-height: 1.2;
  margin-top: 0.7em;
  margin-bottom: 0.3em;
  page-break-after: avoid;
}

/* Bigger headings */
h1 { font-size: 24px; border-bottom: 2px solid #cbd5e1; padding-bottom: 5px; margin-top: 0.6em; margin-bottom: 0.35em; }
h2 { font-size: 19px; border-bottom: 1px solid #e2e8f0; padding-bottom: 3px; margin-top: 0.75em; margin-bottom: 0.3em; }
h3 { font-size: 15px; margin-top: 0.7em; }
h4 { font-size: 12px; }

p, ul, ol {
  margin-top: 0.3em;
  margin-bottom: 0.4em;
}

li { margin-bottom: 0.1em; }

code {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 0.92em;
}

/* Wrap code lines so nothing gets clipped */
pre, pre code {
  white-space: pre-wrap !important;
  overflow-wrap: anywhere;
  word-break: break-word;
}

pre, div.codehilite, div.codehilite pre, div.highlight, div.highlight pre {
  background: #0b1020 !important;
  color: #e5edf5 !important;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 12px;
  line-height: 1.3;
  font-size: 8.6px;
  overflow: visible;
  page-break-inside: auto;
}

div.codehilite pre, div.highlight pre {
  border: none;
  padding: 0;
  margin: 0;
}

.codehilite, .highlight { margin: 0.45em 0 0.7em 0; }

div.sourceCode {
  page-break-inside: auto;
  margin: 0.45em 0 0.7em 0;
}

div.sourceCode pre {
  margin: 0;
}

/* Pandoc code blocks */
code.sourceCode > span { display: inline; }
pre.sourceCode { padding-left: 12px; }

/* Inline code */
p code, li code, td code, th code {
  background: #eef2ff;
  color: #4338ca;
  padding: 0.08em 0.3em;
  border-radius: 4px;
}

table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5em 0 0.8em 0;
  font-size: 9.4px;
}

th, td {
  border: 1px solid #cbd5e1;
  padding: 5px 7px;
  vertical-align: top;
}

th {
  background: #f1f5f9;
}

blockquote {
  border-left: 3px solid #94a3b8;
  margin: 0.6em 0;
  padding: 0.2em 0 0.2em 0.8em;
  color: #475569;
}

hr {
  border: 0;
  border-top: 1px solid #cbd5e1;
  margin: 0.8em 0;
}

a { color: #1d4ed8; text-decoration: none; }

/* Remove TOC if present */
nav#TOC { display: none !important; }

.page-break { page-break-before: always; }
"""


def which_one(names: list[str]) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    # macOS: check standard .app bundle locations
    mac_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for p in mac_paths:
        if Path(p).exists():
            return p
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Markdown to a styled PDF with colored code blocks and wrapped code lines."
    )
    parser.add_argument("input_md", type=Path, nargs="?", help="Input markdown file (omit to launch GUI)")
    parser.add_argument("-o", "--output", type=Path, help="Output PDF path")
    parser.add_argument("--title", help="Optional title override")
    parser.add_argument(
        "--keep-html",
        action="store_true",
        help="Keep the intermediate HTML file",
    )
    parser.add_argument("--page-size", choices=PAGE_SIZES, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--orientation", choices=ORIENTATIONS, default=DEFAULT_ORIENTATION)
    parser.add_argument("--scale", type=float, default=1.0, help="Text scale factor (e.g. 1.25 = 125%)")
    parser.add_argument("--no-compress", action="store_true", help="Skip the PyMuPDF compression pass")
    parser.add_argument("--gui", action="store_true", help="Launch the GUI")
    return parser.parse_args()


def build_html(
    markdown_path: Path,
    html_path: Path,
    title: str | None,
    page_size: str = DEFAULT_PAGE_SIZE,
    orientation: str = DEFAULT_ORIENTATION,
    scale: float = 1.0,
) -> None:
    try:
        import markdown as md_lib
    except Exception as exc:
        raise SystemExit(
            "ERROR: the 'markdown' package is not installed.\n"
            "Install it with: pip install markdown pygments"
        ) from exc

    try:
        from pygments.formatters import HtmlFormatter
        pygments_css = HtmlFormatter(style="default").get_style_defs(".codehilite")
    except Exception:
        pygments_css = ""

    md_text = markdown_path.read_text(encoding="utf-8")
    converter = md_lib.Markdown(
        extensions=[
            "extra",
            "sane_lists",
            "tables",
            "fenced_code",
            "codehilite",
        ],
        extension_configs={
            "codehilite": {"guess_lang": False, "css_class": "codehilite"},
        },
        output_format="html5",
    )
    body_html = converter.convert(md_text)
    doc_title = title or markdown_path.stem

    page_css = _page_css(page_size, orientation, scale)
    html = (
        "<!DOCTYPE html>\n<html><head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{doc_title}</title>\n"
        f"<style>\n{page_css}\n{pygments_css}\n{EXTRA_CSS}\n</style>\n"
        "</head><body>\n"
        f"{body_html}\n"
        "</body></html>\n"
    )
    html_path.write_text(html, encoding="utf-8")


def render_with_chromium(html_path: Path, pdf_path: Path) -> None:
    chromium = which_one(["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"])
    if not chromium:
        raise RuntimeError("Chromium/Chrome not found")

    file_url = html_path.resolve().as_uri()
    cmd = [
        chromium,
        "--headless=new",
        "--disable-gpu",
        "--allow-file-access-from-files",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=10000",
        f"--print-to-pdf={pdf_path}",
        "--no-pdf-header-footer",
        file_url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not pdf_path.exists():
        raise RuntimeError(
            "Chromium PDF render failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def render_pdf(html_path: Path, pdf_path: Path) -> str:
    render_with_chromium(html_path, pdf_path)
    return "chromium"


def compress_pdf(pdf_path: Path) -> None:
    try:
        import fitz
    except Exception:
        return
    tmp = pdf_path.with_suffix(pdf_path.suffix + ".tmp")
    doc = fitz.open(str(pdf_path))
    try:
        doc.save(str(tmp), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    tmp.replace(pdf_path)


def convert_md_to_pdf(
    input_md: Path,
    output_pdf: Path,
    title: str | None,
    keep_html: bool,
    page_size: str = DEFAULT_PAGE_SIZE,
    orientation: str = DEFAULT_ORIENTATION,
    scale: float = 1.0,
    compress: bool = True,
) -> str:
    html_path = output_pdf.with_suffix(".html")
    build_html(input_md, html_path, title, page_size, orientation, scale)
    engine_used = render_pdf(html_path, output_pdf)
    if compress:
        compress_pdf(output_pdf)
    if not keep_html:
        try:
            html_path.unlink()
        except FileNotFoundError:
            pass
    return engine_used


# ----------------------------- GUI -----------------------------

STYLESHEET = """
QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; }
QLabel { color: #cdd6f4; font-size: 13px; }
QLabel#Title { font-size: 22px; font-weight: 600; color: #89b4fa; }
QLabel#Subtitle { font-size: 12px; color: #a6adc8; }
QLabel#DropHint {
    border: 2px dashed #45475a; border-radius: 14px; padding: 40px;
    background-color: #181825; color: #7f849c; font-size: 15px;
}
QPushButton {
    background-color: #89b4fa; color: #1e1e2e; border: none;
    border-radius: 8px; padding: 10px 18px; font-weight: 600; font-size: 13px;
}
QPushButton:hover { background-color: #b4befe; }
QPushButton:disabled { background-color: #45475a; color: #7f849c; }
QPushButton#Secondary { background-color: #313244; color: #cdd6f4; }
QPushButton#Secondary:hover { background-color: #45475a; }
QComboBox, QLineEdit {
    background-color: #313244; color: #cdd6f4; border: 1px solid #45475a;
    border-radius: 6px; padding: 0 10px; font-size: 13px; min-height: 34px;
}
QComboBox:focus, QLineEdit:focus { border-color: #89b4fa; }
QSpinBox {
    background-color: #313244; color: #cdd6f4; border: 1px solid #45475a;
    border-radius: 6px; padding: 0 10px; min-width: 80px;
    font-size: 14px; font-weight: 600;
}
QSpinBox:focus { border-color: #89b4fa; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; height: 0; border: none; }
QPushButton#Stepper {
    background-color: #45475a; color: #cdd6f4; border: none;
    border-radius: 6px; padding: 0; font-size: 18px; font-weight: 700;
}
QPushButton#Stepper:hover { background-color: #89b4fa; color: #1e1e2e; }
QPushButton#Stepper:pressed { background-color: #74a0e6; }
QPlainTextEdit, QTextEdit {
    background-color: #11111b; color: #cdd6f4; border: 1px solid #313244;
    border-radius: 10px; padding: 8px;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 12px;
}
QFrame#Card { background-color: #181825; border-radius: 12px; border: 1px solid #1f1f2e; }
QScrollArea { background-color: #11111b; border: 1px solid #313244; border-radius: 10px; }
QScrollBar:vertical { background: #181825; width: 10px; border-radius: 5px; }
QScrollBar::handle:vertical { background: #45475a; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #585b70; }
QScrollBar:horizontal { background: #181825; height: 10px; border-radius: 5px; }
QScrollBar::handle:horizontal { background: #45475a; border-radius: 5px; min-width: 30px; }
"""


def _run_gui() -> int:
    try:
        import fitz  # PyMuPDF
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtGui import QPixmap, QImage, QDragEnterEvent, QDropEvent, QFont
        from PyQt6.QtWidgets import (
            QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
            QPushButton, QFileDialog, QPlainTextEdit, QFrame, QScrollArea,
            QMessageBox, QSizePolicy, QComboBox, QLineEdit, QSpinBox,
        )
    except Exception as exc:
        print(f"GUI dependencies missing: {exc}\nInstall with: pip install PyQt6 PyMuPDF", file=sys.stderr)
        return 1

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("J Markdown → PDF")
            self.resize(1320, 840)
            self.setMinimumSize(640, 460)
            self.setAcceptDrops(True)

            self._input_path: Path | None = None
            self._tmpdir = Path(tempfile.mkdtemp(prefix="md2pdf_gui_"))
            self._preview_pdf = self._tmpdir / "preview.pdf"
            self._last_engine = ""

            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(500)
            self._timer.timeout.connect(self._update_preview)

            self._build_ui()

        def _build_ui(self):
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(22, 18, 22, 18)
            root.setSpacing(14)

            header = QVBoxLayout()
            header.setSpacing(2)
            title = QLabel("J Markdown → PDF")
            title.setObjectName("Title")
            sub = QLabel("Drag and drop a Markdown file. Edit, preview, and save as a styled PDF.")
            sub.setObjectName("Subtitle")
            header.addWidget(title)
            header.addWidget(sub)
            root.addLayout(header)

            toolbar = QHBoxLayout()
            toolbar.setSpacing(10)
            self.open_btn = QPushButton("📂  Open Markdown")
            self.open_btn.setObjectName("Secondary")
            self.open_btn.setShortcut("Ctrl+O")
            self.open_btn.clicked.connect(self._pick_file)
            toolbar.addWidget(self.open_btn)

            self.file_label = QLabel("No file loaded")
            self.file_label.setObjectName("Subtitle")
            self.file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            toolbar.addWidget(self.file_label, 1)
            toolbar.addStretch(1)

            self.save_html_btn = QPushButton("Save HTML")
            self.save_html_btn.setObjectName("Secondary")
            self.save_html_btn.setEnabled(False)
            self.save_html_btn.clicked.connect(self._save_html)
            toolbar.addWidget(self.save_html_btn)

            self.save_btn = QPushButton("💾  Save PDF")
            self.save_btn.setEnabled(False)
            self.save_btn.setShortcut("Ctrl+S")
            self.save_btn.clicked.connect(self._save_pdf)
            toolbar.addWidget(self.save_btn)
            root.addLayout(toolbar)

            body = QHBoxLayout()
            body.setSpacing(14)
            root.addLayout(body, 1)

            left_card = QFrame()
            left_card.setObjectName("Card")
            left = QVBoxLayout(left_card)
            left.setContentsMargins(14, 14, 14, 14)
            left.setSpacing(8)
            left.addWidget(QLabel("Source — Markdown (edits re-render the preview)"))

            self.drop_hint = QLabel("Drop a .md file here, or click “Open Markdown”")
            self.drop_hint.setObjectName("DropHint")
            self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.drop_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            left.addWidget(self.drop_hint, 1)

            self.editor = QPlainTextEdit()
            self.editor.hide()
            self.editor.textChanged.connect(lambda: self._timer.start())
            left.addWidget(self.editor, 1)
            body.addWidget(left_card, 1)

            right_card = QFrame()
            right_card.setObjectName("Card")
            right = QVBoxLayout(right_card)
            right.setContentsMargins(14, 14, 14, 14)
            right.setSpacing(8)
            right.addWidget(QLabel("Live preview — rendered PDF"))

            self.preview_scroll = QScrollArea()
            self.preview_scroll.setWidgetResizable(True)
            self.preview_host = QWidget()
            self.preview_layout = QVBoxLayout(self.preview_host)
            self.preview_layout.setContentsMargins(8, 8, 8, 8)
            self.preview_layout.setSpacing(10)
            self.preview_placeholder = QLabel("Load a Markdown file to see the preview.")
            self.preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview_placeholder.setStyleSheet("color: #7f849c; padding: 40px;")
            self.preview_layout.addWidget(self.preview_placeholder)
            self.preview_layout.addStretch(1)
            self.preview_scroll.setWidget(self.preview_host)
            right.addWidget(self.preview_scroll, 1)
            body.addWidget(right_card, 1)

            controls_card = QFrame()
            controls_card.setObjectName("Card")
            controls = QHBoxLayout(controls_card)
            controls.setContentsMargins(14, 10, 14, 10)
            controls.setSpacing(14)

            controls.addWidget(self._chip("Size", "#89b4fa"))
            self.size_combo = QComboBox()
            self.size_combo.addItems(PAGE_SIZES)
            self.size_combo.setCurrentText(DEFAULT_PAGE_SIZE)
            self.size_combo.currentTextChanged.connect(lambda _: self._timer.start())
            controls.addWidget(self.size_combo)

            controls.addSpacing(4)
            controls.addWidget(self._chip("Orientation", "#f9e2af"))
            self.orient_combo = QComboBox()
            self.orient_combo.addItems(ORIENTATIONS)
            self.orient_combo.setCurrentText(DEFAULT_ORIENTATION)
            self.orient_combo.currentTextChanged.connect(lambda _: self._timer.start())
            controls.addWidget(self.orient_combo)

            controls.addSpacing(4)
            controls.addWidget(self._chip("Scale", "#a6e3a1"))
            self.scale_spin = QSpinBox()
            self.scale_spin.setRange(50, 300)
            self.scale_spin.setSingleStep(10)
            self.scale_spin.setSuffix(" %")
            self.scale_spin.setValue(100)
            self.scale_spin.valueChanged.connect(lambda _: self._timer.start())
            controls.addWidget(self._stepper(self.scale_spin))

            controls.addSpacing(8)
            controls.addWidget(self._chip("Title", "#cba6f7"))
            self.title_edit = QLineEdit()
            self.title_edit.setPlaceholderText("optional title override")
            self.title_edit.textChanged.connect(lambda _: self._timer.start())
            controls.addWidget(self.title_edit, 1)

            self.compress_btn = QPushButton("Compress: ON")
            self.compress_btn.setObjectName("Secondary")
            self.compress_btn.setCheckable(True)
            self.compress_btn.setChecked(True)
            self.compress_btn.setToolTip("Shrink output via PyMuPDF (garbage collect + deflate)")
            self.compress_btn.toggled.connect(self._on_compress_toggled)
            controls.addWidget(self.compress_btn)

            self.refresh_btn = QPushButton("Refresh Preview")
            self.refresh_btn.setObjectName("Secondary")
            self.refresh_btn.clicked.connect(self._update_preview)
            controls.addWidget(self.refresh_btn)

            controls_scroll = QScrollArea()
            controls_scroll.setWidget(controls_card)
            controls_scroll.setWidgetResizable(True)
            controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
            controls_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            controls_scroll.setFixedHeight(controls_card.sizeHint().height() + 16)
            root.addWidget(controls_scroll)

            self.status = QLabel("")
            self.status.setObjectName("Subtitle")
            root.addWidget(self.status)

        def _stepper(self, spin: QSpinBox) -> QWidget:
            H = 34
            w = QWidget()
            w.setFixedHeight(H)
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)
            minus = QPushButton("−")
            minus.setObjectName("Stepper")
            minus.setAutoRepeat(True)
            minus.setAutoRepeatInterval(60)
            minus.setFixedSize(H, H)
            minus.clicked.connect(lambda: spin.setValue(spin.value() - spin.singleStep()))
            plus = QPushButton("+")
            plus.setObjectName("Stepper")
            plus.setAutoRepeat(True)
            plus.setAutoRepeatInterval(60)
            plus.setFixedSize(H, H)
            plus.clicked.connect(lambda: spin.setValue(spin.value() + spin.singleStep()))
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setFixedHeight(H)
            spin.setMinimumWidth(90)
            lay.addWidget(minus)
            lay.addWidget(spin)
            lay.addWidget(plus)
            return w

        def _chip(self, text: str, color: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setFixedHeight(34)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"background-color: {color}; color: #1e1e2e; padding: 0 12px; "
                f"border-radius: 8px; font-weight: 600; font-size: 12px;"
            )
            return lbl

        def _on_compress_toggled(self, on: bool):
            self.compress_btn.setText(f"Compress: {'ON' if on else 'OFF'}")

        def dragEnterEvent(self, event: QDragEnterEvent):
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    p = url.toLocalFile().lower()
                    if p.endswith(".md") or p.endswith(".markdown"):
                        event.acceptProposedAction()
                        return
            event.ignore()

        def dropEvent(self, event: QDropEvent):
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if p.lower().endswith((".md", ".markdown")):
                    self._load_md(Path(p))
                    event.acceptProposedAction()
                    return

        def _pick_file(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Markdown", "", "Markdown Files (*.md *.markdown);;All Files (*)"
            )
            if path:
                self._load_md(Path(path))

        def _load_md(self, path: Path):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not read file:\n{e}")
                return
            self._input_path = path
            self.editor.blockSignals(True)
            self.editor.setPlainText(text)
            self.editor.blockSignals(False)
            self.drop_hint.hide()
            self.editor.show()
            self.save_btn.setEnabled(True)
            self.save_html_btn.setEnabled(True)
            self.file_label.setText(f"📄  {path.name}")
            self.status.setText(f"Loaded: {path}")
            self._timer.start()

        def _write_editor_to_tmp(self) -> Path:
            src = self._tmpdir / "source.md"
            src.write_text(self.editor.toPlainText(), encoding="utf-8")
            return src

        def _update_preview(self):
            if self._input_path is None:
                return
            self.status.setText("Rendering preview…")
            QApplication.processEvents()
            src = self._write_editor_to_tmp()
            try:
                engine_used = convert_md_to_pdf(
                    src,
                    self._preview_pdf,
                    self.title_edit.text().strip() or None,
                    keep_html=False,
                    page_size=self.size_combo.currentText(),
                    orientation=self.orient_combo.currentText(),
                    scale=self.scale_spin.value() / 100.0,
                    compress=False,
                )
                self._last_engine = engine_used
            except SystemExit as e:
                self._show_preview_error(str(e))
                return
            except Exception as e:
                self._show_preview_error(str(e))
                return
            self._render_preview_pages()
            self.status.setText(f"Preview ready — engine: {engine_used}")

        def _show_preview_error(self, msg: str):
            self._clear_preview()
            err = QLabel(f"Preview error:\n\n{msg}")
            err.setAlignment(Qt.AlignmentFlag.AlignCenter)
            err.setStyleSheet("color: #f38ba8; padding: 20px;")
            err.setWordWrap(True)
            self.preview_layout.insertWidget(0, err)
            self.status.setText("Preview failed.")

        def _clear_preview(self):
            while self.preview_layout.count():
                item = self.preview_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

        def _render_preview_pages(self):
            self._clear_preview()
            try:
                doc = fitz.open(str(self._preview_pdf))
            except Exception as e:
                self._show_preview_error(f"Cannot open preview PDF: {e}")
                return
            dpr = max(1.0, float(self.devicePixelRatioF()))
            target_w = max(400, self.preview_scroll.viewport().width() - 32)
            for page in doc:
                zoom = target_w / page.rect.width
                mat = fitz.Matrix(zoom * dpr, zoom * dpr)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                pm = QPixmap.fromImage(img)
                pm.setDevicePixelRatio(dpr)
                lbl = QLabel()
                lbl.setPixmap(pm)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet("background: white; border: 1px solid #313244; border-radius: 6px;")
                self.preview_layout.addWidget(lbl)
            self.preview_layout.addStretch(1)
            doc.close()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._timer.start()

        def _save_pdf(self):
            if self._input_path is None:
                return
            default = str(self._input_path.with_suffix(".pdf"))
            path, _ = QFileDialog.getSaveFileName(self, "Save PDF", default, "PDF Files (*.pdf)")
            if not path:
                return
            src = self._write_editor_to_tmp()
            try:
                engine_used = convert_md_to_pdf(
                    src,
                    Path(path),
                    self.title_edit.text().strip() or None,
                    keep_html=False,
                    page_size=self.size_combo.currentText(),
                    orientation=self.orient_combo.currentText(),
                    scale=self.scale_spin.value() / 100.0,
                    compress=self.compress_btn.isChecked(),
                )
            except Exception as e:
                QMessageBox.critical(self, "Save failed", str(e))
                return
            self.status.setText(f"Saved PDF: {path}  (engine: {engine_used})")
            QMessageBox.information(self, "Saved", f"PDF: {path}\nEngine: {engine_used}")

        def _save_html(self):
            if self._input_path is None:
                return
            default = str(self._input_path.with_suffix(".html"))
            path, _ = QFileDialog.getSaveFileName(self, "Save HTML", default, "HTML Files (*.html)")
            if not path:
                return
            src = self._write_editor_to_tmp()
            try:
                build_html(
                    src,
                    Path(path),
                    self.title_edit.text().strip() or None,
                    self.size_combo.currentText(),
                    self.orient_combo.currentText(),
                    self.scale_spin.value() / 100.0,
                )
            except Exception as e:
                QMessageBox.critical(self, "Save failed", str(e))
                return
            self.status.setText(f"Saved HTML: {path}")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    app.setFont(QFont("Inter, Segoe UI, SF Pro Display, Helvetica", 10))
    win = MainWindow()
    win.show()
    return app.exec()


def main() -> int:
    args = parse_args()

    if args.gui or args.input_md is None:
        return _run_gui()

    input_md = args.input_md.resolve()
    if not input_md.exists():
        raise SystemExit(f"ERROR: input file does not exist: {input_md}")

    output_pdf = args.output.resolve() if args.output else input_md.with_suffix(".pdf")
    html_path = output_pdf.with_suffix(".html")

    build_html(input_md, html_path, args.title, args.page_size, args.orientation, args.scale)
    engine_used = render_pdf(html_path, output_pdf)
    if not args.no_compress:
        compress_pdf(output_pdf)

    if not args.keep_html:
        try:
            html_path.unlink()
        except FileNotFoundError:
            pass

    print(f"Created PDF: {output_pdf}")
    print(f"Engine used: {engine_used}")
    if args.keep_html:
        print(f"Kept HTML:   {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
