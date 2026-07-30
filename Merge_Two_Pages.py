from __future__ import annotations

"""
J Merge Two Pages — combine two PDF pages (e.g. the front and back of a
document) onto a single page, with a PyQt6 GUI.

Workflow:
- Drag and drop a 2-page PDF (or use Open PDF / Ctrl+O)
- Choose which page is the BASE (stays fixed, original size) and which page
  is MOVED (placed on top of the base)
- Position the moved page by dragging it on the preview, or type exact X/Y
  offsets, and scale it up or down
- Output is a single-page vector PDF the same size as the base page (Ctrl+S)

Dependencies: PyQt6, PyMuPDF. Run with `python Merge_Two_Pages.py`.
"""

import sys
from pathlib import Path

import fitz  # PyMuPDF
from PyQt6.QtCore import Qt, QRectF, QTimer, QPointF, QPoint, QRect, QSize, pyqtSignal
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QColor, QBrush, QDragEnterEvent, QDropEvent,
    QFont, QCursor,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsItem, QGraphicsObject, QGraphicsRectItem, QSpinBox, QDoubleSpinBox,
    QFrame, QMessageBox, QSizePolicy, QComboBox, QCheckBox, QLayout, QWidgetItem,
)


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


def detect_page_dpi(page: "fitz.Page") -> int | None:
    """Estimate a page's native resolution from its embedded raster images.

    For scanned/image PDFs a page is typically one big image. Its native DPI is
    the image's pixel width divided by the page's displayed width in inches. We
    take the largest image on the page (by displayed area) as representative.
    Returns None if the page has no usable images (e.g. pure vector/text).
    """
    try:
        images = page.get_image_info(xrefs=True)
    except Exception:
        images = []
    best_dpi = None
    best_area = 0.0
    page_rect = page.rect
    for info in images:
        bbox = fitz.Rect(info.get("bbox", (0, 0, 0, 0)))
        disp_w_pt = bbox.width
        disp_h_pt = bbox.height
        px_w = info.get("width", 0)
        px_h = info.get("height", 0)
        if disp_w_pt <= 1 or disp_h_pt <= 1 or px_w <= 0 or px_h <= 0:
            continue
        area = disp_w_pt * disp_h_pt
        if area < best_area:
            continue
        # DPI = pixels / inches; 72 pt = 1 inch.
        dpi_w = px_w / (disp_w_pt / 72.0)
        dpi_h = px_h / (disp_h_pt / 72.0)
        best_dpi = int(round(max(dpi_w, dpi_h)))
        best_area = area
    return best_dpi


def resolve_render_dpi(
    src: "fitz.Document", indices: list[int],
    requested: "int | str", floor: int = 150, ceiling: int = 600,
) -> int:
    """Turn a requested DPI (int) or 'auto' into a concrete render DPI.

    'auto' detects each page's native image DPI and uses the maximum, clamped
    to [floor, ceiling]. Falls back to `floor` if nothing can be detected.
    """
    if isinstance(requested, int):
        return requested
    detected = []
    for i in indices:
        d = detect_page_dpi(src[i])
        if d:
            detected.append(d)
    if not detected:
        return floor
    return max(floor, min(ceiling, max(detected)))


