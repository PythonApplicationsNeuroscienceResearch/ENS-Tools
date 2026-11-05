"""Overview widgets: thumbnail gallery and sortable table."""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
from PySide6.QtCore import (QAbstractListModel, QAbstractTableModel, QModelIndex, QRect, QSize, Qt,
                            Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QListView, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableView)

from ..database import Database, _clean
from .common import category_color
from .thumbnails import ThumbnailProvider

KEY_ROLE = Qt.UserRole + 1
COLOR_ROLE = Qt.UserRole + 2


# === Gallery (thumbnail grid) ================================================
class GalleryModel(QAbstractListModel):
    """Qt list model of the gallery: one item per image (thumbnail, name, colour of its category).
    """
    def __init__(self, thumbs: ThumbnailProvider, parent=None):
        """Create an empty model; thumbnails are requested from the ThumbnailProvider."""
        super().__init__(parent)
        self.db: Optional[Database] = None
        self.keys: List[str] = []
        self._row: Dict[str, int] = {}
        self.color_by = "info:clinical_group"
        self.thumbs = thumbs
        thumbs.ready.connect(self._thumb_ready)

    def set_data(self, db: Database, keys: List[str]) -> None:
        """Replace the displayed images by the given list of image keys."""
        self.beginResetModel()
        self.db, self.keys = db, list(keys)
        self._row = {k: i for i, k in enumerate(self.keys)}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        """Number of images shown."""
        return 0 if parent.isValid() else len(self.keys)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        """Data of one item for Qt: name, thumbnail, tooltip or category colour depending on the
        role.
        """
        if not index.isValid() or self.db is None:
            return None
        key = self.keys[index.row()]
        if role == Qt.DisplayRole:
            return self.db.value(key, "info:image") or key
        if role == Qt.DecorationRole:
            return self.thumbs.get(key, self.db.value(key, "info:file") or "")
        if role == KEY_ROLE:
            return key
        if role == COLOR_ROLE:
            if not self.color_by:
                return None
            return self.db.value(key, self.color_by)
        if role == Qt.ToolTipRole:
            v = self.db.value
            return (f"<b>{v(key, 'info:image')}</b><br>Slide: {v(key, 'info:slide')}"
                    f"<br>Clinical group: {v(key, 'info:clinical_group')}"
                    + (f" ({v(key, 'info:diagnosis')}, patient {v(key, 'info:patient')})"
                       if v(key, 'info:diagnosis') else "")
                    + f"<br>Plexus (DH): {v(key, 'DH:plexus')}"
                    f"<br>Majority diagnosis: {v(key, 'consensus:majority')}"
                    f" ({v(key, 'consensus:n_parkinson')} Parkinson call(s))"
                    f"<br>Neurons: {v(key, 'derived:total_neurons')}"
                    f" (large: {v(key, 'derived:total_large')})")
        return None

    def _thumb_ready(self, key: str) -> None:
        """Refresh one item when its thumbnail has been generated in the background."""
        row = self._row.get(key)
        if row is not None:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])


class GalleryDelegate(QStyledItemDelegate):
    """Draws one gallery tile: thumbnail, coloured category band and file name."""
    def __init__(self, parent=None):
        """Default thumbnail size: 150 px."""
        super().__init__(parent)
        self.thumb = 150

    def sizeHint(self, option, index) -> QSize:
        """Size of one tile (thumbnail plus margins and the name line)."""
        return QSize(self.thumb + 14, self.thumb + 34)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Paint one tile: background, thumbnail (or placeholder), category band and name."""
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.State_Selected)
        painter.setPen(QPen(QColor("#7fb2ea") if selected else QColor("#3e4f68"), 2 if selected else 1))
        painter.setBrush(QColor("#2c4466") if selected else QColor("#273142"))
        painter.drawRoundedRect(r, 6, 6)

        img_rect = QRect(r.left() + 4, r.top() + 4, r.width() - 8, r.width() - 8)
        pm = index.data(Qt.DecorationRole)
        if pm is not None and not pm.isNull():
            scaled = pm.scaled(img_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = img_rect.left() + (img_rect.width() - scaled.width()) // 2
            y = img_rect.top() + (img_rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.fillRect(img_rect, QColor("#10151d"))
            painter.setPen(QColor("#9aa4b1"))
            model, key = index.model(), index.data(KEY_ROLE)
            if not model.db.value(key, "info:file"):
                text = "image file\nnot found"
            elif model.thumbs.failed(key):
                text = "no preview"
            else:
                text = "loading…"
            painter.drawText(img_rect, Qt.AlignCenter, text)

        cat = index.data(COLOR_ROLE)
        if index.model().color_by:
            painter.fillRect(QRect(img_rect.left(), img_rect.bottom() - 5, img_rect.width(), 6),
                             category_color(cat))

        painter.setPen(QColor("#e4e9f1"))
        f = QFont(option.font)
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        painter.setFont(f)
        text_rect = QRect(r.left() + 4, img_rect.bottom() + 3, r.width() - 8, 22)
        name = painter.fontMetrics().elidedText(str(index.data(Qt.DisplayRole)), Qt.ElideMiddle,
                                                text_rect.width())
        painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignVCenter, name)
        painter.restore()


class GalleryView(QListView):
    """Grid of thumbnails; double click or Enter opens the identity card of the image."""
    openRequested = Signal(str)

    def __init__(self, model: GalleryModel, parent=None):
        """Configure the list view in icon mode with the custom tile delegate."""
        super().__init__(parent)
        self.setModel(model)
        self.delegate = GalleryDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.doubleClicked.connect(lambda idx: self.openRequested.emit(idx.data(KEY_ROLE)))

    def set_thumb_size(self, px: int) -> None:
        """Change the thumbnail size (Size slider) and re-layout the grid."""
        self.delegate.thumb = px
        self.setGridSize(QSize(px + 18, px + 38))
        self.doItemsLayout()

    def selected_keys(self) -> List[str]:
        """Image keys of the selected tiles."""
        return [i.data(KEY_ROLE) for i in self.selectionModel().selectedIndexes()]

    def keyPressEvent(self, event) -> None:
        """Enter opens the identity card of the current image."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentIndex().isValid():
            self.openRequested.emit(self.currentIndex().data(KEY_ROLE))
            return
        super().keyPressEvent(event)


