from __future__ import annotations

"""
J PDF Maker — stitch a multi-page PDF into a single long-page PDF while
preserving vector quality, with a PyQt6 GUI.

Features:
- Drag and drop a PDF (or use Open PDF / Ctrl+O)
- Auto-detects likely header and footer regions from the first page's text
- Draggable red (header) and green (footer) guide lines on the source preview
  with a thick, easy-to-grab hit band
- Live preview of the stitched long-page output, rendered sharp on HiDPI
- "Keep header on page 1" toggle — skip the top crop on the first page only
- Save as vector PDF (Ctrl+S) or as PNG with a selectable DPI (Ctrl+E)
- Runs on Windows, macOS, and Linux (Fusion style for consistent rendering)

Dependencies: PyQt6, PyMuPDF. Run with `python PDF_maker.py`.
"""

import sys
from pathlib import Path

import fitz  # PyMuPDF
from PyQt6.QtCore import Qt, QRectF, QTimer, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QColor, QDragEnterEvent, QDropEvent,
    QFont, QCursor, QPainterPath,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsItem, QSpinBox, QFrame, QScrollArea, QMessageBox, QSizePolicy,
    QCheckBox,
)


DEFAULT_TOP_CROP_PT = 26
DEFAULT_BOTTOM_CROP_PT = 32
DEFAULT_PNG_PPI = 300


def make_long_pdf_vector(
    input_pdf: str,
    output_pdf: str,
    top_crop_pt: float = DEFAULT_TOP_CROP_PT,
    bottom_crop_pt: float = DEFAULT_BOTTOM_CROP_PT,
    keep_first_header: bool = False,
) -> None:
    """Create one long-page PDF from a multi-page PDF while preserving vectors."""
    src = fitz.open(input_pdf)
    if src.page_count == 0:
        src.close()
        raise ValueError("Input PDF has no pages.")

    clips: list[fitz.Rect] = []
    total_height = 0.0
    max_width = 0.0
    for i, page in enumerate(src):
        r = page.rect
        page_top = 0 if (keep_first_header and i == 0) else top_crop_pt
        clip = fitz.Rect(r.x0, r.y0 + page_top, r.x1, r.y1 - bottom_crop_pt)
        if clip.height <= 0:
            src.close()
            raise ValueError(
                f"Crop removed entire page (top={page_top}, bottom={bottom_crop_pt})."
            )
        clips.append(clip)
        total_height += clip.height
        max_width = max(max_width, clip.width)

    out = fitz.open()
    dst_page = out.new_page(width=max_width, height=total_height)
    y = 0.0
    for page_num, clip in enumerate(clips):
        dest = fitz.Rect(0, y, clip.width, y + clip.height)
        dst_page.show_pdf_page(dest, src, page_num, clip=clip, keep_proportion=False, overlay=True)
        y += clip.height

    out.save(output_pdf, garbage=4, deflate=True)
    out.close()
    src.close()


def export_pdf_first_page_to_png(pdf_path: str, png_path: str, ppi: int = DEFAULT_PNG_PPI) -> None:
    doc = fitz.open(pdf_path)
    if doc.page_count == 0:
        doc.close()
        raise ValueError("Output PDF has no pages to export.")
    page = doc[0]
    pix = page.get_pixmap(alpha=False, dpi=ppi)
    pix.save(png_path)
    doc.close()


def build_output_paths(input_pdf_path: Path) -> tuple[Path, Path]:
    stem = input_pdf_path.stem
    parent = input_pdf_path.parent
    return parent / f"{stem}_long_page_vector.pdf", parent / f"{stem}_long_page_vector.png"


