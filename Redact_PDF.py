from __future__ import annotations

"""
J Redact PDF — permanently black out anything on a PDF, with a PyQt6 GUI.

Workflow:
- Drag and drop a PDF (or use Open PDF / Ctrl+O)
- Scroll through the whole document in one continuous canvas and drag on any
  page to draw a redaction box; drag a box to move it, drag its handles to
  resize, double-click or press Del to remove it
- The right panel is a continuously scrolling live preview of the real
  redacted result, page by page
- Save the result as <name>_redacted.pdf (Ctrl+S)

Redaction is destructive, not cosmetic: boxes become PDF redaction
annotations that are then applied, so the covered text, images, and vector
art are removed from the file's content stream — not merely hidden behind a
black rectangle.

Both panels render pages lazily — only what is on screen (plus a margin) is
rasterized — so documents with hundreds of pages scroll smoothly.

Dependencies: PyQt6, PyMuPDF. Run with `python Redact_PDF.py [input.pdf]`.
"""

import argparse
import bisect
import copy
import os
import sys
from pathlib import Path

import fitz  # PyMuPDF
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, QPointF, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QColor, QBrush, QCursor, QFont, QTransform,
    QDragEnterEvent, QDropEvent,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsItem, QGraphicsObject, QGraphicsRectItem, QGraphicsSimpleTextItem,
    QSpinBox, QComboBox, QFrame, QScrollArea, QMessageBox, QSizePolicy, QLayout,
    QWidgetItem,
)


EDITOR_RENDER_DPI = 200      # page render resolution of the editing canvas
PREVIEW_MAX_ZOOM = 2.5       # upper bound for the live preview zoom
MIN_BOX_PT = 2.0             # ignore accidental click-sized boxes
UNDO_LIMIT = 100
PAGE_GAP_PT = 16             # vertical gap between stacked pages, in points
LAZY_MARGIN = 1.0            # extra viewport heights rendered above/below
BAND_PT = 1200.0             # tall pages are rasterized in bands this high
# Render guards so very tall pages (e.g. the output of Long_PDF_Maker.py) stay
# loadable instead of asking for a gigabyte-sized bitmap.
MAX_RENDER_PIXELS = 26_000_000
MAX_RENDER_SIDE = 20_000

# Box fill colors offered in the UI. None means "remove content, paint nothing".
FILL_COLORS: dict[str, tuple[float, float, float] | None] = {
    "Black": (0.0, 0.0, 0.0),
    "White": (1.0, 1.0, 1.0),
    "No fill": None,
}

# PyMuPDF redaction constants, resolved defensively so the file still runs on
# older PyMuPDF releases that lack some of the names.
IMAGE_MODES: dict[str, int] = {
    "Black out pixels": getattr(fitz, "PDF_REDACT_IMAGE_PIXELS", 2),
    "Remove image": getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", 1),
    "Keep": getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0),
}
VECTOR_MODES: dict[str, int] = {
    "Remove if covered": getattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED", 1),
    "Remove if touched": getattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED", 2),
    "Keep": getattr(fitz, "PDF_REDACT_LINE_ART_NONE", 0),
}

DEFAULT_FILL = "Black"
DEFAULT_IMAGE_MODE = "Black out pixels"
DEFAULT_VECTOR_MODE = "Remove if covered"

Box = tuple[float, float, float, float]
BoxMap = dict[int, list[Box]]


class FlowLayout(QLayout):
    """A layout that arranges child widgets left-to-right and wraps to the next
    row when it runs out of horizontal space (like text wrapping). This keeps
    the controls fully visible at any window width instead of clipping.
    """

    def __init__(self, parent=None, margin: int = 0, hspacing: int = 8, vspacing: int = 8):
        super().__init__(parent)
        self._items: list[QWidgetItem] = []
        self._hspace = hspacing
        self._vspace = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self):
        while self._items:
            self._items.pop()

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._hspace
            if next_x - self._hspace > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._vspace
                next_x = x + hint.width() + self._hspace
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


# --------------------------------------------------------------------------
# Redaction core
# --------------------------------------------------------------------------
def normalize_box(box: Box) -> Box:
    """Return the box with x0 <= x1 and y0 <= y1."""
    x0, y0, x1, y1 = box
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def safe_render_zoom(rect: "fitz.Rect", zoom: float) -> float:
    """Clamp a render zoom so the resulting bitmap stays within sane limits."""
    width = max(rect.width, 1.0)
    height = max(rect.height, 1.0)
    pixels = width * zoom * height * zoom
    if pixels > MAX_RENDER_PIXELS:
        zoom *= (MAX_RENDER_PIXELS / pixels) ** 0.5
    zoom = min(zoom, MAX_RENDER_SIDE / width, MAX_RENDER_SIDE / height)
    return max(zoom, 0.02)


def render_page_pixmap(
    page: "fitz.Page", zoom: float, dpr: float, clip: "fitz.Rect | None" = None
) -> QPixmap:
    """Render a page (or `clip` region of it) to a HiDPI-aware QPixmap.

    `zoom` is in screen pixels per PDF point; it is reduced if the request
    would produce an unreasonably large bitmap.
    """
    area = page.rect if clip is None else clip
    zoom = safe_render_zoom(area, zoom * dpr)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    img = QImage(
        pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
    ).copy()
    pixmap = QPixmap.fromImage(img)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def display_box_to_annot_rect(page: "fitz.Page", box: Box) -> fitz.Rect:
    """Convert a box from displayed page space to annotation space.

    The GUI works in the page's *displayed* coordinate space — exactly what
    `page.get_pixmap()` renders: origin at the top-left of the visible page,
    units in points, page rotation already applied. Annotations, on the other
    hand, live in the page's unrotated space, so a rotated page needs the
    derotation matrix applied before the rect can be used.
    """
    x0, y0, x1, y1 = normalize_box(box)
    rect = fitz.Rect(x0, y0, x1, y1) & page.rect
    return rect * page.derotation_matrix


def apply_page_redactions(page: "fitz.Page", images_mode: int, vector_mode: int) -> None:
    """Apply a page's redaction annotations across PyMuPDF versions.

    `graphics` (vector art handling) only exists in newer releases, so fall
    back to progressively simpler call signatures.
    """
    try:
        page.apply_redactions(images=images_mode, graphics=vector_mode)
        return
    except TypeError:
        pass
    try:
        page.apply_redactions(images=images_mode)
        return
    except TypeError:
        pass
    page.apply_redactions()


def add_page_redactions(
    page: "fitz.Page",
    boxes: list[Box],
    fill: tuple[float, float, float] | None,
) -> int:
    """Add one redaction annotation per box. Returns how many were added."""
    added = 0
    for box in boxes:
        rect = display_box_to_annot_rect(page, box)
        if rect.is_empty or rect.width < MIN_BOX_PT or rect.height < MIN_BOX_PT:
            continue
        page.add_redact_annot(rect, fill=fill)
        added += 1
    return added