DEFAULT_TABLE_COLUMNS = [
    "info:image", "info:slide", "info:clinical_group", "info:diagnosis", "info:patient", "DH:plexus",
    "consensus:majority", "consensus:n_parkinson", "consensus:n_correct", "derived:total_neurons", "derived:total_large",
    "derived:large_with_granules", "derived:large_with_nucleoli", "derived:max_granule_grade_large",
    "DH:nice_picture", "info:has_spectral",
]


# === Table view ==============================================================
class TableModel(QAbstractTableModel):
    """Qt table model of the Table tab: one row per image, user-chosen columns."""
    def __init__(self, parent=None):
        """Create an empty model with the default columns."""
        super().__init__(parent)
        self.db: Optional[Database] = None
        self.keys: List[str] = []
        self.columns: List[str] = list(DEFAULT_TABLE_COLUMNS)
        self._sort = (None, Qt.AscendingOrder)

    def set_data(self, db: Database, keys: List[str]) -> None:
        """Replace the rows by the given image keys (keeping only columns that exist)."""
        self.beginResetModel()
        self.db = db
        self.columns = [c for c in self.columns if c in db.df.columns]
        self.keys = list(keys)
        self.endResetModel()
        if self._sort[0] is not None:
            self.sort(*self._sort)

    def set_columns(self, columns: List[str]) -> None:
        """Change the displayed columns (Table columns menu)."""
        self.beginResetModel()
        self.columns = list(columns)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        """Number of rows (images)."""
        return 0 if parent.isValid() else len(self.keys)

    def columnCount(self, parent=QModelIndex()) -> int:
        """Number of displayed columns."""
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        """Column labels (horizontal header) and row numbers (vertical header)."""
        if role != Qt.DisplayRole or self.db is None:
            return None
        if orientation == Qt.Horizontal:
            col = self.columns[section]
            spec = self.db.fields.get(col)
            return spec.label if spec else col
        return str(section + 1)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        """Text of one cell; group and diagnosis columns are coloured by category."""
        if not index.isValid() or self.db is None:
            return None
        key = self.keys[index.row()]
        col = self.columns[index.column()]
        if role == Qt.DisplayRole:
            v = self.db.value(key, col)
            if v is None:
                return ""
            if isinstance(v, float) and v.is_integer():
                return str(int(v))
            return str(v)
        if role == KEY_ROLE:
            return key
        if role == Qt.ForegroundRole and col in ("consensus:majority", "info:clinical_group",
                                                     "info:diagnosis"):
            return category_color(self.db.value(key, col))
        return None

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        """Sort the rows by one column (numbers numerically, grades by order, text alphabetically).
        """
        if self.db is None or column < 0 or column >= len(self.columns):
            return
        self._sort = (column, order)
        col = self.columns[column]
        spec = self.db.fields.get(col)
        s = self.db.df.loc[self.keys, col].map(_clean)
        if spec and spec.kind == "numeric":
            s = pd.to_numeric(s, errors="coerce")
        elif spec and spec.kind == "ordinal" and spec.order:
            lower = [o.lower() for o in spec.order]
            s = s.map(lambda v: lower.index(str(v).lower()) if v is not None and str(v).lower() in lower
                      else None).astype(float)
        else:
            s = s.map(lambda v: None if v is None else str(v).lower())
        ordered = s.sort_values(ascending=(order == Qt.AscendingOrder), na_position="last",
                                kind="mergesort")
        self.layoutAboutToBeChanged.emit()
        self.keys = list(ordered.index)
        self.layoutChanged.emit()


class TableView(QTableView):
    """Sortable table of the images; double click opens the identity card."""
    openRequested = Signal(str)

    def __init__(self, model: TableModel, parent=None):
        """Configure row selection, sorting and column resizing."""
        super().__init__(parent)
        self.setModel(model)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setDefaultSectionSize(22)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.doubleClicked.connect(lambda idx: self.openRequested.emit(idx.data(KEY_ROLE)))

    def selected_keys(self) -> List[str]:
        """Image keys of the selected rows, in table order."""
        rows = sorted({i.row() for i in self.selectionModel().selectedIndexes()})
        return [self.model().keys[r] for r in rows]