def auto_detect_crops(doc: fitz.Document) -> tuple[float, float]:
    """Heuristic: guess header/footer heights from the first page's text blocks."""
    if doc.page_count == 0:
        return float(DEFAULT_TOP_CROP_PT), float(DEFAULT_BOTTOM_CROP_PT)
    page = doc[0]
    h = page.rect.height
    blocks = [b for b in page.get_text("blocks") if b[4].strip()]
    if not blocks:
        return float(DEFAULT_TOP_CROP_PT), float(DEFAULT_BOTTOM_CROP_PT)
    top_y = min(b[1] for b in blocks)
    bottom_y = max(b[3] for b in blocks)
    top = max(10.0, min(top_y - 2, 80.0))
    bottom = max(10.0, min(h - bottom_y - 2, 80.0))
    return float(top), float(bottom)


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
QFrame#Card { background-color: #181825; border-radius: 12px; border: 1px solid #1f1f2e; }
QGraphicsView { background-color: #11111b; border: 1px solid #313244; border-radius: 10px; }
QScrollArea { background-color: #11111b; border: 1px solid #313244; border-radius: 10px; }
QScrollBar:vertical { background: #181825; width: 10px; border-radius: 5px; }
QScrollBar::handle:vertical { background: #45475a; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #585b70; }
QScrollBar:horizontal { background: #181825; height: 10px; border-radius: 5px; }
QScrollBar::handle:horizontal { background: #45475a; border-radius: 5px; min-width: 30px; }
"""


class GuideLine(QGraphicsItem):
    """Draggable horizontal guide line with a thick, easy-to-grab hit area."""

    HIT_HALF_HEIGHT = 10  # scene px half-height of draggable band

    def __init__(self, color: QColor, view: "PdfView"):
        super().__init__()
        self._view = view
        self._color = color
        self._width = 0.0
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))
        self.setZValue(10)
        self.setAcceptHoverEvents(True)

    def set_width(self, w: float):
        self.prepareGeometryChange()
        self._width = w

    def boundingRect(self) -> QRectF:
        return QRectF(0, -self.HIT_HALF_HEIGHT, self._width, self.HIT_HALF_HEIGHT * 2)

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        p.addRect(self.boundingRect())
        return p

    def paint(self, painter, option, widget=None):
        # translucent grab band
        band = QColor(self._color)
        band.setAlpha(55)
        painter.fillRect(QRectF(0, -self.HIT_HALF_HEIGHT, self._width, self.HIT_HALF_HEIGHT * 2), band)
        pen = QPen(self._color, 5)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(QPointF(0, 0), QPointF(self._width, 0))
        # handle circles at the ends
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(8, 0), 6, 6)
        painter.drawEllipse(QPointF(self._width - 8, 0), 6, 6)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            new_pos = QPointF(0, value.y())
            new_pos.setY(max(self._view.min_y(self), min(self._view.max_y(self), new_pos.y())))
            return new_pos
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._view.guide_moved()
        return super().itemChange(change, value)


class PdfView(QGraphicsView):
    cropsChanged = pyqtSignal(float, float)

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setMinimumSize(180, 180)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._top_line: GuideLine | None = None
        self._bottom_line: GuideLine | None = None
        self._page_width_pt = 0.0
        self._page_height_pt = 0.0
        self._scale = 1.0

    def load_page(self, doc: fitz.Document, top_pt: float, bottom_pt: float):
        self._scene.clear()
        page = doc[0]
        self._page_width_pt = page.rect.width
        self._page_height_pt = page.rect.height
        dpr = max(1.0, float(self.devicePixelRatioF()))
        render_dpi = int(200 * dpr)
        pix = page.get_pixmap(dpi=render_dpi, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        scene_w = pm.width() / dpr
        scene_h = pm.height() / dpr
        self._scale = scene_w / self._page_width_pt
        self._pixmap_item = self._scene.addPixmap(pm)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.setSceneRect(QRectF(0, 0, scene_w, scene_h))

        self._top_line = GuideLine(QColor("#f38ba8"), self)
        self._top_line.set_width(scene_w)
        self._top_line.setPos(0, top_pt * self._scale)
        self._scene.addItem(self._top_line)

        self._bottom_line = GuideLine(QColor("#a6e3a1"), self)
        self._bottom_line.set_width(scene_w)
        self._bottom_line.setPos(0, (self._page_height_pt - bottom_pt) * self._scale)
        self._scene.addItem(self._bottom_line)

        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pixmap_item is not None:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def min_y(self, line: GuideLine) -> float:
        if line is self._top_line:
            return 0.0
        if line is self._bottom_line and self._top_line is not None:
            return self._top_line.pos().y() + 4
        return 0.0

    def max_y(self, line: GuideLine) -> float:
        h = self._page_height_pt * self._scale
        if line is self._bottom_line:
            return h
        if line is self._top_line and self._bottom_line is not None:
            return self._bottom_line.pos().y() - 4
        return h

    def guide_moved(self):
        if self._top_line and self._bottom_line:
            top_pt = self._top_line.pos().y() / self._scale
            bottom_pt = (self._page_height_pt * self._scale - self._bottom_line.pos().y()) / self._scale
            self.cropsChanged.emit(max(0.0, top_pt), max(0.0, bottom_pt))

    def set_crops(self, top_pt: float, bottom_pt: float):
        if self._top_line and self._bottom_line:
            self._top_line.setPos(0, top_pt * self._scale)
            self._bottom_line.setPos(0, (self._page_height_pt - bottom_pt) * self._scale)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("J PDF Maker")
        self.resize(1280, 820)
        self.setMinimumSize(560, 420)
        self.setAcceptDrops(True)

        self._doc: fitz.Document | None = None
        self._input_path: Path | None = None
        self._top_pt = float(DEFAULT_TOP_CROP_PT)
        self._bottom_pt = float(DEFAULT_BOTTOM_CROP_PT)
        self._keep_first_header = True

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._update_preview)

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("J PDF Maker")
        title.setObjectName("Title")
        subtitle = QLabel("Drag and drop a PDF. Adjust the header and footer guides. Save a stitched long-page PDF.")
        subtitle.setObjectName("Subtitle")
        header.addWidget(title)
        header.addWidget(subtitle)
        root.addLayout(header)

        # --- Toolbar row (top): open, save actions ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.open_btn = QPushButton("📂  Open PDF")
        self.open_btn.setObjectName("Secondary")
        self.open_btn.setShortcut("Ctrl+O")
        self.open_btn.setToolTip("Open a PDF (⌘O / Ctrl+O)")
        self.open_btn.clicked.connect(self._pick_file)
        toolbar.addWidget(self.open_btn)

        self.file_label = QLabel("No file loaded")
        self.file_label.setObjectName("Subtitle")
        self.file_label.setMinimumWidth(0)
        self.file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(self.file_label, 1)
        toolbar.addStretch(1)

        self.save_png_btn = QPushButton("Save PNG")
        self.save_png_btn.setObjectName("Secondary")
        self.save_png_btn.setEnabled(False)
        self.save_png_btn.setShortcut("Ctrl+E")
        self.save_png_btn.setToolTip("Export as PNG (⌘E / Ctrl+E)")
        self.save_png_btn.clicked.connect(self._save_png)
        toolbar.addWidget(self.save_png_btn)

        self.save_btn = QPushButton("💾  Save PDF")
        self.save_btn.setEnabled(False)
        self.save_btn.setShortcut("Ctrl+S")
        self.save_btn.setToolTip("Save long-page PDF (⌘S / Ctrl+S)")
        self.save_btn.clicked.connect(self._save_output)
        toolbar.addWidget(self.save_btn)
        root.addLayout(toolbar)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)

        left_card = QFrame()
        left_card.setObjectName("Card")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(8)
        left_layout.addWidget(QLabel("Source — drag the red (header) and green (footer) lines"))

        self.drop_hint = QLabel("Drop a PDF here, or click \u201cOpen PDF\u201d")
        self.drop_hint.setObjectName("DropHint")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout.addWidget(self.drop_hint, 1)

        self.pdf_view = PdfView()
        self.pdf_view.hide()
        self.pdf_view.cropsChanged.connect(self._on_crops_changed)
        left_layout.addWidget(self.pdf_view, 1)
        body.addWidget(left_card, 1)

        right_card = QFrame()
        right_card.setObjectName("Card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(8)
        right_layout.addWidget(QLabel("Live preview — stitched long-page output"))

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setMinimumSize(180, 180)
        self.preview_label = QLabel("Load a PDF to see the preview.")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("color: #7f849c; padding: 40px;")
        self.preview_scroll.setWidget(self.preview_label)
        right_layout.addWidget(self.preview_scroll, 1)
        body.addWidget(right_card, 1)

        controls_card = QFrame()
        controls_card.setObjectName("Card")
        controls = QHBoxLayout(controls_card)
        controls.setContentsMargins(14, 10, 14, 10)
        controls.setSpacing(14)

        controls.addWidget(self._chip("Header", "#f38ba8"))
        self.top_spin = QSpinBox()
        self.top_spin.setRange(0, 500)
        self.top_spin.setSuffix(" pt")
        self.top_spin.setValue(int(self._top_pt))
        self.top_spin.valueChanged.connect(self._on_spin_changed)
        controls.addWidget(self._stepper(self.top_spin))

        controls.addSpacing(8)
        controls.addWidget(self._chip("Footer", "#a6e3a1"))
        self.bottom_spin = QSpinBox()
        self.bottom_spin.setRange(0, 500)
        self.bottom_spin.setSuffix(" pt")
        self.bottom_spin.setValue(int(self._bottom_pt))
        self.bottom_spin.valueChanged.connect(self._on_spin_changed)
        controls.addWidget(self._stepper(self.bottom_spin))

        controls.addSpacing(18)
        self.keep_first_header_btn = QPushButton("Keep header on page 1: ON")
        self.keep_first_header_btn.setObjectName("Secondary")
        self.keep_first_header_btn.setCheckable(True)
        self.keep_first_header_btn.setChecked(True)
        self.keep_first_header_btn.toggled.connect(self._on_keep_first_toggled)
        controls.addWidget(self.keep_first_header_btn)

        controls.addStretch(1)

        controls.addWidget(QLabel("PNG DPI:"))
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(100, 1200)
        self.dpi_spin.setSingleStep(100)
        self.dpi_spin.setValue(DEFAULT_PNG_PPI)
        self.dpi_spin.setSuffix(" dpi")
        controls.addWidget(self._stepper(self.dpi_spin))

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

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".pdf"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith(".pdf"):
                self._load_pdf(Path(p))
                event.acceptProposedAction()
                return

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF Files (*.pdf)")
        if path:
            self._load_pdf(Path(path))

    def _load_pdf(self, path: Path):
        try:
            doc = fitz.open(str(path))
            if doc.page_count == 0:
                raise ValueError("PDF has no pages.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open PDF:\n{e}")
            return

        if self._doc is not None:
            self._doc.close()
        self._doc = doc
        self._input_path = path

        top, bottom = auto_detect_crops(doc)
        self._top_pt = top
        self._bottom_pt = bottom
        self.top_spin.blockSignals(True)
        self.bottom_spin.blockSignals(True)
        self.top_spin.setValue(int(round(top)))
        self.bottom_spin.setValue(int(round(bottom)))
        self.top_spin.blockSignals(False)
        self.bottom_spin.blockSignals(False)

        self.drop_hint.hide()
        self.pdf_view.show()
        self.pdf_view.load_page(doc, top, bottom)
        self.save_btn.setEnabled(True)
        self.save_png_btn.setEnabled(True)
        self.file_label.setText(f"📄  {path.name}  •  {doc.page_count} pages")
        self.status.setText(
            f"Loaded: {path.name}  •  {doc.page_count} pages  •  "
            f"auto-detected header {top:.0f}pt / footer {bottom:.0f}pt"
        )
        self._preview_timer.start()

    def _on_crops_changed(self, top: float, bottom: float):
        self._top_pt = top
        self._bottom_pt = bottom
        self.top_spin.blockSignals(True)
        self.bottom_spin.blockSignals(True)
        self.top_spin.setValue(int(round(top)))
        self.bottom_spin.setValue(int(round(bottom)))
        self.top_spin.blockSignals(False)
        self.bottom_spin.blockSignals(False)
        self._preview_timer.start()

    def _on_spin_changed(self):
        self._top_pt = float(self.top_spin.value())
        self._bottom_pt = float(self.bottom_spin.value())
        self.pdf_view.set_crops(self._top_pt, self._bottom_pt)
        self._preview_timer.start()

    def _on_keep_first_toggled(self, checked: bool):
        self._keep_first_header = checked
        self.keep_first_header_btn.setText(
            f"Keep header on page 1: {'ON' if checked else 'OFF'}"
        )
        self._preview_timer.start()

    def _update_preview(self):
        if self._doc is None:
            return
        try:
            pm = self._render_stitched_preview(self._doc, self._top_pt, self._bottom_pt)
        except Exception as e:
            self.preview_label.setText(f"Preview error: {e}")
            self.preview_label.setPixmap(QPixmap())
            return
        self.preview_label.setText("")
        self.preview_label.setPixmap(pm)
        self.preview_label.resize(pm.size())

    def _render_stitched_preview(self, src: fitz.Document, top_pt: float, bottom_pt: float) -> QPixmap:
        clips = []
        total_h = 0.0
        max_w = 0.0
        for i, page in enumerate(src):
            r = page.rect
            page_top = 0 if (self._keep_first_header and i == 0) else top_pt
            clip = fitz.Rect(r.x0, r.y0 + page_top, r.x1, r.y1 - bottom_pt)
            if clip.height <= 0:
                raise ValueError("Crop too large.")
            clips.append(clip)
            total_h += clip.height
            max_w = max(max_w, clip.width)

        tmp = fitz.open()
        dst = tmp.new_page(width=max_w, height=total_h)
        y = 0.0
        for i, clip in enumerate(clips):
            dest = fitz.Rect(0, y, clip.width, y + clip.height)
            dst.show_pdf_page(dest, src, i, clip=clip, keep_proportion=False, overlay=True)
            y += clip.height

        dpr = max(1.0, float(self.devicePixelRatioF()))
        target_w = max(400, self.preview_scroll.viewport().width() - 24)
        zoom = min(target_w / max_w, 2.5)
        mat = fitz.Matrix(zoom * dpr, zoom * dpr)
        pix = dst.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        tmp.close()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        return pm

    def _save_output(self):
        if self._doc is None or self._input_path is None:
            return
        default_pdf, _ = build_output_paths(self._input_path)
        path, _ = QFileDialog.getSaveFileName(self, "Save Long PDF", str(default_pdf), "PDF Files (*.pdf)")
        if not path:
            return
        out_pdf = Path(path)
        try:
            make_long_pdf_vector(
                input_pdf=str(self._input_path),
                output_pdf=str(out_pdf),
                top_crop_pt=self._top_pt,
                bottom_crop_pt=self._bottom_pt,
                keep_first_header=self._keep_first_header,
            )
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.status.setText(f"Saved PDF: {out_pdf.name}")
        QMessageBox.information(self, "Saved", f"PDF: {out_pdf}")

    def _save_png(self):
        if self._doc is None or self._input_path is None:
            return
        _, default_png = build_output_paths(self._input_path)
        path, _ = QFileDialog.getSaveFileName(self, "Save PNG", str(default_png), "PNG Files (*.png)")
        if not path:
            return
        out_png = Path(path)
        tmp_pdf = out_png.with_name(out_png.stem + "_tmp.pdf")
        dpi = int(self.dpi_spin.value())
        try:
            make_long_pdf_vector(
                input_pdf=str(self._input_path),
                output_pdf=str(tmp_pdf),
                top_crop_pt=self._top_pt,
                bottom_crop_pt=self._bottom_pt,
                keep_first_header=self._keep_first_header,
            )
            export_pdf_first_page_to_png(str(tmp_pdf), str(out_png), ppi=dpi)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        finally:
            try:
                tmp_pdf.unlink(missing_ok=True)
            except Exception:
                pass
        self.status.setText(f"Saved PNG: {out_png.name} @ {dpi} DPI")
        QMessageBox.information(self, "Saved", f"PNG: {out_png}\nDPI: {dpi}")


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    app.setFont(QFont("Inter, Segoe UI, SF Pro Display, Helvetica", 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
