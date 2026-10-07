from __future__ import annotations

"""
J Long PDF to A4 - split tall PDF pages into standard A4 portrait pages.

The PDF content is placed with PyMuPDF's ``show_pdf_page`` API, so text and
vector artwork stay vector-based. The source width is fitted to the usable A4
width and the page is then sliced from top to bottom without stretching.

Run the GUI:
    python Long_PDF_To_A4.py

Or convert directly:
    python Long_PDF_To_A4.py input.pdf -o output.pdf
"""

import argparse
import math
import sys
from pathlib import Path
from typing import Iterator

import fitz  # PyMuPDF
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


PT_PER_MM = 72.0 / 25.4
A4_WIDTH_PT = 210.0 * PT_PER_MM
A4_HEIGHT_PT = 297.0 * PT_PER_MM
DEFAULT_MARGIN_MM = 0.0
DEFAULT_OVERLAP_MM = 0.0
MAX_PREVIEW_PAGES = 30


def _a4_geometry(margin_mm: float, overlap_mm: float) -> tuple[float, float, float]:
    """Return the margin, usable width, and usable height in PDF points."""
    if not math.isfinite(margin_mm) or margin_mm < 0:
        raise ValueError("Margin must be a non-negative number.")
    if not math.isfinite(overlap_mm) or overlap_mm < 0:
        raise ValueError("Overlap must be a non-negative number.")

    margin_pt = margin_mm * PT_PER_MM
    overlap_pt = overlap_mm * PT_PER_MM
    usable_width = A4_WIDTH_PT - 2 * margin_pt
    usable_height = A4_HEIGHT_PT - 2 * margin_pt
    if usable_width <= 0 or usable_height <= 0:
        raise ValueError("The margin is too large for an A4 page.")
    if overlap_pt >= usable_height:
        raise ValueError("Overlap must be smaller than the usable A4 page height.")
    return margin_pt, usable_width, usable_height


def iter_page_slices(
    page_rect: fitz.Rect,
    margin_mm: float = DEFAULT_MARGIN_MM,
    overlap_mm: float = DEFAULT_OVERLAP_MM,
) -> Iterator[tuple[fitz.Rect, float]]:
    """Yield ``(source_clip, scale)`` pairs for one source page."""
    margin_pt, usable_width, usable_height = _a4_geometry(margin_mm, overlap_mm)
    del margin_pt  # Validation is shared with the document builder.

    if page_rect.width <= 0 or page_rect.height <= 0:
        raise ValueError("The source PDF contains a page with an invalid size.")

    scale = usable_width / page_rect.width
    source_slice_height = usable_height / scale
    source_overlap = overlap_mm * PT_PER_MM / scale
    source_step = source_slice_height - source_overlap

    y = page_rect.y0
    # PDF page coordinates are stored with limited precision. Without a
    # relative tolerance, an exact multiple of A4 can leave a microscopic
    # remainder and incorrectly create one extra output page.
    epsilon = max(1e-4, page_rect.height * 1e-7)
    while y < page_rect.y1 - epsilon:
        bottom = min(y + source_slice_height, page_rect.y1)
        if page_rect.y1 - bottom <= epsilon:
            bottom = page_rect.y1
        yield fitz.Rect(page_rect.x0, y, page_rect.x1, bottom), scale
        if bottom >= page_rect.y1 - epsilon:
            break
        y += source_step


def count_a4_pages(
    source: fitz.Document,
    margin_mm: float = DEFAULT_MARGIN_MM,
    overlap_mm: float = DEFAULT_OVERLAP_MM,
) -> int:
    """Calculate the number of A4 pages without building the output PDF."""
    return sum(
        1
        for page in source
        for _clip, _scale in iter_page_slices(page.rect, margin_mm, overlap_mm)
    )