def redact_pdf(
    input_pdf: str,
    output_pdf: str,
    boxes: BoxMap,
    fill: tuple[float, float, float] | None = (0.0, 0.0, 0.0),
    images_mode: int = IMAGE_MODES[DEFAULT_IMAGE_MODE],
    vector_mode: int = VECTOR_MODES[DEFAULT_VECTOR_MODE],
) -> int:
    """Write a redacted copy of `input_pdf`, removing everything under `boxes`.

    `boxes` maps a 0-based page index to boxes in that page's displayed
    coordinate space (points). Returns the number of boxes applied.
    """
    in_path = Path(input_pdf)
    out_path = Path(output_pdf)
    # Saving onto the source file needs a temporary file: PyMuPDF keeps the
    # input open while writing.
    overwrite_input = out_path.resolve() == in_path.resolve()
    target = (
        out_path.with_name(f"{out_path.stem}._redact_tmp.pdf")
        if overwrite_input else out_path
    )

    try:
        doc = fitz.open(str(in_path))
        try:
            if doc.needs_pass:
                raise ValueError("Password-protected PDFs are not supported.")
            total = 0
            for page_index in sorted(boxes):
                page_boxes = boxes[page_index]
                if not page_boxes or not 0 <= page_index < doc.page_count:
                    continue
                page = doc[page_index]
                added = add_page_redactions(page, page_boxes, fill)
                if added:
                    apply_page_redactions(page, images_mode, vector_mode)
                    total += added
            if total == 0:
                raise ValueError("No redaction boxes to apply.")
            doc.save(str(target), garbage=4, deflate=True)
        finally:
            doc.close()
    except BaseException:
        if target != out_path:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    if overwrite_input:
        os.replace(target, out_path)
    return total


def redacted_page_document(
    doc: "fitz.Document",
    page_index: int,
    boxes: list[Box],
    fill: tuple[float, float, float] | None,
    images_mode: int,
    vector_mode: int,
) -> "fitz.Document":
    """Build a one-page document showing the redacted result of a page.

    The page is copied first (redaction is destructive), so the loaded source
    document is never modified. `insert_pdf` keeps rotation and cropbox, which
    means the GUI's display-space boxes stay valid in the copy.
    """
    single = fitz.open()
    single.insert_pdf(doc, from_page=page_index, to_page=page_index, annots=False)
    page = single[0]
    if add_page_redactions(page, boxes, fill):
        apply_page_redactions(page, images_mode, vector_mode)
    return single


def build_output_path(input_pdf_path: Path) -> Path:
    return input_pdf_path.parent / f"{input_pdf_path.stem}_redacted.pdf"


STYLESHEET = """
QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; }
QLabel { color: #cdd6f4; font-size: 13px; }
QLabel#Title { font-size: 22px; font-weight: 600; color: #89b4fa; }
QLabel#Subtitle { font-size: 12px; color: #a6adc8; }
QLabel#DropHint {
    border: 2px dashed #45475a; border-radius: 14px; padding: 40px;
    background-color: #181825; color: #7f849c; font-size: 15px;
}
QLabel#PagePlaceholder {
    background-color: #181825; border: 1px solid #313244; border-radius: 4px;
    color: #585b70; font-size: 12px;
}
QPushButton {
    background-color: #89b4fa; color: #1e1e2e; border: none;
    border-radius: 8px; padding: 10px 18px; font-weight: 600; font-size: 13px;
}
QPushButton:hover { background-color: #b4befe; }
QPushButton:disabled { background-color: #45475a; color: #7f849c; }
QPushButton#Secondary { background-color: #313244; color: #cdd6f4; }
QPushButton#Secondary:hover { background-color: #45475a; }
QPushButton#Danger { background-color: #f38ba8; color: #1e1e2e; }
QPushButton#Danger:hover { background-color: #f5a3bb; }
QSpinBox {
    background-color: #313244; color: #cdd6f4; border: 1px solid #45475a;
    border-radius: 6px; padding: 0 10px; min-width: 70px;
    font-size: 14px; font-weight: 600;
}
QSpinBox:focus { border-color: #89b4fa; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; height: 0; border: none; }
QComboBox {
    background-color: #313244; color: #cdd6f4; border: 1px solid #45475a;
    border-radius: 6px; padding: 4px 10px; min-width: 100px; font-size: 13px;
}
QComboBox:focus { border-color: #89b4fa; }
QComboBox QAbstractItemView {
    background-color: #313244; color: #cdd6f4; selection-background-color: #89b4fa;
    selection-color: #1e1e2e; border: 1px solid #45475a;
}
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

BOX_COLOR = QColor("#f38ba8")
BOX_FILL = QColor(243, 139, 168, 90)
BOX_FILL_SELECTED = QColor(243, 139, 168, 130)


class PageStack:
    """Geometry of every page of a document stacked vertically.

    Pages are centered horizontally and separated by `PAGE_GAP_PT`. All values
    are cheap to compute (they only need `page.rect`), which is what keeps the
    continuous canvas usable on documents with hundreds of pages — pixmaps are
    rendered later, on demand.
    """

    def __init__(self, doc: "fitz.Document", scale: float):
        self.scale = scale
        self.rects: list[fitz.Rect] = [page.rect for page in doc]
        self.width = max((r.width for r in self.rects), default = 1.0)
        self.tops: list[float] = []
        y = 0.0
        for rect in self.rects:
            self.tops.append(y)
            y += rect.height + PAGE_GAP_PT
        self.height = max(y - PAGE_GAP_PT, 1.0)

    @property
    def count(self) -> int:
        return len(self.rects)

    def scene_rect(self) -> QRectF:
        return QRectF(0, 0, self.width * self.scale, self.height * self.scale)

    def page_rect(self, index: int) -> QRectF:
        rect = self.rects[index]
        x = (self.width - rect.width) / 2.0
        return QRectF(
            x * self.scale,
            self.tops[index] * self.scale,
            rect.width * self.scale,
            rect.height * self.scale,
        )

    def band_count(self, index: int) -> int:
        """How many horizontal bands a page is rasterized in.

        Ordinary pages are a single band. Very tall pages (the output of
        Long_PDF_Maker.py, for instance) are split so that only the bands near
        the viewport are rasterized, and at full resolution.
        """
        height = self.rects[index].height
        return max(1, int((height + BAND_PT - 1) // BAND_PT))

    def band_clip(self, index: int, band: int) -> fitz.Rect:
        """Clip rectangle of a band, in the page's displayed point space."""
        rect = self.rects[index]
        y0 = rect.y0 + band * BAND_PT
        y1 = min(y0 + BAND_PT, rect.y1)
        return fitz.Rect(rect.x0, y0, rect.x1, y1)

    def band_scene_rect(self, index: int, band: int) -> QRectF:
        page = self.page_rect(index)
        rect = self.rects[index]
        clip = self.band_clip(index, band)
        return QRectF(
            page.left(),
            page.top() + (clip.y0 - rect.y0) * self.scale,
            page.width(),
            (clip.y1 - clip.y0) * self.scale,
        )

    def page_at(self, scene_y: float) -> int:
        """Index of the page containing (or nearest above) a scene y position."""
        if not self.rects:
            return 0
        y = scene_y / self.scale
        index = bisect.bisect_right(self.tops, y) - 1
        return min(max(index, 0), len(self.rects) - 1)

    def visible_range(self, scene_rect: QRectF) -> tuple[int, int]:
        """Inclusive page index range overlapping a scene rectangle."""
        if not self.rects:
            return (0, -1)
        first = self.page_at(scene_rect.top())
        last = self.page_at(scene_rect.bottom())
        return (first, max(first, last))

    def box_to_scene(self, index: int, box: Box) -> QRectF:
        page = self.page_rect(index)
        rect = self.rects[index]
        x0, y0, x1, y1 = normalize_box(box)
        return QRectF(
            page.left() + (x0 - rect.x0) * self.scale,
            page.top() + (y0 - rect.y0) * self.scale,
            (x1 - x0) * self.scale,
            (y1 - y0) * self.scale,
        )

    def box_from_scene(self, index: int, scene_box: QRectF) -> Box:
        page = self.page_rect(index)
        rect = self.rects[index]
        x0 = rect.x0 + (scene_box.left() - page.left()) / self.scale
        y0 = rect.y0 + (scene_box.top() - page.top()) / self.scale
        return (
            x0, y0,
            x0 + scene_box.width() / self.scale,
            y0 + scene_box.height() / self.scale,
        )


