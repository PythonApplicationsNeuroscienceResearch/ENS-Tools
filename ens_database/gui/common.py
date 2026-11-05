"""Shared GUI helpers: colour scheme, image conversion, zoomable image viewer."""
from __future__ import annotations

from typing import Optional

from PIL import Image
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import (QFrame, QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QLabel)

# Category colours (colour-blind-friendly, readable on white).
CATEGORY_COLORS = {
    "Parkinson": "#c8414b",
    "Alzheimer": "#7a5195",
    "Control": "#2a9d8f",
    "Control (aged)": "#2a9d8f",
    "Control (young)": "#5fc4b8",
    "Healthy": "#2a9d8f",
    "OtherPathology": "#e09f3e",
    "Other": "#e09f3e",
    "Tie": "#7a8699",
    "Unassigned": "#66758c",
    "Auerbach": "#3a6ea5",
    "Meissner": "#e07a2f",
    "NoENS": "#6f7d91",
    "Undetemined": "#8793a6",
    "Undetermined": "#8793a6",
    "ENS": "#5b8e7d",
    "Both": "#6c757d",
    None: "#4a5568",
}
# Theme names re-exported here, so that other modules (e.g. __main__) can import them from common.
from .theme import (ACCENT, BORDER, BORDER_STRONG, MUTED, SURFACE, SURFACE_2,  # noqa: F401
                    TEXT, apply_style)


def category_color(value) -> QColor:
    """Colour used for a category (clinical group, diagnosis, plexus) in the gallery and legends."""
    return QColor(CATEGORY_COLORS.get(value, "#6c8ebf" if value is not None else "#4a5568"))


def pil_to_qimage(img: Image.Image) -> QImage:
    """Convert a PIL image to a QImage that owns its pixel buffer."""
    if img.mode not in ("RGB", "RGBA"):
        if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
            import numpy as np
            a = np.asarray(img, dtype=np.float32)
            lo, hi = np.percentile(a, (0.5, 99.8))
            a = np.clip((a - lo) / max(hi - lo, 1e-9), 0, 1) * 255
            img = Image.fromarray(a.astype("uint8"))
        img = img.convert("RGB")
    if img.mode == "RGB":
        img = img.convert("RGBA")
    w, h = img.size
    data = img.tobytes("raw", "RGBA")
    return QImage(data, w, h, 4 * w, QImage.Format_RGBA8888).copy()


def load_qimage(path: str) -> QImage:
    """Load an image file or a '<archive>.zip::<member>' image."""
    from .. import imagefiles
    return pil_to_qimage(imagefiles.open_image(path))


class ElidedLabel(QLabel):
    """One-line label that shortens long text with '…' instead of widening the window."""

    def __init__(self, text: str = "", parent=None):
        """Label that shortens long text with '…' in the middle and shows the full text as a
        tooltip.
        """
        super().__init__(parent)
        from PySide6.QtWidgets import QSizePolicy
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        """Store the full text and display the shortened version that fits the label width."""
        self._full = text or ""
        self.setToolTip(self._full)
        self._refresh()

    def fullText(self) -> str:  # noqa: N802
        """Full, non-shortened text."""
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Recompute the shortened text when the label is resized."""
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        """Shorten the full text to the current width (middle elision)."""
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideMiddle, max(10, self.width()))
        QLabel.setText(self, shown)


def chip(text: str, color: Optional[str] = None) -> QLabel:
    """Small rounded label used for categorical badges."""
    lab = QLabel(text)
    c = QColor(color or "#8d99ae")
    lab.setStyleSheet(
        f"QLabel {{ background: {c.name()}; color: white; border-radius: 9px;"
        f" padding: 2px 9px; font-weight: 600; }}")
    return lab


class ImageView(QGraphicsView):
    """Zoomable / pannable image viewer (wheel = zoom, drag = pan, double-click = fit)."""
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        """Zoomable, pannable image viewer built on a QGraphicsView with one pixmap item."""
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self._item)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#0e1218"))
        self.setFrameShape(QFrame.NoFrame)
        self._fit = True

    def set_image(self, image: Optional[QImage]) -> None:
        """Show a new image (or clear the view when the image is empty)."""
        if image is None or image.isNull():
            self._item.setPixmap(QPixmap())
            return
        self._item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(QRectF(self._item.pixmap().rect()))
        self.fit()

    def fit(self) -> None:
        """Scale the image to fit the view and stay in 'fit' mode on resize."""
        self._fit = True
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.KeepAspectRatio)
            self.zoomChanged.emit(self.transform().m11())

    def zoom(self, factor: float) -> None:
        """Zoom in or out by a factor and leave 'fit' mode."""
        self._fit = False
        self.scale(factor, factor)
        self.zoomChanged.emit(self.transform().m11())

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Mouse wheel: zoom in (up) or out (down)."""
        self.zoom(1.25 if event.angleDelta().y() > 0 else 0.8)

    def mouseDoubleClickEvent(self, event) -> None:
        """Double click: fit the image to the view again."""
        self.fit()

    def resizeEvent(self, event) -> None:
        """Keep the image fitted when the view is resized in 'fit' mode."""
        super().resizeEvent(event)
        if self._fit:
            self.fit()
