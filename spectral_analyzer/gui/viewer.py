"""Image viewer with ROI drawing tools (freehand, polygon, rectangle, ellipse, point)."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QFrame, QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView)

from ..roi_io import RoiRecord
from ..spectral_core import RoiGeometry

TOOLS = ["pan", "freehand", "polygon", "rectangle", "ellipse", "point"]


def _path_from_points(pts: np.ndarray, close: bool = True) -> QPainterPath:
    """Qt drawing path through a list of (x, y) points, closed for polygons and free-hand ROIs."""
    path = QPainterPath()
    if len(pts) == 0:
        return path
    path.addPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in pts]))
    if close:
        path.closeSubpath()
    return path


class SpectralViewer(QGraphicsView):
    """Image view of one spectral stack: zoom, pan, scale bar, display of the ROIs and mouse tools
    to draw new ROIs.
    """
    roiDrawn = Signal(object)          # RoiGeometry
    hovered = Signal(float, float)     # image coordinates (NaN when outside)
    roiClicked = Signal(int)           # uid

    def __init__(self, parent=None):
        """Create the scene (image, ROI overlays, drawing preview, scale bar) and the default tool.
        """
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.FastTransformation)   # show true pixels when zoomed
        self.scene().addItem(self.pix)
        self.setRenderHints(QPainter.Antialiasing)
        self.setBackgroundBrush(QColor("#15181c"))
        self.setFrameShape(QFrame.NoFrame)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.tool = "pan"
        self.point_radius = 0
        self._drawing: List[QPointF] = []
        self._start: Optional[QPointF] = None
        self._preview = QGraphicsPathItem()
        pen = QPen(QColor("#ffffff"), 1.5, Qt.DashLine)
        pen.setCosmetic(True)
        self._preview.setPen(pen)
        self._preview.setZValue(10)
        self.scene().addItem(self._preview)
        self._roi_items: Dict[int, List[QGraphicsItem]] = {}
        self._pan_origin = None
        self._fit = True
        self.scalebar_um: Optional[float] = None
        self._scalebar_items: List[QGraphicsItem] = []
        self.pixel_size_um: Optional[float] = None
        self.set_tool("pan")

    # ------------------------------------------------------------ content
    def set_image(self, rgb8: Optional[np.ndarray]) -> None:
        """Show a new 8-bit RGB image (or clear the view)."""
        if rgb8 is None:
            self.pix.setPixmap(QPixmap())
            return
        h, w, _ = rgb8.shape
        first = self.pix.pixmap().isNull() or (self.pix.pixmap().width(), self.pix.pixmap().height()) != (w, h)
        img = QImage(rgb8.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.pix.setPixmap(QPixmap.fromImage(img))
        self.scene().setSceneRect(QRectF(0, 0, w, h))
        if first:
            self.fit()
        self._update_scalebar()

    def image_size(self):
        """(width, height) of the displayed image in pixels."""
        pm = self.pix.pixmap()
        return (pm.width(), pm.height()) if not pm.isNull() else (0, 0)

    def set_rois(self, rois: List[RoiRecord], selected: Optional[int]) -> None:
        """Redraw every ROI outline and label; the selected ROI is drawn thicker."""
        for items in self._roi_items.values():
            for it in items:
                self.scene().removeItem(it)
        self._roi_items = {}
        for r in rois:
            if not r.visible:
                continue
            color = QColor(r.color)
            path = _path_from_points(r.geom.outline())
            item = QGraphicsPathItem(path)
            pen = QPen(color, 3.0 if r.uid == selected else 1.6)
            pen.setCosmetic(True)
            if r.uid == selected:
                pen.setStyle(Qt.DashLine)
            item.setPen(pen)
            fill = QColor(color)
            fill.setAlpha(45 if r.uid == selected else 18)
            item.setBrush(QBrush(fill))
            item.setData(0, r.uid)
            item.setZValue(5)
            label = QGraphicsSimpleTextItem(r.name)
            label.setBrush(QBrush(color))
            f = QFont()
            f.setPointSize(9)
            f.setBold(True)
            label.setFont(f)
            label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
            x0, y0, _, _ = r.geom.bbox()
            label.setPos(x0, y0)
            label.setZValue(6)
            label.setData(0, r.uid)
            self.scene().addItem(item)
            self.scene().addItem(label)
            self._roi_items[r.uid] = [item, label]

    # ---------------------------------------------------------- scale bar
    def set_scalebar(self, um: Optional[float], pixel_size_um: Optional[float]) -> None:
        """Set the scale-bar length (µm) and the pixel size used to draw it."""
        self.scalebar_um = um
        self.pixel_size_um = pixel_size_um
        self._update_scalebar()

    def _update_scalebar(self) -> None:
        """Redraw the scale bar in the bottom-right corner of the image."""
        for it in self._scalebar_items:
            self.scene().removeItem(it)
        self._scalebar_items = []
        w, h = self.image_size()
        if not (self.scalebar_um and self.pixel_size_um and w):
            return
        length_px = self.scalebar_um / self.pixel_size_um
        thick = max(3.0, h * 0.008)
        x1 = w - w * 0.04
        y = h - h * 0.05
        bar = QGraphicsRectItem(x1 - length_px, y, length_px, thick)
        bar.setBrush(QBrush(QColor("white")))
        bar.setPen(QPen(Qt.NoPen))
        bar.setZValue(8)
        txt = QGraphicsSimpleTextItem(f"{self.scalebar_um:g} µm")
        f = QFont()
        f.setPointSize(10)
        f.setBold(True)
        txt.setFont(f)
        txt.setBrush(QBrush(QColor("white")))
        txt.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        txt.setPos(x1 - length_px, y - h * 0.045)
        txt.setZValue(8)
        self.scene().addItem(bar)
        self.scene().addItem(txt)
        self._scalebar_items = [bar, txt]

    # ---------------------------------------------------------------- view
    def fit(self) -> None:
        """Scale the image to fit the view and stay in 'fit' mode."""
        self._fit = True
        if not self.pix.pixmap().isNull():
            self.fitInView(self.pix, Qt.KeepAspectRatio)

    def zoom(self, factor: float) -> None:
        """Zoom in or out by a factor and leave 'fit' mode."""
        self._fit = False
        self.scale(factor, factor)

    def resizeEvent(self, event) -> None:
        """Keep the image fitted when the window is resized in 'fit' mode."""
        super().resizeEvent(event)
        if self._fit:
            self.fit()

    def wheelEvent(self, event) -> None:
        """Mouse wheel: zoom in (up) or out (down)."""
        self.zoom(1.25 if event.angleDelta().y() > 0 else 0.8)

    def set_tool(self, tool: str) -> None:
        """Choose the mouse tool: pan/select or one of the ROI shapes."""
        self.tool = tool
        self._cancel()
        if tool == "pan":
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.viewport().setCursor(Qt.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
            self.viewport().setCursor(Qt.CrossCursor)

    # ------------------------------------------------------------- drawing
    def _cancel(self) -> None:
        """Abandon the ROI being drawn."""
        self._drawing = []
        self._start = None
        self._preview.setPath(QPainterPath())

    def _clamp(self, p: QPointF) -> QPointF:
        """Keep a point inside the image."""
        w, h = self.image_size()
        return QPointF(min(max(p.x(), 0.0), float(w)), min(max(p.y(), 0.0), float(h)))

    def mousePressEvent(self, event) -> None:
        """Start an action: pan (middle button), select an ROI, add a polygon vertex, start a
        shape, or place a point ROI.
        """
        if event.button() == Qt.MiddleButton:
            self._pan_origin = event.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        pos = self._clamp(self.mapToScene(event.position().toPoint()))
        if self.tool == "pan":
            for it in self.items(event.position().toPoint()):
                uid = it.data(0)
                if uid is not None:
                    self.roiClicked.emit(int(uid))
                    break
            super().mousePressEvent(event)
            return
        if event.button() == Qt.RightButton:
            if self.tool == "polygon" and len(self._drawing) >= 3:
                self._finish_polygon()
            else:
                self._cancel()
            return
        if event.button() != Qt.LeftButton:
            return
        if self.tool == "freehand":
            self._drawing = [pos]
        elif self.tool == "polygon":
            self._drawing.append(pos)
            self._update_polygon_preview(pos)
        elif self.tool in ("rectangle", "ellipse"):
            self._start = pos
        elif self.tool == "point":
            self.roiDrawn.emit(RoiGeometry("point", {"x": pos.x(), "y": pos.y(),
                                                     "radius": int(self.point_radius)}))

    def mouseMoveEvent(self, event) -> None:
        """Pan, extend the free-hand outline or the shape preview, and report the cursor position
        (live spectrum).
        """
        if self._pan_origin is not None:
            d = event.position() - self._pan_origin
            self._pan_origin = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        raw = self.mapToScene(event.position().toPoint())
        w, h = self.image_size()
        if 0 <= raw.x() < w and 0 <= raw.y() < h:
            self.hovered.emit(raw.x(), raw.y())
        else:
            self.hovered.emit(float("nan"), float("nan"))
        pos = self._clamp(raw)
        if self.tool == "freehand" and self._drawing and event.buttons() & Qt.LeftButton:
            last = self._drawing[-1]
            if (pos - last).manhattanLength() >= 0.75:
                self._drawing.append(pos)
                self._preview.setPath(_path_from_points(
                    np.array([[p.x(), p.y()] for p in self._drawing]), close=False))
        elif self.tool == "polygon" and self._drawing:
            self._update_polygon_preview(pos)
        elif self.tool in ("rectangle", "ellipse") and self._start is not None:
            r = QRectF(self._start, pos).normalized()
            path = QPainterPath()
            if self.tool == "rectangle":
                path.addRect(r)
            else:
                path.addEllipse(r)
            self._preview.setPath(path)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Finish a free-hand, rectangle or ellipse ROI and emit it."""
        if event.button() == Qt.MiddleButton and self._pan_origin is not None:
            self._pan_origin = None
            self.viewport().setCursor(Qt.OpenHandCursor if self.tool == "pan" else Qt.CrossCursor)
            return
        pos = self._clamp(self.mapToScene(event.position().toPoint()))
        if self.tool == "freehand" and self._drawing and event.button() == Qt.LeftButton:
            pts = np.array([[p.x(), p.y()] for p in self._drawing])
            self._cancel()
            if len(pts) >= 3:
                self.roiDrawn.emit(RoiGeometry("freehand", {"points": pts.round(2).tolist()}))
        elif self.tool in ("rectangle", "ellipse") and self._start is not None \
                and event.button() == Qt.LeftButton:
            r = QRectF(self._start, pos).normalized()
            self._cancel()
            if r.width() >= 1 and r.height() >= 1:
                if self.tool == "rectangle":
                    self.roiDrawn.emit(RoiGeometry("rectangle", {
                        "x0": r.left(), "y0": r.top(), "x1": r.right(), "y1": r.bottom()}))
                else:
                    self.roiDrawn.emit(RoiGeometry("ellipse", {
                        "cx": r.center().x(), "cy": r.center().y(),
                        "rx": r.width() / 2, "ry": r.height() / 2}))
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Double click closes the polygon being drawn."""
        if self.tool == "polygon" and len(self._drawing) >= 3:
            self._finish_polygon()
            return
        if self.tool == "pan":
            self.fit()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        """Esc cancels the ROI being drawn; Enter closes a polygon."""
        if event.key() == Qt.Key_Escape:
            self._cancel()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.tool == "polygon" \
                and len(self._drawing) >= 3:
            self._finish_polygon()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event) -> None:
        """Tell the main window that the cursor left the image (stops the live spectrum)."""
        self.hovered.emit(float("nan"), float("nan"))
        super().leaveEvent(event)

    def _update_polygon_preview(self, cursor: QPointF) -> None:
        """Draw the open polygon from the vertices placed so far to the cursor."""
        pts = np.array([[p.x(), p.y()] for p in self._drawing + [cursor]])
        self._preview.setPath(_path_from_points(pts, close=False))

    def _finish_polygon(self) -> None:
        # A double click adds the same vertex twice: drop consecutive duplicates.
        """Close the polygon and emit it as a new ROI."""
        pts = []
        for p in self._drawing:
            if not pts or (p - pts[-1]).manhattanLength() > 1e-6:
                pts.append(p)
        self._cancel()
        if len(pts) >= 3:
            self.roiDrawn.emit(RoiGeometry("polygon", {
                "points": np.array([[p.x(), p.y()] for p in pts]).round(2).tolist()}))

    # -------------------------------------------------------------- export
    def render_image(self) -> QImage:
        """Image + ROI overlays at native resolution."""
        w, h = self.image_size()
        img = QImage(w, h, QImage.Format_RGB888)
        img.fill(QColor("black"))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        self._preview.setVisible(False)
        self.scene().render(p, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
        self._preview.setVisible(True)
        p.end()
        return img