class RedactBoxItem(QGraphicsObject):
    """One redaction box drawn over a page: movable, resizable, deletable.

    The item's `pos()` is the box's top-left corner in scene pixels and its
    local rect is `(0, 0, w, h)`. Handle and grab sizes are expressed in screen
    pixels and converted to scene units using the view's current zoom, so they
    stay easy to hit at any zoom level. A box belongs to exactly one page and
    is clamped to that page's bounds.
    """

    HANDLE_PX = 9    # drawn handle size, in screen pixels
    GRAB_PX = 11     # grab tolerance around edges/corners, in screen pixels
    MIN_PX = 4       # minimum box size, in screen pixels

    boxGeometryChanged = pyqtSignal(int, int, QRectF)  # page, index, scene rect
    pressed = pyqtSignal(int, int)                     # page, index
    removeRequested = pyqtSignal(int, int)             # page, index
    dragStarted = pyqtSignal()

    def __init__(self, view: "RedactView", page_index: int, index: int, scene_rect: QRectF):
        super().__init__()
        self._view = view
        self._page_index = page_index
        self._index = index
        self._w = scene_rect.width()
        self._h = scene_rect.height()
        self._active_zone: str | None = None
        self._drag_last = QPointF()
        self._pending_snapshot = False
        self.setPos(scene_rect.topLeft())
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(100 + index)

    # --- geometry ---
    @property
    def page_index(self) -> int:
        return self._page_index

    @property
    def index(self) -> int:
        return self._index

    def _px(self, screen_px: float) -> float:
        """Convert a screen-pixel length into scene units at the current zoom."""
        return screen_px / max(self._view.view_scale(), 1e-6)

    def boundingRect(self) -> QRectF:
        m = self._px(self.GRAB_PX) + 1
        return QRectF(-m, -m, self._w + 2 * m, self._h + 2 * m)

    def box_rect(self) -> QRectF:
        return QRectF(self.pos().x(), self.pos().y(), self._w, self._h)

    def refresh_scale(self) -> None:
        """Re-measure handle sizes after the view zoom changed."""
        self.prepareGeometryChange()
        self.update()

    def _corner_points(self) -> dict[str, QPointF]:
        return {
            "tl": QPointF(0, 0),
            "tr": QPointF(self._w, 0),
            "bl": QPointF(0, self._h),
            "br": QPointF(self._w, self._h),
        }

    def _corner_at(self, pos: QPointF) -> str | None:
        grab = self._px(self.GRAB_PX)
        for name, pt in self._corner_points().items():
            if abs(pos.x() - pt.x()) <= grab and abs(pos.y() - pt.y()) <= grab:
                return name
        return None

    def _edge_at(self, pos: QPointF) -> str | None:
        grab = self._px(self.GRAB_PX)
        x, y = pos.x(), pos.y()
        in_x = grab <= x <= self._w - grab
        in_y = grab <= y <= self._h - grab
        if in_y and abs(x) <= grab:
            return "l"
        if in_y and abs(x - self._w) <= grab:
            return "r"
        if in_x and abs(y) <= grab:
            return "t"
        if in_x and abs(y - self._h) <= grab:
            return "b"
        return None

    # --- painting ---
    def paint(self, painter, option, widget=None):
        rect = QRectF(0, 0, self._w, self._h)
        selected = self.isSelected()
        painter.setBrush(QBrush(BOX_FILL_SELECTED if selected else BOX_FILL))
        pen = QPen(BOX_COLOR, 2 if selected else 1)
        pen.setCosmetic(True)
        if not selected:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(rect)

        if not selected:
            return
        size = self._px(self.HANDLE_PX)
        painter.setPen(QPen(QColor("#1e1e2e"), 1))
        painter.setBrush(QBrush(BOX_COLOR))
        for pt in self._corner_points().values():
            painter.drawRect(QRectF(pt.x() - size / 2, pt.y() - size / 2, size, size))
        bar = size * 1.6
        thick = size * 0.45
        cx, cy = self._w / 2, self._h / 2
        painter.drawRoundedRect(QRectF(-thick / 2, cy - bar / 2, thick, bar), 2, 2)
        painter.drawRoundedRect(QRectF(self._w - thick / 2, cy - bar / 2, thick, bar), 2, 2)
        painter.drawRoundedRect(QRectF(cx - bar / 2, -thick / 2, bar, thick), 2, 2)
        painter.drawRoundedRect(QRectF(cx - bar / 2, self._h - thick / 2, bar, thick), 2, 2)

    # --- cursor ---
    def _update_cursor(self, pos: QPointF):
        corner = self._corner_at(pos)
        if corner in ("tl", "br"):
            self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
            return
        if corner in ("tr", "bl"):
            self.setCursor(QCursor(Qt.CursorShape.SizeBDiagCursor))
            return
        edge = self._edge_at(pos)
        if edge in ("l", "r"):
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        elif edge in ("t", "b"):
            self.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

    def hoverMoveEvent(self, event):
        self._update_cursor(event.pos())
        super().hoverMoveEvent(event)

    # --- mouse ---
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        scene = self.scene()
        if scene is not None:
            scene.clearSelection()
        self.setSelected(True)
        self.pressed.emit(self._page_index, self._index)
        # Only record an undo state once the box really starts changing, so a
        # plain selection click does not land on the undo stack.
        self._pending_snapshot = True
        self._active_zone = self._corner_at(event.pos()) or self._edge_at(event.pos())
        self._drag_last = event.scenePos()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._pending_snapshot:
            self._pending_snapshot = False
            self.dragStarted.emit()
        if self._active_zone is not None:
            self._resize(event.pos())
        else:
            delta = event.scenePos() - self._drag_last
            self._drag_last = event.scenePos()
            self._move_by(delta)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._active_zone = None
        self._pending_snapshot = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.removeRequested.emit(self._page_index, self._index)
        event.accept()

    def _bounds(self) -> QRectF:
        return self._view.page_bounds(self._page_index)

    def _move_by(self, delta: QPointF):
        bounds = self._bounds()
        x = min(max(self.pos().x() + delta.x(), bounds.left()), bounds.right() - self._w)
        y = min(max(self.pos().y() + delta.y(), bounds.top()), bounds.bottom() - self._h)
        self.setPos(x, y)
        self.boxGeometryChanged.emit(self._page_index, self._index, self.box_rect())

    def _resize(self, pos: QPointF):
        """Resize the box; corners move two edges, single edges move one."""
        bounds = self._bounds()
        zone = self._active_zone or ""
        min_size = self._px(self.MIN_PX)
        left = self.pos().x()
        top = self.pos().y()
        right = left + self._w
        bottom = top + self._h
        x = min(max(left + pos.x(), bounds.left()), bounds.right())
        y = min(max(top + pos.y(), bounds.top()), bounds.bottom())
        if "l" in zone:
            left = min(x, right - min_size)
        if "r" in zone:
            right = max(x, left + min_size)
        if "t" in zone:
            top = min(y, bottom - min_size)
        if "b" in zone:
            bottom = max(y, top + min_size)

        self.prepareGeometryChange()
        self._w = right - left
        self._h = bottom - top
        self.setPos(left, top)
        self.update()
        self.boxGeometryChanged.emit(self._page_index, self._index, self.box_rect())