def merge_two_pages_vector(
    input_pdf: str,
    output_pdf: str,
    base_index: int,
    moved_index: int,
    offset_x_pt: float,
    offset_y_pt: float,
    scale: float,
    crop: tuple[float, float, float, float] | None = None,
    render_dpi: "int | str" = "auto",
) -> None:
    """Create a single-page PDF with the moved page placed onto the base page.

    The output page is the same size as the base page. The moved page is
    optionally cropped to `crop` (a rect in the moved page's own coordinate
    system, in points), then drawn at (offset_x_pt, offset_y_pt) from the base
    page's top-left corner and scaled by `scale`.

    Both pages are normalized through a high-DPI render (`render_dpi`) so the
    output matches the on-screen preview even when the source PDF has page
    rotation or a non-zero MediaBox origin.
    """
    src = fitz.open(input_pdf)
    norm = None
    try:
        if src.page_count < 2:
            raise ValueError("Input PDF must have at least 2 pages.")
        if base_index == moved_index:
            raise ValueError("Base page and moved page must be different.")

        # The GUI works in each page's *displayed* coordinate space (page.rect,
        # which is what get_pixmap renders: origin at 0,0 with page rotation
        # applied). A real PDF may have rotation and/or a non-zero MediaBox
        # origin, so raw clip/placement coordinates disagree with the preview.
        #
        # To make the output pixel-identical to the preview, normalize both
        # pages by rendering each to a high-DPI image (rotation applied by
        # get_pixmap, exactly like the preview) and placing that image on a
        # clean upright page whose size equals page.rect. Everything downstream
        # then works in the same coordinate space the user cropped in.
        # Use the document's own resolution when render_dpi == "auto".
        dpi = resolve_render_dpi(src, [base_index, moved_index], render_dpi)

        norm = fitz.open()
        base_disp = src[base_index].rect   # rotation-aware displayed size
        moved_disp = src[moved_index].rect
        base_pix = src[base_index].get_pixmap(dpi=dpi, alpha=False)
        moved_pix = src[moved_index].get_pixmap(dpi=dpi, alpha=False)
        base_norm = norm.new_page(width=base_disp.width, height=base_disp.height)
        base_norm.insert_image(base_norm.rect, pixmap=base_pix)
        moved_norm = norm.new_page(width=moved_disp.width, height=moved_disp.height)
        moved_norm.insert_image(moved_norm.rect, pixmap=moved_pix)

        base_rect = norm[0].rect
        moved_rect = norm[1].rect

        # Resolve the clip rect (portion of the moved page to keep), in the
        # normalized/displayed coordinate space that the GUI used.
        if crop is not None:
            clip = fitz.Rect(*crop)
        else:
            clip = fitz.Rect(moved_rect)
        clip = clip & moved_rect  # intersect with page bounds
        if clip.width <= 0 or clip.height <= 0:
            raise ValueError("Crop removed the entire moved page.")

        out = fitz.open()
        dst = out.new_page(width=base_rect.width, height=base_rect.height)

        # Draw the base page filling the whole output page.
        dst.show_pdf_page(
            fitz.Rect(0, 0, base_rect.width, base_rect.height),
            norm, 0, keep_proportion=False, overlay=True,
        )

        # Draw the (cropped) moved page at the requested offset and scale.
        mw = clip.width * scale
        mh = clip.height * scale
        dest = fitz.Rect(offset_x_pt, offset_y_pt, offset_x_pt + mw, offset_y_pt + mh)
        dst.show_pdf_page(
            dest, norm, 1, clip=clip, keep_proportion=True, overlay=True
        )

        out.save(output_pdf, garbage=4, deflate=True)
        out.close()
    finally:
        if norm is not None:
            norm.close()
        src.close()