def build_a4_document(
    source: fitz.Document,
    margin_mm: float = DEFAULT_MARGIN_MM,
    overlap_mm: float = DEFAULT_OVERLAP_MM,
) -> fitz.Document:
    """Build an in-memory A4 document from every page in ``source``."""
    if source.page_count == 0:
        raise ValueError("Input PDF has no pages.")
    if source.needs_pass:
        raise ValueError("Password-protected PDFs are not supported.")

    margin_pt, _usable_width, _usable_height = _a4_geometry(margin_mm, overlap_mm)
    output = fitz.open()
    try:
        for source_page_number, source_page in enumerate(source):
            for clip, scale in iter_page_slices(
                source_page.rect, margin_mm, overlap_mm
            ):
                output_page = output.new_page(
                    width=A4_WIDTH_PT,
                    height=A4_HEIGHT_PT,
                )
                content_height = clip.height * scale
                destination = fitz.Rect(
                    margin_pt,
                    margin_pt,
                    A4_WIDTH_PT - margin_pt,
                    margin_pt + content_height,
                )
                output_page.show_pdf_page(
                    destination,
                    source,
                    source_page_number,
                    clip=clip,
                    keep_proportion=False,
                    overlay=True,
                )

        metadata = dict(source.metadata or {})
        metadata["producer"] = "J Long PDF to A4 (PyMuPDF)"
        output.set_metadata(metadata)
        return output
    except Exception:
        output.close()
        raise


def split_long_pdf_to_a4(
    input_pdf: str | Path,
    output_pdf: str | Path,
    margin_mm: float = DEFAULT_MARGIN_MM,
    overlap_mm: float = DEFAULT_OVERLAP_MM,
) -> int:
    """Convert a long PDF to A4 pages and return the output page count."""
    input_path = Path(input_pdf).expanduser().resolve()
    output_path = Path(output_pdf).expanduser().resolve()
    if input_path == output_path:
        raise ValueError("Input and output paths must be different.")

    source = fitz.open(str(input_path))
    output: fitz.Document | None = None
    try:
        output = build_a4_document(source, margin_mm, overlap_mm)
        page_count = output.page_count
        output.save(str(output_path), garbage=4, deflate=True)
        return page_count
    finally:
        if output is not None:
            output.close()
        source.close()


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_A4_pages.pdf")