class RedactView(QGraphicsView):
    """Continuous editing canvas: every page stacked vertically, scroll freely.

    Dragging empty space draws a new box on the page the drag started on;
    dragging an existing box moves or resizes it within its own page. Page
    bitmaps are rendered lazily for the visible range only. All coordinates
    leaving this class are (page index, box in that page's displayed point
    space).
    """

    boxAdded = pyqtSignal(int, float, float, float, float)
    boxEdited = pyqtSignal(int, int, float, float, float, float)
    boxRemoveRequested = pyqtSignal(int, int)
    selectionChanged = pyqtSignal(int, int)  # page, index; (-1, -1) = nothing
    currentPageChanged = pyqtSignal(int)
    dragStarted = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
        )
        self.setMinimumSize(220, 220)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self._doc: fitz.Document | None = None
        self._stack: PageStack | None = None
        # (page index, band index) -> rendered bitmap currently in the scene
        self._page_items: dict[tuple[int, int], QGraphicsPixmapItem] = {}
        self._box_items: list[RedactBoxItem] = []
        self._rubber: QGraphicsRectItem | None = None
        self._draw_page = 0
        self._draw_origin = QPointF()
        self._zoom: float | None = None  # None = fit page width
        self._current_page = 0

        self._lazy_timer = QTimer(self)
        self._lazy_timer.setSingleShot(True)
        self._lazy_timer.setInterval(40)
        self._lazy_timer.timeout.connect(self._refresh_visible)
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._lazy_timer.start)

    # --- coordinate helpers ---
    def view_scale(self) -> float:
        """Screen pixels per scene unit at the current zoom."""
        return abs(self.transform().m11()) or 1.0

    def has_document(self) -> bool:
        return self._stack is not None

    def page_bounds(self, page_index: int) -> QRectF:
        if self._stack is None:
            return QRectF()
        return self._stack.page_rect(page_index)

    def current_page(self) -> int:
        return self._current_page

    def scene_rect_from_box(self, page_index: int, box: Box) -> QRectF:
        if self._stack is None:
            return QRectF()
        return self._stack.box_to_scene(page_index, box)

    def box_from_scene_rect(self, page_index: int, rect: QRectF) -> Box:
        if self._stack is None:
            return (0.0, 0.0, 0.0, 0.0)
        return self._stack.box_from_scene(page_index, rect)

    # --- document ---
    def load_document(self, doc: "fitz.Document"):
        self._scene.clear()
        self._page_items.clear()
        self._box_items.clear()
        self._rubber = None
        self._doc = doc
        self._current_page = 0
        self._stack = PageStack(doc, EDITOR_RENDER_DPI / 72.0)
        self._scene.setSceneRect(self._stack.scene_rect())

        # Page frames and numbers are cheap, so draw them for every page up
        # front; the bitmaps themselves are filled in lazily while scrolling.
        frame_pen = QPen(QColor("#313244"))
        frame_pen.setCosmetic(True)
        frame_brush = QBrush(QColor("#f5f5f5"))
        for index in range(self._stack.count):
            rect = self._stack.page_rect(index)
            frame = self._scene.addRect(rect, frame_pen, frame_brush)
            frame.setZValue(0)
            label = QGraphicsSimpleTextItem(f"Page {index + 1}")
            label.setBrush(QBrush(QColor("#7f849c")))
            label.setFont(QFont("Inter, Segoe UI, SF Pro Display, Helvetica", 8))
            # Ignoring the view transform keeps the caption legible at any zoom;
            # its own transform then lifts it into the gap above the page.
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            label.setPos(rect.left(), rect.top())
            label.setTransform(QTransform.fromTranslate(0, -16))
            label.setZValue(50)
            self._scene.addItem(label)

        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)
        self._apply_zoom()
        self._refresh_visible()

    def set_boxes(self, boxes: BoxMap, selected: tuple[int, int] | None = None):
        """Rebuild every box item from the model."""
        for item in self._box_items:
            self._scene.removeItem(item)
        self._box_items.clear()
        if self._stack is None:
            return
        for page_index in sorted(boxes):
            if not 0 <= page_index < self._stack.count:
                continue
            for index, box in enumerate(boxes[page_index]):
                item = RedactBoxItem(
                    self, page_index, index,
                    self._stack.box_to_scene(page_index, box),
                )
                item.boxGeometryChanged.connect(self._on_item_geometry)
                item.pressed.connect(self.selectionChanged)
                item.removeRequested.connect(self.boxRemoveRequested)
                item.dragStarted.connect(self.dragStarted)
                self._scene.addItem(item)
                self._box_items.append(item)
        if selected is not None:
            for item in self._box_items:
                if (item.page_index, item.index) == selected:
                    item.setSelected(True)
                    self.selectionChanged.emit(*selected)
                    return
        self.selectionChanged.emit(-1, -1)

    def selected_box(self) -> tuple[int, int] | None:
        for item in self._box_items:
            if item.isSelected():
                return (item.page_index, item.index)
        return None

    def _on_item_geometry(self, page_index: int, index: int, rect: QRectF):
        box = self.box_from_scene_rect(page_index, rect)
        self.boxEdited.emit(page_index, index, *box)

    # --- lazy page rendering ---
    def _visible_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def _on_scrolled(self):
        self._lazy_timer.start()
        self._update_current_page()

    def _update_current_page(self):
        if self._stack is None:
            return
        visible = self._visible_scene_rect()
        page = self._stack.page_at(visible.center().y())
        if page != self._current_page:
            self._current_page = page
            self.currentPageChanged.emit(page)

    def _refresh_visible(self):
        if self._stack is None or self._doc is None:
            return
        visible = self._visible_scene_rect()
        margin = visible.height() * LAZY_MARGIN
        window = visible.adjusted(0, -margin, 0, margin)
        first, last = self._stack.visible_range(window)
        wanted: set[tuple[int, int]] = set()
        for index in range(first, last + 1):
            for band in range(self._stack.band_count(index)):
                if self._stack.band_scene_rect(index, band).intersects(window):
                    wanted.add((index, band))
        for key in list(self._page_items):
            if key not in wanted:
                self._scene.removeItem(self._page_items.pop(key))
        for key in sorted(wanted):
            if key not in self._page_items:
                self._page_items[key] = self._render_band(*key)

    def _render_band(self, index: int, band: int) -> QGraphicsPixmapItem:
        assert self._doc is not None and self._stack is not None
        page = self._doc[index]
        dpr = max(1.0, float(self.devicePixelRatioF()))
        clip = self._stack.band_clip(index, band)
        pixmap = render_page_pixmap(page, self._stack.scale, dpr, clip=clip)
        item = self._scene.addPixmap(pixmap)
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        target = self._stack.band_scene_rect(index, band)
        logical_w = pixmap.width() / max(pixmap.devicePixelRatio(), 1e-6)
        if logical_w > 0:
            item.setScale(target.width() / logical_w)
        item.setPos(target.topLeft())
        item.setZValue(1)
        return item

    # --- zoom + navigation ---
    def set_zoom(self, zoom: float | None):
        """`zoom` is screen pixels per PDF point; None fits the page width."""
        self._zoom = zoom
        self._apply_zoom()

    def _apply_zoom(self):
        if self._stack is None:
            return
        anchor = self._anchor_point()
        if self._zoom is None:
            available = max(self.viewport().width() - 28, 80)
            factor = available / (self._stack.width * self._stack.scale)
        else:
            factor = self._zoom / self._stack.scale
        self.setTransform(QTransform.fromScale(factor, factor))
        for item in self._box_items:
            item.refresh_scale()
        if anchor is not None:
            self.centerOn(anchor)
        self._lazy_timer.start()
        self._update_current_page()

    def _anchor_point(self) -> QPointF | None:
        """Scene point to keep centered across a zoom change."""
        if self._stack is None:
            return None
        visible = self._visible_scene_rect()
        if visible.isEmpty():
            return None
        return visible.center()

    def scroll_to_page(self, index: int):
        if self._stack is None or not 0 <= index < self._stack.count:
            return
        rect = self._stack.page_rect(index)
        visible = self._visible_scene_rect()
        # Put the page top near the viewport top rather than its center.
        target_y = rect.top() + visible.height() / 2 - PAGE_GAP_PT * self._stack.scale
        self.centerOn(QPointF(rect.center().x(), target_y))
        self._current_page = index
        self.currentPageChanged.emit(index)
        self._refresh_visible()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._stack is None:
            return
        if self._zoom is None:
            self._apply_zoom()
        else:
            self._lazy_timer.start()
            self._update_current_page()

    # --- drawing new boxes ---
    def mousePressEvent(self, event):
        if self._stack is None or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()
        if isinstance(self.itemAt(pos), RedactBoxItem):
            super().mousePressEvent(event)
            return
        scene_pos = self.mapToScene(pos)
        self._scene.clearSelection()
        self.selectionChanged.emit(-1, -1)
        self._draw_page = self._stack.page_at(scene_pos.y())
        self._draw_origin = self._clamp_to_page(self._draw_page, scene_pos)
        self._rubber = QGraphicsRectItem(QRectF(self._draw_origin, self._draw_origin))
        pen = QPen(BOX_COLOR, 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._rubber.setPen(pen)
        self._rubber.setBrush(QBrush(BOX_FILL_SELECTED))
        self._rubber.setZValue(1000)
        self._scene.addItem(self._rubber)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._rubber is None:
            super().mouseMoveEvent(event)
            return
        current = self._clamp_to_page(
            self._draw_page, self.mapToScene(event.position().toPoint())
        )
        self._rubber.setRect(QRectF(self._draw_origin, current).normalized())
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._rubber is None:
            super().mouseReleaseEvent(event)
            return
        rect = self._rubber.rect()
        self._scene.removeItem(self._rubber)
        self._rubber = None
        box = self.box_from_scene_rect(self._draw_page, rect)
        if (box[2] - box[0]) >= MIN_BOX_PT and (box[3] - box[1]) >= MIN_BOX_PT:
            self.boxAdded.emit(self._draw_page, *box)
        event.accept()

    def _clamp_to_page(self, page_index: int, point: QPointF) -> QPointF:
        bounds = self.page_bounds(page_index)
        return QPointF(
            min(max(point.x(), bounds.left()), bounds.right()),
            min(max(point.y(), bounds.top()), bounds.bottom()),
        )


class PreviewPane(QScrollArea):
    """Continuously scrolling preview of the redacted result, page by page.

    Each page is a label sized from the page's aspect ratio, so the scroll
    range is correct immediately; the bitmaps are produced lazily for the
    visible range and re-rendered when that page's boxes or the redaction
    options change.
    """

    # Width changes smaller than this are ignored; re-rendering every page on a
    # one-pixel layout nudge would be wasteful.
    WIDTH_TOLERANCE = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setMinimumSize(220, 220)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # Reserving the scrollbar permanently keeps the usable width constant:
        # otherwise the first rendered page makes the scrollbar appear, which
        # changes the width, which invalidates the render that just happened.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(14)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.setWidget(self._container)

        self._doc: fitz.Document | None = None
        self._boxes_for = lambda index: []
        self._options = lambda: (FILL_COLORS[DEFAULT_FILL],
                                 IMAGE_MODES[DEFAULT_IMAGE_MODE],
                                 VECTOR_MODES[DEFAULT_VECTOR_MODE])
        self._labels: list[QLabel] = []
        self._dirty: set[int] = set()
        self._rendered: set[int] = set()
        self._zoom = 1.0
        self._last_width = 0
        self._scroll_target: int | None = None
        self._show_placeholder()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._render_visible)
        self.verticalScrollBar().valueChanged.connect(self._timer.start)

    # --- setup ---
    def set_providers(self, boxes_for, options):
        self._boxes_for = boxes_for
        self._options = options

    def _show_placeholder(self):
        """Message shown while no document is loaded."""
        label = QLabel("Load a PDF to see the preview.")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #7f849c; padding: 40px;")
        self._layout.addWidget(label)

    def set_document(self, doc: "fitz.Document | None"):
        self._doc = doc
        self._scroll_target = None
        self._clear_labels()
        self._rendered.clear()
        self._dirty.clear()
        if doc is None:
            self._show_placeholder()
            return
        for index in range(doc.page_count):
            label = QLabel(f"Page {index + 1}")
            label.setObjectName("PagePlaceholder")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
            self._labels.append(label)
        self._dirty = set(range(doc.page_count))
        self._resize_labels(force=True)
        self._timer.start()

    def _clear_labels(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._labels.clear()

    # --- invalidation ---
    def invalidate(self, pages: list[int] | None = None):
        if self._doc is None:
            return
        if pages is None:
            self._dirty = set(range(self._doc.page_count))
            self._rendered.clear()
        else:
            for index in pages:
                if 0 <= index < self._doc.page_count:
                    self._dirty.add(index)
                    self._rendered.discard(index)
        self._timer.start()

    def _offsets(self) -> list[tuple[int, int]]:
        """Top/bottom of every page label inside the scrolled container.

        Computed from the labels' fixed sizes rather than read back from the
        widgets: positions are only valid once Qt has run the layout, and the
        lazy renderer needs to know what is visible before that happens.
        """
        margins = self._layout.contentsMargins()
        spacing = self._layout.spacing()
        top = margins.top()
        offsets: list[tuple[int, int]] = []
        for label in self._labels:
            height = label.height()
            offsets.append((top, top + height))
            top += height + spacing
        return offsets

    def scroll_to_page(self, index: int):
        """Scroll so a page starts at the top of the pane."""
        if not 0 <= index < len(self._labels):
            return
        self._scroll_target = index
        # Scroll now for the common case, then again once Qt has processed the
        # pending layout: until it does, the scrollbar range can still be stale
        # and setValue() would clamp a long jump to the old maximum.
        self._apply_scroll_target()
        QTimer.singleShot(0, self._apply_scroll_target)

    def _apply_scroll_target(self):
        index = self._scroll_target
        if index is None or not 0 <= index < len(self._labels):
            return
        self._layout.activate()
        bar = self.verticalScrollBar()
        top = int(self._offsets()[index][0] - self._layout.contentsMargins().top())
        bar.setValue(min(top, bar.maximum()))
        self._timer.start()

    def visible_pages(self) -> list[int]:
        if not self._labels:
            return []
        view_top = self.verticalScrollBar().value()
        height = self.viewport().height()
        view_bottom = view_top + height
        margin = height * LAZY_MARGIN
        return [
            index for index, (top, bottom) in enumerate(self._offsets())
            if bottom >= view_top - margin and top <= view_bottom + margin
        ]

    # --- rendering ---
    def resizeEvent(self, event):
        super().resizeEvent(event)
        changed = abs(self.viewport().width() - self._last_width)
        if self._doc is not None and changed > self.WIDTH_TOLERANCE:
            self._resize_labels(force=True)
            self.invalidate()

    def _page_zoom(self, index: int) -> float:
        """Preview zoom for a page: fit the pane width, within render limits."""
        assert self._doc is not None
        rect = self._doc[index].rect
        width = max(self.viewport().width() - 48, 160)
        zoom = min(width / max(rect.width, 1.0), PREVIEW_MAX_ZOOM)
        return safe_render_zoom(rect, zoom)

    def _page_display_size(self, index: int) -> QSize:
        assert self._doc is not None
        rect = self._doc[index].rect
        self._zoom = self._page_zoom(index)
        return QSize(
            max(int(rect.width * self._zoom), 1),
            max(int(rect.height * self._zoom), 1),
        )

    def _resize_labels(self, force: bool = False):
        if self._doc is None:
            return
        self._last_width = self.viewport().width()
        for index, label in enumerate(self._labels):
            size = self._page_display_size(index)
            if force or label.size() != size:
                label.setFixedSize(size)
                if index in self._rendered:
                    self._rendered.discard(index)
                    self._dirty.add(index)
                    label.setPixmap(QPixmap())
                    label.setText(f"Page {index + 1}")

    def _render_visible(self):
        if self._doc is None:
            return
        visible = self.visible_pages()
        # Drop bitmaps that scrolled far away so memory stays bounded.
        for index in list(self._rendered):
            if index not in visible:
                self._rendered.discard(index)
                self._dirty.add(index)
                label = self._labels[index]
                label.setPixmap(QPixmap())
                label.setText(f"Page {index + 1}")
        for index in visible:
            if index in self._rendered:
                continue
            try:
                pixmap = self._render_page(index)
            except Exception as error:
                self._labels[index].setText(f"Page {index + 1}: {error}")
                self._rendered.add(index)
                self._dirty.discard(index)
                continue
            label = self._labels[index]
            label.setText("")
            label.setPixmap(pixmap)
            # Keep the slot exactly as large as what was actually rendered, so
            # clamped renders of very tall pages still line up.
            label.setFixedSize(pixmap.deviceIndependentSize().toSize())
            self._rendered.add(index)
            self._dirty.discard(index)

    def _render_page(self, index: int) -> QPixmap:
        assert self._doc is not None
        fill, images_mode, vector_mode = self._options()
        single = redacted_page_document(
            self._doc, index, list(self._boxes_for(index)), fill, images_mode, vector_mode
        )
        try:
            dpr = max(1.0, float(self.devicePixelRatioF()))
            return render_page_pixmap(single[0], self._page_zoom(index), dpr)
        finally:
            single.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("J Redact PDF")
        self.resize(1320, 860)
        self.setMinimumSize(520, 460)
        self.setAcceptDrops(True)

        self._doc: fitz.Document | None = None
        self._input_path: Path | None = None
        self._boxes: BoxMap = {}
        self._undo_stack: list[BoxMap] = []
        self._redo_stack: list[BoxMap] = []
        self._syncing_page = False

        self._build_ui()
        self._update_actions()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("J Redact PDF")
        title.setObjectName("Title")
        subtitle = QLabel(
            "Scroll the whole document, draw boxes over anything secret, then save "
            "a copy with that content permanently removed."
        )
        subtitle.setObjectName("Subtitle")
        header.addWidget(title)
        header.addWidget(subtitle)
        root.addLayout(header)

        # --- Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.open_btn = QPushButton("Open PDF")
        self.open_btn.setObjectName("Secondary")
        self.open_btn.setShortcut("Ctrl+O")
        self.open_btn.setToolTip("Open a PDF (Ctrl+O)")
        self.open_btn.clicked.connect(self._pick_file)
        toolbar.addWidget(self.open_btn)

        self.file_label = QLabel("No file loaded")
        self.file_label.setObjectName("Subtitle")
        self.file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(self.file_label, 1)
        toolbar.addStretch(1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("Subtitle")
        toolbar.addWidget(self.count_label)

        self.save_btn = QPushButton("Save redacted PDF")
        self.save_btn.setEnabled(False)
        self.save_btn.setShortcut("Ctrl+S")
        self.save_btn.setToolTip("Save <name>_redacted.pdf (Ctrl+S)")
        self.save_btn.clicked.connect(self._save_output)
        toolbar.addWidget(self.save_btn)
        root.addLayout(toolbar)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)

        # --- Editor card ---
        editor_card = QFrame()
        editor_card.setObjectName("Card")
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(14, 14, 14, 14)
        editor_layout.setSpacing(8)
        editor_layout.addWidget(QLabel(
            "Editor — scroll through all pages; drag to draw a box, drag a box to "
            "move it, drag handles to resize, double-click or Del to delete"
        ))

        self.drop_hint = QLabel("Drop a PDF here, or click \u201cOpen PDF\u201d")
        self.drop_hint.setObjectName("DropHint")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        editor_layout.addWidget(self.drop_hint, 1)

        self.view = RedactView()
        self.view.hide()
        self.view.boxAdded.connect(self._on_box_added)
        self.view.boxEdited.connect(self._on_box_edited)
        self.view.boxRemoveRequested.connect(self._remove_box)
        self.view.selectionChanged.connect(self._on_selection_changed)
        self.view.currentPageChanged.connect(self._on_view_page_changed)
        self.view.dragStarted.connect(self._snapshot)
        editor_layout.addWidget(self.view, 1)
        body.addWidget(editor_card, 1)

        # --- Preview card ---
        preview_card = QFrame()
        preview_card.setObjectName("Card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(14, 14, 14, 14)
        preview_layout.setSpacing(8)
        self.preview_title = QLabel("Live preview — redacted result, all pages")
        preview_layout.addWidget(self.preview_title)

        self.preview = PreviewPane()
        self.preview.set_providers(self._boxes_for_page, self._preview_options)
        preview_layout.addWidget(self.preview, 1)
        body.addWidget(preview_card, 1)

        # --- Controls (wrap to new rows when the window is narrow) ---
        controls_card = QFrame()
        controls_card.setObjectName("Card")
        controls_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        controls = FlowLayout(controls_card, margin=10, hspacing=10, vspacing=8)

        self.prev_btn = QPushButton("\u25c0")
        self.prev_btn.setObjectName("Stepper")
        self.prev_btn.setFixedSize(34, 34)
        self.prev_btn.setShortcut("PgUp")
        self.prev_btn.setToolTip("Scroll to the previous page (PgUp)")
        self.prev_btn.clicked.connect(lambda: self._goto_page(self._current_page() - 1))
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setFixedHeight(34)
        self.page_spin.setMinimumWidth(70)
        self.page_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.page_spin.setToolTip("Jump to a page")
        self.page_spin.valueChanged.connect(self._on_page_spin_changed)
        self.page_total_label = QLabel("/ 1")
        self.next_btn = QPushButton("\u25b6")
        self.next_btn.setObjectName("Stepper")
        self.next_btn.setFixedSize(34, 34)
        self.next_btn.setShortcut("PgDown")
        self.next_btn.setToolTip("Scroll to the next page (PgDown)")
        self.next_btn.clicked.connect(lambda: self._goto_page(self._current_page() + 1))
        controls.addWidget(self._group(
            "Page", self.prev_btn, self.page_spin, self.page_total_label, self.next_btn
        ))

        self.sync_btn = QPushButton("Sync preview: ON")
        self.sync_btn.setObjectName("Secondary")
        self.sync_btn.setCheckable(True)
        self.sync_btn.setChecked(True)
        self.sync_btn.setToolTip("Scroll the preview to follow the editor")
        self.sync_btn.toggled.connect(self._on_sync_toggled)
        controls.addWidget(self.sync_btn)

        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["Fit width", "50%", "75%", "100%", "150%", "200%", "300%"])
        self.zoom_combo.setToolTip("Editor zoom")
        self.zoom_combo.currentIndexChanged.connect(self._on_zoom_changed)
        controls.addWidget(self._group("Zoom", self.zoom_combo))

        self.fill_combo = QComboBox()
        self.fill_combo.addItems(FILL_COLORS.keys())
        self.fill_combo.setCurrentText(DEFAULT_FILL)
        self.fill_combo.setToolTip("Color painted over each redacted area")
        self.fill_combo.currentIndexChanged.connect(self._on_option_changed)
        controls.addWidget(self._group("Fill", self.fill_combo))

        self.image_combo = QComboBox()
        self.image_combo.addItems(IMAGE_MODES.keys())
        self.image_combo.setCurrentText(DEFAULT_IMAGE_MODE)
        self.image_combo.setToolTip("What happens to images under a box")
        self.image_combo.currentIndexChanged.connect(self._on_option_changed)
        controls.addWidget(self._group("Images", self.image_combo))

        self.vector_combo = QComboBox()
        self.vector_combo.addItems(VECTOR_MODES.keys())
        self.vector_combo.setCurrentText(DEFAULT_VECTOR_MODE)
        self.vector_combo.setToolTip("What happens to vector drawings under a box")
        self.vector_combo.currentIndexChanged.connect(self._on_option_changed)
        controls.addWidget(self._group("Vector art", self.vector_combo))

        self.delete_btn = QPushButton("Delete box")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.setShortcut("Del")
        self.delete_btn.setToolTip("Delete the selected box (Del)")
        self.delete_btn.clicked.connect(self._delete_selected)
        controls.addWidget(self.delete_btn)

        self.copy_btn = QPushButton("Copy to all pages")
        self.copy_btn.setObjectName("Secondary")
        self.copy_btn.setToolTip("Put this page's boxes on every page (headers, stamps, ...)")
        self.copy_btn.clicked.connect(self._copy_to_all_pages)
        controls.addWidget(self.copy_btn)

        self.clear_page_btn = QPushButton("Clear page")
        self.clear_page_btn.setObjectName("Secondary")
        self.clear_page_btn.clicked.connect(self._clear_page)
        controls.addWidget(self.clear_page_btn)

        self.clear_all_btn = QPushButton("Clear all")
        self.clear_all_btn.setObjectName("Secondary")
        self.clear_all_btn.clicked.connect(self._clear_all)
        controls.addWidget(self.clear_all_btn)

        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setObjectName("Secondary")
        self.undo_btn.setShortcut("Ctrl+Z")
        self.undo_btn.setToolTip("Undo last change (Ctrl+Z)")
        self.undo_btn.clicked.connect(self._undo)
        controls.addWidget(self.undo_btn)

        self.redo_btn = QPushButton("Redo")
        self.redo_btn.setObjectName("Secondary")
        self.redo_btn.setShortcut("Ctrl+Y")
        self.redo_btn.setToolTip("Redo last undone change (Ctrl+Y)")
        self.redo_btn.clicked.connect(self._redo)
        controls.addWidget(self.redo_btn)

        root.addWidget(controls_card)

        self.status = QLabel("")
        self.status.setObjectName("Subtitle")
        root.addWidget(self.status)

    def _group(self, label: str, *widgets: QWidget) -> QWidget:
        """Bundle a caption label with its input(s) so they wrap together."""
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(QLabel(f"{label}:"))
        for widget in widgets:
            lay.addWidget(widget)
        return box

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent):
        if any(
            url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".pdf":
                self.load_pdf(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF Files (*.pdf)")
        if path:
            self.load_pdf(Path(path))

    def load_pdf(self, path: Path):
        doc: fitz.Document | None = None
        try:
            doc = fitz.open(str(path))
            if doc.page_count == 0:
                raise ValueError("PDF has no pages.")
            if doc.needs_pass:
                raise ValueError("Password-protected PDFs are not supported.")
            # Read the first page now so damaged files fail before the old one
            # is replaced.
            first_rect = doc[0].rect
        except Exception as error:
            if doc is not None:
                doc.close()
            QMessageBox.critical(self, "Open failed", f"Could not open PDF:\n{error}")
            return

        self.preview.set_document(None)
        if self._doc is not None:
            self._doc.close()
        self._doc = doc
        self._input_path = path
        self._boxes = {}
        self._undo_stack.clear()
        self._redo_stack.clear()

        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1, doc.page_count)
        self.page_spin.setValue(1)
        self.page_spin.blockSignals(False)
        self.page_total_label.setText(f"/ {doc.page_count}")

        self.drop_hint.hide()
        self.view.show()
        self.view.load_document(doc)
        self.view.set_boxes(self._boxes)
        self.preview.set_document(doc)
        self.file_label.setText(f"{path.name}  \u2022  {doc.page_count} page(s)")
        self.status.setText(
            f"Loaded {path.name}  \u2022  {doc.page_count} page(s)  \u2022  first page "
            f"{first_rect.width:.0f} \u00d7 {first_rect.height:.0f} pt. "
            "Scroll and drag on any page to draw redaction boxes."
        )
        self._update_actions()

    # ------------------------------------------------------------------
    # Box model
    # ------------------------------------------------------------------
    def _current_page(self) -> int:
        return self.view.current_page() if self._doc is not None else 0

    def _boxes_for_page(self, page_index: int) -> list[Box]:
        return self._boxes.get(page_index, [])

    def _preview_options(self):
        return (self._fill(), self._images_mode(), self._vector_mode())

    def _total_boxes(self) -> int:
        return sum(len(v) for v in self._boxes.values())

    def _snapshot(self):
        self._undo_stack.append(copy.deepcopy(self._boxes))
        if len(self._undo_stack) > UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_actions()

    def _restore(self, state: BoxMap):
        changed = sorted(set(state) | set(self._boxes))
        self._boxes = copy.deepcopy(state)
        self.view.set_boxes(self._boxes)
        self.preview.invalidate(changed)
        self._update_actions()

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(copy.deepcopy(self._boxes))
        self._restore(self._undo_stack.pop())

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(copy.deepcopy(self._boxes))
        self._restore(self._redo_stack.pop())

    def _on_box_added(self, page_index: int, x0: float, y0: float, x1: float, y1: float):
        self._snapshot()
        boxes = self._boxes.setdefault(page_index, [])
        boxes.append(normalize_box((x0, y0, x1, y1)))
        self.view.set_boxes(self._boxes, selected=(page_index, len(boxes) - 1))
        self.preview.invalidate([page_index])
        self._update_actions()

    def _on_box_edited(
        self, page_index: int, index: int, x0: float, y0: float, x1: float, y1: float
    ):
        boxes = self._boxes.get(page_index)
        if boxes is None or not 0 <= index < len(boxes):
            return
        boxes[index] = normalize_box((x0, y0, x1, y1))
        self.preview.invalidate([page_index])

    def _remove_box(self, page_index: int, index: int):
        boxes = self._boxes.get(page_index)
        if boxes is None or not 0 <= index < len(boxes):
            return
        self._snapshot()
        boxes.pop(index)
        self.view.set_boxes(self._boxes)
        self.preview.invalidate([page_index])
        self._update_actions()

    def _delete_selected(self):
        selected = self.view.selected_box()
        if selected is None:
            self.status.setText("Select a box first, then press Del.")
            return
        self._remove_box(*selected)

    def _clear_page(self):
        page_index = self._current_page()
        if not self._boxes_for_page(page_index):
            return
        self._snapshot()
        self._boxes[page_index] = []
        self.view.set_boxes(self._boxes)
        self.preview.invalidate([page_index])
        self._update_actions()

    def _clear_all(self):
        if self._total_boxes() == 0:
            return
        self._snapshot()
        self._boxes = {}
        self.view.set_boxes(self._boxes)
        self.preview.invalidate()
        self._update_actions()

    def _copy_to_all_pages(self):
        if self._doc is None:
            return
        page_index = self._current_page()
        source = list(self._boxes_for_page(page_index))
        if not source:
            self.status.setText("Draw at least one box on this page first.")
            return
        self._snapshot()
        for index in range(self._doc.page_count):
            if index == page_index:
                continue
            page_rect = self._doc[index].rect
            clipped = [
                normalize_box((
                    min(max(box[0], page_rect.x0), page_rect.x1),
                    min(max(box[1], page_rect.y0), page_rect.y1),
                    min(max(box[2], page_rect.x0), page_rect.x1),
                    min(max(box[3], page_rect.y0), page_rect.y1),
                ))
                for box in source
            ]
            kept = [
                box for box in clipped
                if (box[2] - box[0]) >= MIN_BOX_PT and (box[3] - box[1]) >= MIN_BOX_PT
            ]
            self._boxes.setdefault(index, []).extend(kept)
        self.view.set_boxes(self._boxes)
        self.preview.invalidate()
        self._update_actions()
        self.status.setText(
            f"Copied {len(source)} box(es) from page {page_index + 1} to the other "
            f"{self._doc.page_count - 1} page(s)."
        )

    # ------------------------------------------------------------------
    # Options / navigation
    # ------------------------------------------------------------------
    def _fill(self) -> tuple[float, float, float] | None:
        return FILL_COLORS[self.fill_combo.currentText()]

    def _images_mode(self) -> int:
        return IMAGE_MODES[self.image_combo.currentText()]

    def _vector_mode(self) -> int:
        return VECTOR_MODES[self.vector_combo.currentText()]

    def _goto_page(self, page_index: int):
        if self._doc is None:
            return
        page_index = min(max(page_index, 0), self._doc.page_count - 1)
        self.view.scroll_to_page(page_index)
        if self.sync_btn.isChecked():
            self.preview.scroll_to_page(page_index)

    def _on_page_spin_changed(self, value: int):
        if self._doc is None or self._syncing_page:
            return
        self._goto_page(value - 1)

    def _on_view_page_changed(self, page_index: int):
        self._syncing_page = True
        self.page_spin.setValue(page_index + 1)
        self._syncing_page = False
        if self.sync_btn.isChecked():
            self.preview.scroll_to_page(page_index)
        self._update_actions()

    def _on_sync_toggled(self, checked: bool):
        self.sync_btn.setText(f"Sync preview: {'ON' if checked else 'OFF'}")
        if checked and self._doc is not None:
            self.preview.scroll_to_page(self._current_page())

    def _on_zoom_changed(self):
        text = self.zoom_combo.currentText()
        self.view.set_zoom(
            None if text.startswith("Fit") else float(text.rstrip("%")) / 100.0
        )

    def _on_option_changed(self):
        if self._doc is not None:
            self.preview.invalidate()

    def _on_selection_changed(self, page_index: int, index: int):
        self.delete_btn.setEnabled(index >= 0)

    def _update_actions(self):
        has_doc = self._doc is not None
        last_page = (self._doc.page_count - 1) if self._doc is not None else 0
        page_index = self._current_page()
        total = self._total_boxes()
        page_count = len(self._boxes_for_page(page_index))
        self.save_btn.setEnabled(has_doc and total > 0)
        self.undo_btn.setEnabled(bool(self._undo_stack))
        self.redo_btn.setEnabled(bool(self._redo_stack))
        self.delete_btn.setEnabled(self.view.selected_box() is not None)
        self.copy_btn.setEnabled(has_doc and page_count > 0)
        self.clear_page_btn.setEnabled(page_count > 0)
        self.clear_all_btn.setEnabled(total > 0)
        self.prev_btn.setEnabled(has_doc and page_index > 0)
        self.next_btn.setEnabled(has_doc and page_index < last_page)
        self.count_label.setText(
            f"{page_count} box(es) on page {page_index + 1}  \u2022  {total} total"
            if has_doc else ""
        )

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------
    def _save_output(self):
        if self._doc is None or self._input_path is None:
            return
        if self._total_boxes() == 0:
            QMessageBox.information(
                self, "Nothing to redact", "Draw at least one redaction box first."
            )
            return
        default_path = build_output_path(self._input_path)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save redacted PDF", str(default_path), "PDF Files (*.pdf)"
        )
        if not path:
            return
        out_path = Path(path)
        try:
            applied = redact_pdf(
                input_pdf=str(self._input_path),
                output_pdf=str(out_path),
                boxes=self._boxes,
                fill=self._fill(),
                images_mode=self._images_mode(),
                vector_mode=self._vector_mode(),
            )
        except Exception as error:
            QMessageBox.critical(self, "Save failed", str(error))
            return
        self.status.setText(f"Saved {out_path.name} with {applied} redaction(s).")
        QMessageBox.information(
            self, "Saved", f"{out_path}\n\n{applied} redaction(s) applied."
        )

    def closeEvent(self, event):
        self.preview.set_document(None)
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        super().closeEvent(event)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Draw redaction boxes on a PDF and save <name>_redacted.pdf."
    )
    parser.add_argument("input", nargs="?", help="PDF to open on startup")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    app.setFont(QFont("Inter, Segoe UI, SF Pro Display, Helvetica", 10))
    window = MainWindow()
    window.show()
    if args.input:
        window.load_pdf(Path(args.input))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