def build_output_path(input_pdf_path: Path) -> Path:
    return input_pdf_path.parent / f"{input_pdf_path.stem}_merged.pdf"


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
QSpinBox, QDoubleSpinBox {
    background-color: #313244; color: #cdd6f4; border: 1px solid #45475a;
    border-radius: 6px; padding: 0 10px; min-width: 80px;
    font-size: 14px; font-weight: 600;
}
QSpinBox:focus, QDoubleSpinBox:focus { border-color: #89b4fa; }
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0; height: 0; border: none; }
QComboBox {
    background-color: #313244; color: #cdd6f4; border: 1px solid #45475a;
    border-radius: 6px; padding: 4px 10px; min-width: 90px; font-size: 13px;
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
QScrollBar:vertical { background: #181825; width: 10px; border-radius: 5px; }
QScrollBar::handle:vertical { background: #45475a; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #585b70; }
QScrollBar:horizontal { background: #181825; height: 10px; border-radius: 5px; }
QScrollBar::handle:horizontal { background: #45475a; border-radius: 5px; min-width: 30px; }
"""


class MovedPageItem(QGraphicsObject):
    """The moved page drawn on top of the base page, supporting two modes:

    - Pan mode: drag the body to reposition the page on the base.
    - Crop mode (default): drag a corner to crop the page.

    The item's `pos()` is the scene position of the FULL moved page's top-left
    corner. `_crop` is the visible rectangle in moved-page points. Because the
    origin never shifts during a crop drag, motion tracks the mouse 1:1 in all
    directions.
    """

    HANDLE = 14   # handle square size (scene px)
    GRAB = 16     # half-size of the corner grab zone (scene px)

    offsetChanged = pyqtSignal(float, float)      # x_pt, y_pt: top-left of crop on base
    cropChanged = pyqtSignal(float, float, float, float)  # x0, y0, x1, y1 in moved points
    dragStarted = pyqtSignal()                    # emitted on mouse press (for undo snapshot)

    def __init__(self, view: "MergeView", pixmap: QPixmap, page_w_pt: float,
                 page_h_pt: float, moved_scale: float, base_scale: float):
        super().__init__()
        self._view = view
        self._pixmap = pixmap
        self._page_w = page_w_pt
        self._page_h = page_h_pt
        self._moved_scale = moved_scale        # moved-points -> output-points
        self._base_scale = base_scale          # base-points -> scene px
        self._ppp = self._moved_scale * self._base_scale  # scene px per moved-point
        # visible crop in moved-page points (start = full page)
        self._crop = QRectF(0, 0, page_w_pt, page_h_pt)
        self._suppress = False
        self._pan_mode = False                 # default = crop mode
        self._active_zone: str | None = None   # corner/edge being dragged
        self._pan_last = QPointF()             # last mouse scene pos while panning

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self._update_cursor(QPointF(0, 0))

    # --- geometry helpers (all in item coords; origin = full page top-left) ---
    def _full_w(self) -> float:
        return self._page_w * self._ppp

    def _full_h(self) -> float:
        return self._page_h * self._ppp

    def _crop_rect_px(self) -> QRectF:
        """Visible crop rectangle in item pixel coords."""
        return QRectF(
            self._crop.x() * self._ppp,
            self._crop.y() * self._ppp,
            self._crop.width() * self._ppp,
            self._crop.height() * self._ppp,
        )

    def boundingRect(self) -> QRectF:
        m = self.GRAB
        return QRectF(-m, -m, self._full_w() + 2 * m, self._full_h() + 2 * m)

    def _corner_points(self) -> dict[str, QPointF]:
        r = self._crop_rect_px()
        return {
            "tl": QPointF(r.left(), r.top()),
            "tr": QPointF(r.right(), r.top()),
            "bl": QPointF(r.left(), r.bottom()),
            "br": QPointF(r.right(), r.bottom()),
        }

    def _corner_at(self, item_pos: QPointF) -> str | None:
        for name, pt in self._corner_points().items():
            if (abs(item_pos.x() - pt.x()) <= self.GRAB
                    and abs(item_pos.y() - pt.y()) <= self.GRAB):
                return name
        return None

    def _edge_at(self, item_pos: QPointF) -> str | None:
        """Return 'l', 'r', 't', or 'b' if the point is near a single edge.

        Corners take priority and are handled separately; here we only match
        the middle span of each edge so a drag moves it in one direction.
        """
        r = self._crop_rect_px()
        x, y = item_pos.x(), item_pos.y()
        near_x_span = (r.left() + self.GRAB) <= x <= (r.right() - self.GRAB)
        near_y_span = (r.top() + self.GRAB) <= y <= (r.bottom() - self.GRAB)
        if near_y_span and abs(x - r.left()) <= self.GRAB:
            return "l"
        if near_y_span and abs(x - r.right()) <= self.GRAB:
            return "r"
        if near_x_span and abs(y - r.top()) <= self.GRAB:
            return "t"
        if near_x_span and abs(y - r.bottom()) <= self.GRAB:
            return "b"
        return None

    def paint(self, painter, option, widget=None):
        r = self._crop_rect_px()
        pm_dpr = self._pixmap.devicePixelRatio()
        px_per_pt = (self._pixmap.width() / pm_dpr) / self._page_w
        src = QRectF(
            self._crop.x() * px_per_pt * pm_dpr,
            self._crop.y() * px_per_pt * pm_dpr,
            self._crop.width() * px_per_pt * pm_dpr,
            self._crop.height() * px_per_pt * pm_dpr,
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(r, self._pixmap, src)

        # Dashed border around the visible crop.
        pen = QPen(QColor("#89b4fa"), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r)

        # Corner + edge handles (only in crop mode).
        if not self._pan_mode:
            painter.setPen(QPen(QColor("#1e1e2e"), 1))
            painter.setBrush(QBrush(QColor("#89b4fa")))
            s = self.HANDLE
            # Mid-edge bars (thin rounded rects) to signal edge dragging.
            bar = s * 1.6
            thick = s * 0.42
            cx = (r.left() + r.right()) / 2
            cy = (r.top() + r.bottom()) / 2
            painter.drawRoundedRect(
                QRectF(r.left() - thick / 2, cy - bar / 2, thick, bar), 2, 2)
            painter.drawRoundedRect(
                QRectF(r.right() - thick / 2, cy - bar / 2, thick, bar), 2, 2)
            painter.drawRoundedRect(
                QRectF(cx - bar / 2, r.top() - thick / 2, bar, thick), 2, 2)
            painter.drawRoundedRect(
                QRectF(cx - bar / 2, r.bottom() - thick / 2, bar, thick), 2, 2)
            # Corner squares (drawn last so they sit on top).
            for pt in self._corner_points().values():
                painter.drawRect(QRectF(pt.x() - s / 2, pt.y() - s / 2, s, s))

    # --- mode + cursor ---
    def set_pannable(self, pannable: bool):
        self._pan_mode = pannable
        self.update()

    def _update_cursor(self, item_pos: QPointF):
        if self._pan_mode:
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            return
        c = self._corner_at(item_pos)
        if c in ("tl", "br"):
            self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
            return
        if c in ("tr", "bl"):
            self.setCursor(QCursor(Qt.CursorShape.SizeBDiagCursor))
            return
        e = self._edge_at(item_pos)
        if e in ("l", "r"):
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        elif e in ("t", "b"):
            self.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def hoverMoveEvent(self, event):
        self._update_cursor(event.pos())
        super().hoverMoveEvent(event)

    # --- mouse handling ---
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.dragStarted.emit()
        if self._pan_mode:
            self._pan_last = event.scenePos()
            event.accept()
            return
        zone = self._corner_at(event.pos()) or self._edge_at(event.pos())
        if zone is not None:
            self._active_zone = zone
            event.accept()
            return
        # Click inside crop with no handle: allow pan-like move even in crop mode.
        self._pan_last = event.scenePos()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._active_zone is not None:
            self._drag_zone(event.pos())
            event.accept()
            return
        # Panning (either pan mode, or body-drag in crop mode).
        delta = event.scenePos() - self._pan_last
        self._pan_last = event.scenePos()
        self._suppress = True
        self.setPos(self.pos() + delta)
        self._suppress = False
        self._emit_offset()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._active_zone = None
        super().mouseReleaseEvent(event)

    def _drag_zone(self, item_pos: QPointF):
        """Resize the crop. Corners move two edges, single edges move one.

        `_active_zone` is a corner ("tl"/"tr"/"bl"/"br") or an edge
        ("l"/"r"/"t"/"b"). Only the edges named in the zone move; the rest stay
        fixed as anchors, so a corner drag keeps the opposite corner fixed and
        an edge drag moves only that one edge.
        """
        min_px = 24
        zone = self._active_zone
        r = self._crop_rect_px()
        left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
        # Clamp the mouse to the full page bounds and keep min size.
        x = max(0.0, min(item_pos.x(), self._full_w()))
        y = max(0.0, min(item_pos.y(), self._full_h()))
        if "l" in zone:
            left = min(x, right - min_px)
        if "r" in zone:
            right = max(x, left + min_px)
        if "t" in zone:
            top = min(y, bottom - min_px)
        if "b" in zone:
            bottom = max(y, top + min_px)

        self.prepareGeometryChange()
        self._crop = QRectF(
            left / self._ppp,
            top / self._ppp,
            (right - left) / self._ppp,
            (bottom - top) / self._ppp,
        )
        self.update()
        self._emit_offset()
        self.cropChanged.emit(
            self._crop.x(), self._crop.y(),
            self._crop.x() + self._crop.width(),
            self._crop.y() + self._crop.height(),
        )

    def _emit_offset(self):
        """Offset (in base points) of the visible crop's top-left corner."""
        if self._suppress and self._active_zone is None:
            return
        scene_x = self.pos().x() + self._crop.x() * self._ppp
        scene_y = self.pos().y() + self._crop.y() * self._ppp
        self.offsetChanged.emit(scene_x / self._base_scale, scene_y / self._base_scale)

    # --- programmatic setters ---
    def set_scene_pos_from_pt(self, x_pt: float, y_pt: float):
        """Set position so the visible crop's top-left lands at (x_pt, y_pt)."""
        self._suppress = True
        origin_x = x_pt * self._base_scale - self._crop.x() * self._ppp
        origin_y = y_pt * self._base_scale - self._crop.y() * self._ppp
        self.setPos(origin_x, origin_y)
        self._suppress = False

    def set_crop_pt(self, crop: tuple[float, float, float, float] | None):
        self.prepareGeometryChange()
        if crop is None:
            self._crop = QRectF(0, 0, self._page_w, self._page_h)
        else:
            self._crop = QRectF(crop[0], crop[1], crop[2] - crop[0], crop[3] - crop[1])
        self.update()

    def current_crop(self) -> QRectF:
        return QRectF(self._crop)


class MergeView(QGraphicsView):
    """Preview canvas: base page at the bottom, draggable/croppable moved page on top."""

    offsetChanged = pyqtSignal(float, float)              # x_pt, y_pt
    cropChanged = pyqtSignal(float, float, float, float)  # x0, y0, x1, y1 (moved points)
    dragStarted = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
        )
        self.setMinimumSize(220, 220)
        self._base_item: QGraphicsPixmapItem | None = None
        self._moved_item: MovedPageItem | None = None
        self._scale = 1.0  # scene px per PDF point (base page)
        self._base_w_pt = 0.0
        self._base_h_pt = 0.0

    def _render_page(self, doc: fitz.Document, index: int) -> tuple[QPixmap, float, float]:
        page = doc[index]
        dpr = max(1.0, float(self.devicePixelRatioF()))
        pix = page.get_pixmap(dpi=int(150 * dpr), alpha=False)
        img = QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
        ).copy()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        return pm, page.rect.width, page.rect.height

    def load(
        self,
        doc: fitz.Document,
        base_index: int,
        moved_index: int,
        offset_x_pt: float,
        offset_y_pt: float,
        moved_scale: float,
        crop: tuple[float, float, float, float] | None = None,
        pannable: bool = False,
    ):
        self._scene.clear()

        base_pm, self._base_w_pt, self._base_h_pt = self._render_page(doc, base_index)
        dpr = base_pm.devicePixelRatio()
        scene_w = base_pm.width() / dpr
        scene_h = base_pm.height() / dpr
        self._scale = scene_w / self._base_w_pt

        self._base_item = self._scene.addPixmap(base_pm)
        self._base_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._base_item.setZValue(0)
        self._scene.setSceneRect(QRectF(0, 0, scene_w, scene_h))

        moved_pm, moved_w_pt, moved_h_pt = self._render_page(doc, moved_index)
        self._moved_item = MovedPageItem(
            self, moved_pm, moved_w_pt, moved_h_pt, moved_scale, self._scale
        )
        self._moved_item.set_crop_pt(crop)
        self._moved_item.set_scene_pos_from_pt(offset_x_pt, offset_y_pt)
        self._moved_item.set_pannable(pannable)
        self._moved_item.offsetChanged.connect(self.offsetChanged)
        self._moved_item.cropChanged.connect(self.cropChanged)
        self._moved_item.dragStarted.connect(self.dragStarted)
        self._scene.addItem(self._moved_item)

        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._base_item is not None:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_offset(self, x_pt: float, y_pt: float):
        if self._moved_item is not None:
            self._moved_item.set_scene_pos_from_pt(x_pt, y_pt)

    def set_pannable(self, pannable: bool):
        if self._moved_item is not None:
            self._moved_item.set_pannable(pannable)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("J Merge Two Pages")
        self.resize(1180, 820)
        self.setMinimumSize(420, 460)
        self.setAcceptDrops(True)

        self._doc: fitz.Document | None = None
        self._input_path: Path | None = None
        self._base_index = 0
        self._moved_index = 1
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._scale = 1.0
        self._crop: tuple[float, float, float, float] | None = None
        self._detected_dpi: int | None = None  # native DPI of loaded pages
        self._pan_mode = False  # default = crop mode
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._push_guard = False  # avoid recording states during undo/reload

        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(120)
        self._reload_timer.timeout.connect(self._reload_view)

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("J Merge Two Pages")
        title.setObjectName("Title")
        subtitle = QLabel(
            "Drop a 2-page PDF. Pick the base and moved pages, drag the moved "
            "page onto the base, then save a single-page PDF."
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

        self.save_btn = QPushButton("Save PDF")
        self.save_btn.setEnabled(False)
        self.save_btn.setShortcut("Ctrl+S")
        self.save_btn.setToolTip("Save merged PDF (Ctrl+S)")
        self.save_btn.clicked.connect(self._save_output)
        toolbar.addWidget(self.save_btn)
        root.addLayout(toolbar)

        # --- Preview card ---
        preview_card = QFrame()
        preview_card.setObjectName("Card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(14, 14, 14, 14)
        preview_layout.setSpacing(8)
        self.mode_hint = QLabel(
            "Crop mode — drag the corners or the edges to crop. Switch to Pan to reposition."
        )
        preview_layout.addWidget(self.mode_hint)

        self.drop_hint = QLabel("Drop a 2-page PDF here, or click \u201cOpen PDF\u201d")
        self.drop_hint.setObjectName("DropHint")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_layout.addWidget(self.drop_hint, 1)

        self.view = MergeView()
        self.view.hide()
        self.view.offsetChanged.connect(self._on_offset_dragged)
        self.view.cropChanged.connect(self._on_crop_changed)
        self.view.dragStarted.connect(self._snapshot)
        preview_layout.addWidget(self.view, 1)
        root.addWidget(preview_card, 1)

        # --- Controls (wrap to new rows when the window is narrow) ---
        controls_card = QFrame()
        controls_card.setObjectName("Card")
        controls_card.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        controls = FlowLayout(controls_card, margin=10, hspacing=10, vspacing=8)

        self.base_combo = QComboBox()
        self.base_combo.currentIndexChanged.connect(self._on_base_changed)
        controls.addWidget(self._group("Base page", self.base_combo))

        self.moved_combo = QComboBox()
        self.moved_combo.currentIndexChanged.connect(self._on_moved_changed)
        controls.addWidget(self._group("Moved page", self.moved_combo))

        self.x_spin = QSpinBox()
        self.x_spin.setRange(-2000, 2000)
        self.x_spin.setSuffix(" pt")
        self.x_spin.valueChanged.connect(self._on_spin_changed)
        controls.addWidget(self._group("X", self._stepper(self.x_spin)))

        self.y_spin = QSpinBox()
        self.y_spin.setRange(-2000, 2000)
        self.y_spin.setSuffix(" pt")
        self.y_spin.valueChanged.connect(self._on_spin_changed)
        controls.addWidget(self._group("Y", self._stepper(self.y_spin)))

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.05, 5.0)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setDecimals(2)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setSuffix(" x")
        self.scale_spin.valueChanged.connect(self._on_scale_changed)
        controls.addWidget(self._group("Scale", self.scale_spin))

        self.dpi_auto_chk = QCheckBox("Auto")
        self.dpi_auto_chk.setChecked(True)
        self.dpi_auto_chk.setToolTip("Use the document's own resolution (detected from its images)")
        self.dpi_auto_chk.toggled.connect(self._on_dpi_auto_toggled)
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setValue(300)
        self.dpi_spin.setSuffix(" dpi")
        self.dpi_spin.setEnabled(False)
        self.dpi_spin.setToolTip("Render quality of the saved PDF (higher = sharper, larger file)")
        controls.addWidget(self._group("Quality", self.dpi_auto_chk, self.dpi_spin))

        self.mode_btn = QPushButton("Mode: Crop")
        self.mode_btn.setCheckable(True)
        self.mode_btn.setToolTip("Toggle between Crop (drag corners) and Pan (move page)")
        self.mode_btn.toggled.connect(self._on_mode_toggled)
        controls.addWidget(self.mode_btn)

        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setObjectName("Secondary")
        self.undo_btn.setToolTip("Undo last change (Ctrl+Z)")
        self.undo_btn.setShortcut("Ctrl+Z")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo)
        controls.addWidget(self.undo_btn)

        self.redo_btn = QPushButton("Redo")
        self.redo_btn.setObjectName("Secondary")
        self.redo_btn.setToolTip("Redo last undone change (Ctrl+Y)")
        self.redo_btn.setShortcut("Ctrl+Y")
        self.redo_btn.setEnabled(False)
        self.redo_btn.clicked.connect(self._redo)
        controls.addWidget(self.redo_btn)

        self.reset_crop_btn = QPushButton("Reset crop")
        self.reset_crop_btn.setObjectName("Secondary")
        self.reset_crop_btn.setToolTip("Restore the moved page to its full size")
        self.reset_crop_btn.clicked.connect(self._reset_crop)
        controls.addWidget(self.reset_crop_btn)

        self.center_btn = QPushButton("Center")
        self.center_btn.setObjectName("Secondary")
        self.center_btn.setToolTip("Center the moved page on the base page")
        self.center_btn.clicked.connect(self._center_moved)
        controls.addWidget(self.center_btn)

        root.addWidget(controls_card)

        self.status = QLabel("")
        self.status.setObjectName("Subtitle")
        root.addWidget(self.status)

    def _stepper(self, spin) -> QWidget:
        H = 34
        w = QWidget()
        w.setFixedHeight(H)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        minus = QPushButton("\u2212")
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

    def _group(self, label: str, *widgets: QWidget) -> QWidget:
        """Bundle a caption label with its input(s) so they wrap together."""
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(QLabel(f"{label}:"))
        for wdg in widgets:
            lay.addWidget(wdg)
        return box

    # --- Drag and drop ---
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
            if doc.page_count < 2:
                raise ValueError("PDF must have at least 2 pages.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open PDF:\n{e}")
            return

        if self._doc is not None:
            self._doc.close()
        self._doc = doc
        self._input_path = path

        self._base_index = 0
        self._moved_index = 1
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._scale = 1.0
        self._crop = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_history_buttons()

        # Populate page selectors.
        for combo in (self.base_combo, self.moved_combo):
            combo.blockSignals(True)
            combo.clear()
            for i in range(doc.page_count):
                combo.addItem(f"Page {i + 1}", i)
            combo.blockSignals(False)
        self.base_combo.setCurrentIndex(0)
        self.moved_combo.setCurrentIndex(1)

        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)
        self.scale_spin.blockSignals(True)
        self.x_spin.setValue(0)
        self.y_spin.setValue(0)
        self.scale_spin.setValue(1.0)
        self.x_spin.blockSignals(False)
        self.y_spin.blockSignals(False)
        self.scale_spin.blockSignals(False)

        # Detect the document's native resolution from its embedded images.
        detected = [d for d in (detect_page_dpi(doc[i]) for i in range(doc.page_count)) if d]
        self._detected_dpi = max(detected) if detected else None
        if self._detected_dpi:
            self.dpi_auto_chk.setText(f"Auto ({self._detected_dpi} dpi)")
            self.dpi_spin.blockSignals(True)
            self.dpi_spin.setValue(
                max(self.dpi_spin.minimum(), min(self.dpi_spin.maximum(), self._detected_dpi))
            )
            self.dpi_spin.blockSignals(False)
        else:
            self.dpi_auto_chk.setText("Auto (vector)")

        self.drop_hint.hide()
        self.view.show()
        self.save_btn.setEnabled(True)
        self.file_label.setText(f"{path.name}  \u2022  {doc.page_count} pages")
        dpi_note = (
            f"native {self._detected_dpi} dpi"
            if self._detected_dpi else "no embedded images (vector)"
        )
        self.status.setText(
            f"Loaded: {path.name}  \u2022  {doc.page_count} pages  \u2022  {dpi_note}"
        )
        self._reload_view()

    def _reload_view(self):
        if self._doc is None:
            return
        self.view.load(
            self._doc,
            self._base_index,
            self._moved_index,
            self._offset_x,
            self._offset_y,
            self._scale,
            self._crop,
            self._pan_mode,
        )

    # --- undo support ---
    def _state(self) -> dict:
        return {
            "base_index": self._base_index,
            "moved_index": self._moved_index,
            "offset_x": self._offset_x,
            "offset_y": self._offset_y,
            "scale": self._scale,
            "crop": self._crop,
        }

    def _snapshot(self):
        """Record the current state so it can be restored via Undo (Ctrl+Z).

        Making a new change clears the redo history.
        """
        if self._doc is None or self._push_guard:
            return
        state = self._state()
        if self._undo_stack and self._undo_stack[-1] == state:
            return  # skip duplicate consecutive states
        self._undo_stack.append(state)
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_history_buttons()

    def _apply_state(self, s: dict):
        """Restore a saved state to fields and sync widgets without emitting."""
        self._push_guard = True
        self._base_index = s["base_index"]
        self._moved_index = s["moved_index"]
        self._offset_x = s["offset_x"]
        self._offset_y = s["offset_y"]
        self._scale = s["scale"]
        self._crop = s["crop"]
        for combo, idx in ((self.base_combo, self._base_index),
                           (self.moved_combo, self._moved_index)):
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        for spin, val in ((self.x_spin, int(round(self._offset_x))),
                          (self.y_spin, int(round(self._offset_y)))):
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
        self.scale_spin.blockSignals(True)
        self.scale_spin.setValue(self._scale)
        self.scale_spin.blockSignals(False)
        self._push_guard = False
        self._reload_view()

    def _update_history_buttons(self):
        self.undo_btn.setEnabled(bool(self._undo_stack))
        self.redo_btn.setEnabled(bool(self._redo_stack))

    def _undo(self):
        if not self._undo_stack:
            self.status.setText("Nothing to undo.")
            return
        # Save current state to redo before restoring the previous one.
        self._redo_stack.append(self._state())
        self._apply_state(self._undo_stack.pop())
        self._update_history_buttons()
        self.status.setText("Undid last change.")

    def _redo(self):
        if not self._redo_stack:
            self.status.setText("Nothing to redo.")
            return
        # Save current state to undo before re-applying the undone one.
        self._undo_stack.append(self._state())
        self._apply_state(self._redo_stack.pop())
        self._update_history_buttons()
        self.status.setText("Redid change.")

    def _on_mode_toggled(self, checked: bool):
        self._pan_mode = checked
        self.mode_btn.setText("Mode: Pan" if checked else "Mode: Crop")
        self.mode_hint.setText(
            "Pan mode — drag the page to reposition. Switch to Crop to trim edges."
            if checked else
            "Crop mode — drag the corners or the edges to crop. Switch to Pan to reposition."
        )
        self.view.set_pannable(checked)

    def _on_base_changed(self, idx: int):
        if idx < 0 or self._doc is None:
            return
        self._snapshot()
        self._base_index = self.base_combo.currentData()
        # Avoid base == moved by nudging moved to a different page.
        if self._moved_index == self._base_index:
            other = (self._base_index + 1) % self._doc.page_count
            self.moved_combo.blockSignals(True)
            self.moved_combo.setCurrentIndex(other)
            self.moved_combo.blockSignals(False)
            self._moved_index = other
        self._reload_view()

    def _on_moved_changed(self, idx: int):
        if idx < 0 or self._doc is None:
            return
        self._snapshot()
        new_moved = self.moved_combo.currentData()
        if new_moved == self._base_index:
            other = (self._base_index + 1) % self._doc.page_count
            self.moved_combo.blockSignals(True)
            self.moved_combo.setCurrentIndex(other)
            self.moved_combo.blockSignals(False)
            new_moved = other
        self._moved_index = new_moved
        # Different page => previous crop no longer applies.
        self._crop = None
        self._reload_view()

    def _on_offset_dragged(self, x_pt: float, y_pt: float):
        self._offset_x = x_pt
        self._offset_y = y_pt
        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)
        self.x_spin.setValue(int(round(x_pt)))
        self.y_spin.setValue(int(round(y_pt)))
        self.x_spin.blockSignals(False)
        self.y_spin.blockSignals(False)

    def _on_crop_changed(self, x0: float, y0: float, x1: float, y1: float):
        self._crop = (x0, y0, x1, y1)
        self.status.setText(
            f"Crop: {x1 - x0:.0f} x {y1 - y0:.0f} pt  \u2022  drag corners to adjust"
        )

    def _on_spin_changed(self):
        self._snapshot()
        self._offset_x = float(self.x_spin.value())
        self._offset_y = float(self.y_spin.value())
        self.view.set_offset(self._offset_x, self._offset_y)

    def _on_scale_changed(self):
        self._snapshot()
        self._scale = float(self.scale_spin.value())
        self._reload_timer.start()

    def _on_dpi_auto_toggled(self, checked: bool):
        self.dpi_spin.setEnabled(not checked)
        if checked and self._detected_dpi:
            # Reflect the detected value in the (disabled) spinbox for reference.
            self.dpi_spin.setValue(
                max(self.dpi_spin.minimum(), min(self.dpi_spin.maximum(), self._detected_dpi))
            )

    def _reset_crop(self):
        self._snapshot()
        self._crop = None
        self._reload_view()
        self.status.setText("Crop reset to full page.")

    def _center_moved(self):
        if self._doc is None:
            return
        self._snapshot()
        base_rect = self._doc[self._base_index].rect
        moved_rect = self._doc[self._moved_index].rect
        # Use the cropped size if a crop is active.
        if self._crop is not None:
            crop_w = self._crop[2] - self._crop[0]
            crop_h = self._crop[3] - self._crop[1]
        else:
            crop_w = moved_rect.width
            crop_h = moved_rect.height
        mw = crop_w * self._scale
        mh = crop_h * self._scale
        self._offset_x = (base_rect.width - mw) / 2.0
        self._offset_y = (base_rect.height - mh) / 2.0
        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)
        self.x_spin.setValue(int(round(self._offset_x)))
        self.y_spin.setValue(int(round(self._offset_y)))
        self.x_spin.blockSignals(False)
        self.y_spin.blockSignals(False)
        self.view.set_offset(self._offset_x, self._offset_y)

    def _save_output(self):
        if self._doc is None or self._input_path is None:
            return
        default_pdf = build_output_path(self._input_path)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Merged PDF", str(default_pdf), "PDF Files (*.pdf)"
        )
        if not path:
            return
        out_pdf = Path(path)
        try:
            merge_two_pages_vector(
                input_pdf=str(self._input_path),
                output_pdf=str(out_pdf),
                base_index=self._base_index,
                moved_index=self._moved_index,
                offset_x_pt=self._offset_x,
                offset_y_pt=self._offset_y,
                scale=self._scale,
                crop=self._crop,
                render_dpi="auto" if self.dpi_auto_chk.isChecked() else int(self.dpi_spin.value()),
            )
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.status.setText(f"Saved PDF: {out_pdf.name}")
        QMessageBox.information(self, "Saved", f"PDF: {out_pdf}")


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