STYLESHEET = """
QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; }
QLabel { color: #cdd6f4; font-size: 13px; }
QLabel#Title { font-size: 22px; font-weight: 600; color: #89b4fa; }
QLabel#Subtitle { font-size: 12px; color: #a6adc8; }
QLabel#DropHint {
    border: 2px dashed #45475a; border-radius: 14px; padding: 44px;
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
QDoubleSpinBox {
    background-color: #313244; color: #cdd6f4; border: 1px solid #45475a;
    border-radius: 6px; padding: 7px 10px; min-width: 100px;
    font-size: 13px; font-weight: 600;
}
QDoubleSpinBox:focus { border-color: #89b4fa; }
QFrame#Card { background-color: #181825; border-radius: 12px; border: 1px solid #313244; }
QScrollArea { background-color: #11111b; border: 1px solid #313244; border-radius: 10px; }
QScrollBar:vertical { background: #181825; width: 10px; border-radius: 5px; }
QScrollBar::handle:vertical { background: #45475a; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #585b70; }
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("J Long PDF to A4")
        self.resize(1000, 820)
        self.setMinimumSize(560, 480)
        self.setAcceptDrops(True)

        self._source: fitz.Document | None = None
        self._preview_document: fitz.Document | None = None
        self._input_path: Path | None = None

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._update_preview)

        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        title = QLabel("J Long PDF to A4")
        title.setObjectName("Title")
        subtitle = QLabel(
            "Split tall PDF pages into standard A4 portrait pages while preserving vector quality."
        )
        subtitle.setObjectName("Subtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.open_button = QPushButton("Open PDF")
        self.open_button.setObjectName("Secondary")
        self.open_button.setShortcut("Ctrl+O")
        self.open_button.clicked.connect(self._pick_file)
        toolbar.addWidget(self.open_button)

        self.file_label = QLabel("No file loaded")
        self.file_label.setObjectName("Subtitle")
        self.file_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        toolbar.addWidget(self.file_label, 1)

        self.save_button = QPushButton("Save A4 PDF")
        self.save_button.setShortcut("Ctrl+S")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save_output)
        toolbar.addWidget(self.save_button)
        root.addLayout(toolbar)

        controls_card = QFrame()
        controls_card.setObjectName("Card")
        controls = QHBoxLayout(controls_card)
        controls.setContentsMargins(14, 12, 14, 12)
        controls.setSpacing(12)

        controls.addWidget(QLabel("A4 margin:"))
        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0.0, 50.0)
        self.margin_spin.setDecimals(1)
        self.margin_spin.setSingleStep(1.0)
        self.margin_spin.setSuffix(" mm")
        self.margin_spin.setValue(DEFAULT_MARGIN_MM)
        self.margin_spin.valueChanged.connect(self._settings_changed)
        controls.addWidget(self.margin_spin)

        controls.addSpacing(12)
        controls.addWidget(QLabel("Page overlap:"))
        self.overlap_spin = QDoubleSpinBox()
        self.overlap_spin.setRange(0.0, 100.0)
        self.overlap_spin.setDecimals(1)
        self.overlap_spin.setSingleStep(1.0)
        self.overlap_spin.setSuffix(" mm")
        self.overlap_spin.setValue(DEFAULT_OVERLAP_MM)
        self.overlap_spin.valueChanged.connect(self._settings_changed)
        controls.addWidget(self.overlap_spin)
        controls.addStretch(1)

        hint = QLabel("Overlap repeats the bottom of one page at the top of the next.")
        hint.setObjectName("Subtitle")
        controls.addWidget(hint)
        root.addWidget(controls_card)

        preview_card = QFrame()
        preview_card.setObjectName("Card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(14, 14, 14, 14)
        preview_layout.setSpacing(9)

        self.summary_label = QLabel("Output preview")
        preview_layout.addWidget(self.summary_label)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.preview_content = QWidget()
        self.preview_pages_layout = QVBoxLayout(self.preview_content)
        self.preview_pages_layout.setContentsMargins(18, 18, 18, 18)
        self.preview_pages_layout.setSpacing(18)
        self.preview_pages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.drop_hint = QLabel("Drop a long PDF here, or click Open PDF")
        self.drop_hint.setObjectName("DropHint")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setMinimumHeight(260)
        self.preview_pages_layout.addWidget(self.drop_hint)

        self.preview_scroll.setWidget(self.preview_content)
        preview_layout.addWidget(self.preview_scroll, 1)
        root.addWidget(preview_card, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Subtitle")
        root.addWidget(self.status_label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(
            url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".pdf":
                self._load_pdf(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def _pick_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Select a long PDF", "", "PDF Files (*.pdf)"
        )
        if path:
            self._load_pdf(Path(path))

    def _load_pdf(self, path: Path) -> None:
        source: fitz.Document | None = None
        try:
            source = fitz.open(str(path))
            if source.page_count == 0:
                raise ValueError("PDF has no pages.")
            if source.needs_pass:
                raise ValueError("Password-protected PDFs are not supported.")
            # Read the first page now so damaged files fail before replacing the old one.
            first_size = source[0].rect
        except Exception as error:
            if source is not None:
                source.close()
            QMessageBox.critical(self, "Open failed", f"Could not open PDF:\n{error}")
            return

        self._close_documents()
        self._source = source
        self._input_path = path
        self.file_label.setText(path.name)
        self.save_button.setEnabled(True)
        self.status_label.setText(
            f"Loaded {source.page_count} source page(s); first page is "
            f"{first_size.width / PT_PER_MM:.1f} x {first_size.height / PT_PER_MM:.1f} mm."
        )
        self._preview_timer.start()

    def _settings_changed(self) -> None:
        if self._source is not None:
            self._preview_timer.start()

    def _clear_preview_widgets(self) -> None:
        while self.preview_pages_layout.count():
            item = self.preview_pages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _update_preview(self) -> None:
        if self._source is None:
            return

        margin_mm = self.margin_spin.value()
        overlap_mm = self.overlap_spin.value()
        try:
            preview_document = build_a4_document(
                self._source,
                margin_mm=margin_mm,
                overlap_mm=overlap_mm,
            )
        except Exception as error:
            self.summary_label.setText(f"Preview error: {error}")
            return

        if self._preview_document is not None:
            self._preview_document.close()
        self._preview_document = preview_document
        self._clear_preview_widgets()

        total = preview_document.page_count
        shown = min(total, MAX_PREVIEW_PAGES)
        self.summary_label.setText(
            f"Output: {total} A4 page(s)"
            + (f" - previewing the first {shown}" if shown < total else "")
        )

        viewport_width = max(280, self.preview_scroll.viewport().width() - 70)
        display_width = min(500, viewport_width)
        dpr = max(1.0, float(self.devicePixelRatioF()))
        zoom = display_width / A4_WIDTH_PT

        for page_number in range(shown):
            caption = QLabel(f"A4 page {page_number + 1} of {total}")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setObjectName("Subtitle")
            self.preview_pages_layout.addWidget(caption)

            page = preview_document[page_number]
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom * dpr, zoom * dpr), alpha=False)
            image = QImage(
                pix.samples,
                pix.width,
                pix.height,
                pix.stride,
                QImage.Format.Format_RGB888,
            ).copy()
            page_pixmap = QPixmap.fromImage(image)
            page_pixmap.setDevicePixelRatio(dpr)

            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setPixmap(page_pixmap)
            label.setStyleSheet("border: 1px solid #45475a; background: white;")
            page_size = page_pixmap.deviceIndependentSize().toSize()
            label.setFixedSize(page_size.width() + 2, page_size.height() + 2)
            self.preview_pages_layout.addWidget(
                label, alignment=Qt.AlignmentFlag.AlignHCenter
            )

        if shown < total:
            more = QLabel(
                f"{total - shown} more page(s) are not rendered in the preview. "
                "They will be included when you save."
            )
            more.setObjectName("Subtitle")
            more.setAlignment(Qt.AlignmentFlag.AlignCenter)
            more.setWordWrap(True)
            self.preview_pages_layout.addWidget(more)

    def _save_output(self) -> None:
        if self._source is None or self._input_path is None:
            return

        suggested = default_output_path(self._input_path)
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save A4 PDF", str(suggested), "PDF Files (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        try:
            page_count = split_long_pdf_to_a4(
                self._input_path,
                path,
                margin_mm=self.margin_spin.value(),
                overlap_mm=self.overlap_spin.value(),
            )
        except Exception as error:
            QMessageBox.critical(self, "Save failed", str(error))
            return

        self.status_label.setText(f"Saved {page_count} A4 page(s): {path}")
        QMessageBox.information(
            self, "Saved", f"Created {page_count} A4 page(s).\n\n{path}"
        )

    def _close_documents(self) -> None:
        if self._preview_document is not None:
            self._preview_document.close()
            self._preview_document = None
        if self._source is not None:
            self._source.close()
            self._source = None

    def closeEvent(self, event) -> None:
        self._close_documents()
        super().closeEvent(event)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split long PDF pages into standard A4 portrait pages."
    )
    parser.add_argument("input", nargs="?", help="Long PDF to convert")
    parser.add_argument("-o", "--output", help="Output PDF path")
    parser.add_argument(
        "--margin-mm",
        type=float,
        default=DEFAULT_MARGIN_MM,
        help="Blank margin on every A4 edge (default: 0)",
    )
    parser.add_argument(
        "--overlap-mm",
        type=float,
        default=DEFAULT_OVERLAP_MM,
        help="Content repeated between adjacent pages (default: 0)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open the GUI even if an input path is supplied",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.input and not args.gui:
        input_path = Path(args.input)
        output_path = Path(args.output) if args.output else default_output_path(input_path)
        try:
            page_count = split_long_pdf_to_a4(
                input_path,
                output_path,
                margin_mm=args.margin_mm,
                overlap_mm=args.overlap_mm,
            )
        except Exception as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        print(f"Created {page_count} A4 page(s): {output_path}")
        return 0

    app = QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    app.setFont(QFont("Inter, Segoe UI, SF Pro Display, Helvetica", 10))
    window = MainWindow()
    window.show()
    if args.input:
        QTimer.singleShot(0, lambda: window._load_pdf(Path(args.input)))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
