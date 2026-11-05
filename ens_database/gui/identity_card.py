"""Identity card of one image: picture + every piece of information available."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from .. import imagefiles
from ..database import Database
from .common import CATEGORY_COLORS, ElidedLabel, ImageView, chip, load_qimage


def _fmt(v) -> str:
    """Value as displayed on the card ('–' when missing, integers without decimals)."""
    if v is None:
        return "–"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


class IdentityCard(QWidget):
    """Full-page view of one image: zoomable image on the left, all its information on the right."""
    backRequested = Signal()
    navigate = Signal(int)                 # -1 previous, +1 next
    editRequested = Signal(str)
    spectralRequested = Signal(str)

    def __init__(self, parent=None):
        """Build the header bar, the image viewer and the information sections."""
        super().__init__(parent)
        self.db: Optional[Database] = None
        self.key: Optional[str] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        # ---- header bar
        bar = QHBoxLayout()
        self.btn_back = QPushButton("◀  Overview")
        self.btn_back.clicked.connect(self.backRequested)
        self.btn_prev = QToolButton()
        self.btn_prev.setText("‹ Previous")
        self.btn_prev.clicked.connect(lambda: self.navigate.emit(-1))
        self.btn_next = QToolButton()
        self.btn_next.setText("Next ›")
        self.btn_next.clicked.connect(lambda: self.navigate.emit(1))
        self.position = QLabel()
        self.position.setObjectName("muted")
        self.title = QLabel()
        self.title.setObjectName("title")
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        self.btn_edit = QPushButton("Edit values…")
        self.btn_edit.setObjectName("primary")
        self.btn_edit.clicked.connect(lambda: self.key and self.editRequested.emit(self.key))
        bar.addWidget(self.btn_back)
        bar.addSpacing(10)
        bar.addWidget(self.btn_prev)
        bar.addWidget(self.btn_next)
        bar.addWidget(self.position)
        bar.addStretch(1)
        bar.addWidget(self.btn_edit)
        outer.addLayout(bar)
        # Second row: image name and category chips (kept apart from the buttons so that
        # the window stays narrow enough for laptop screens).
        bar2 = QHBoxLayout()
        bar2.addWidget(self.title)
        bar2.addSpacing(8)
        bar2.addLayout(self.chips)
        bar2.addStretch(1)
        outer.addLayout(bar2)

        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split, 1)

        # ---- image side
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.view = ImageView()
        ll.addWidget(self.view, 1)
        row = QHBoxLayout()
        for text, fn in (("Fit", lambda: self.view.fit()), ("Zoom +", lambda: self.view.zoom(1.25)),
                         ("Zoom −", lambda: self.view.zoom(0.8)),
                         ("1:1", self._one_to_one)):
            b = QToolButton()
            b.setText(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        self.path_label = ElidedLabel()
        self.path_label.setObjectName("muted")
        row.addWidget(self.path_label, 1)
        b = QToolButton()
        b.setText("Open folder")
        b.clicked.connect(self._open_folder)
        row.addWidget(b)
        ll.addLayout(row)
        split.addWidget(left)

        # ---- information side
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumWidth(360)
        info = QWidget()
        self.info_layout = QVBoxLayout(info)
        self.info_layout.setContentsMargins(4, 0, 4, 4)
        scroll.setWidget(info)
        split.addWidget(scroll)
        split.setSizes([700, 640])
        split.setChildrenCollapsible(False)

        self.box_morph = QGroupBox("Neuronal morphometry (DH, semi-quantitative)")
        self.morph_grid = QGridLayout(self.box_morph)
        self.info_layout.addWidget(self.box_morph)

        self.box_other = QGroupBox("Tissue and image")
        self.other_grid = QGridLayout(self.box_other)
        self.info_layout.addWidget(self.box_other)

        self.box_path = QGroupBox("Blinded pathologist assessments")
        pl = QVBoxLayout(self.box_path)
        self.consensus = QLabel()
        self.consensus.setWordWrap(True)
        pl.addWidget(self.consensus)
        self.path_rows = QVBoxLayout()
        self.path_rows.setSpacing(4)
        pl.addLayout(self.path_rows)
        self.info_layout.addWidget(self.box_path)

        self.box_spec = QGroupBox("Spectral imaging")
        self.spec_layout = QVBoxLayout(self.box_spec)
        self.info_layout.addWidget(self.box_spec)

        self.box_all = QGroupBox("All fields")
        al = QVBoxLayout(self.box_all)
        self.btn_all = QToolButton()
        self.btn_all.setText("Show every stored field ▾")
        self.btn_all.setCheckable(True)
        self.all_table = QTableWidget()
        self.all_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.all_table.verticalHeader().setVisible(False)
        self.all_table.setVisible(False)
        self.btn_all.toggled.connect(self.all_table.setVisible)
        al.addWidget(self.btn_all)
        al.addWidget(self.all_table)
        self.info_layout.addWidget(self.box_all)
        self.info_layout.addStretch(1)

        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self.navigate.emit(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self.navigate.emit(1))
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.backRequested)

    # ------------------------------------------------------------------ API
    def show_image(self, db: Database, key: str, position: str = "") -> None:
        """Display one image and fill every section with its data."""
        self.db, self.key = db, key
        v = lambda c: db.value(key, c)  # noqa: E731
        self.position.setText(position)
        self.title.setText(_fmt(v("info:image")))
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.chips.addWidget(chip(f"Slide {_fmt(v('info:slide'))}", "#4a5568"))
        grp = v("info:clinical_group")
        self.chips.addWidget(chip(f"Group: {_fmt(grp)}", CATEGORY_COLORS.get(grp)))
        plex = v("DH:plexus")
        self.chips.addWidget(chip(f"Plexus: {_fmt(plex)}", CATEGORY_COLORS.get(plex)))
        maj = v("consensus:majority")
        if maj:
            self.chips.addWidget(chip(f"Majority: {maj}", CATEGORY_COLORS.get(maj)))

        path = v("info:file")
        self.path_label.setText(path or "image file not found")
        if path and imagefiles.exists(path):
            try:
                self.view.set_image(load_qimage(path))
            except Exception:  # noqa: BLE001
                self.view.set_image(None)
        else:
            self.view.set_image(None)

        self._fill_morphology()
        self._fill_other()
        self._fill_pathologists()
        self._fill_spectral()
        self._fill_all()

    # ------------------------------------------------------------- sections
    def _clear_grid(self, grid: QGridLayout) -> None:
        """Remove every widget of a grid before refilling it."""
        while grid.count():
            w = grid.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _cell(self, text: str, bold=False, color: Optional[str] = None, align=Qt.AlignCenter) -> QLabel:
        """Label used as one cell of the information grids."""
        lab = QLabel(text)
        lab.setAlignment(align)
        lab.setWordWrap(True)
        style = "padding: 3px 6px;"
        if bold:
            style += "font-weight: 600;"
        if color:
            style += f"background: {color}; color: #1b2230; border-radius: 4px;"
        lab.setStyleSheet(style)
        return lab

    def _fill_morphology(self) -> None:
        """Fill the morphometry grid: small/large neurons × granules × nucleoli, with granule
        grades.
        """
        g = self.morph_grid
        self._clear_grid(g)
        db, key = self.db, self.key
        code = db.morph.code
        heads = ["", "No granules\nnucleoli +", "No granules\nnucleoli −", "Granules\nnucleoli +",
                 "Granules\nnucleoli −", "Total"]
        for j, h in enumerate(heads):
            g.addWidget(self._cell(h, bold=True), 0, j)
        grade_bg = {"+": "#fde8c8", "++": "#f9c784", "+++": "#f4a259"}
        for i, size in enumerate(("small", "large"), start=1):
            g.addWidget(self._cell(f"{size.capitalize()} neurons", bold=True, align=Qt.AlignLeft), i, 0)
            cols = [("without", "with"), ("without", "without"), ("with", "with"), ("with", "without")]
            for j, (gran, nucl) in enumerate(cols, start=1):
                n = db.value(key, f"{code}:{size}_nerve_cell_{gran}_granules_{nucl}_nucleoli_nb")
                text = _fmt(n)
                color = None
                if gran == "with":
                    grade = db.value(key, f"{code}:{size}_nerve_cell_with_granules_{nucl}_nucleoli_granules")
                    if grade and str(grade).lower() != "none":
                        text += f"   ({grade})"
                        color = grade_bg.get(str(grade))
                g.addWidget(self._cell(text, color=color), i, j)
            g.addWidget(self._cell(_fmt(db.value(key, f"derived:total_{size}")), bold=True), i, 5)
        g.addWidget(self._cell("All", bold=True, align=Qt.AlignLeft), 3, 0)
        g.addWidget(self._cell(_fmt(db.value(key, "derived:total_neurons")), bold=True), 3, 5)
        note = QLabel("Counts per image; granule load graded none / + / ++ / +++ (shaded cells).")
        note.setObjectName("muted")
        g.addWidget(note, 4, 0, 1, 6)

    def _fill_other(self) -> None:
        """Fill the tissue and image information (plexus, infiltrating cells, picture quality,
        slide, group, patient).
        """
        g = self.other_grid
        self._clear_grid(g)
        db, key, code = self.db, self.key, self.db.morph.code
        ep = db.value(key, f"{code}:empty_plexus")
        rows = [
            ("Plexus (DH)", db.value(key, f"{code}:plexus")),
            ("Empty plexus", {1: "Yes", 0: "No"}.get(ep, ep) if ep is not None else None),
            ("Infiltrating cells", db.value(key, f"{code}:infiltrating_cell_nb")),
            ("Infiltrating cells, surrounding tissue", db.value(key, f"{code}:infiltrating_cell_in_env_tissue")),
            ("Picture quality (nice_picture)", db.value(key, f"{code}:nice_picture")),
            ("Comments (DH)", db.value(key, f"{code}:comments")),
            ("Slide", db.value(key, "info:slide")),
            ("Clinical group (slide)", db.value(key, "info:clinical_group")),
            ("Diagnosis (detailed)", db.value(key, "info:diagnosis")),
            ("Patient", db.value(key, "info:patient")),
        ]
        for i, (k, val) in enumerate(rows):
            g.addWidget(self._cell(k, bold=True, align=Qt.AlignLeft), i, 0)
            g.addWidget(self._cell(_fmt(val), align=Qt.AlignLeft), i, 1)
        g.setColumnStretch(1, 1)

    def _fill_pathologists(self) -> None:
        """Fill the blinded pathologist assessments and their consensus."""
        db, key = self.db, self.key
        n = len(db.raters)
        n_pd = db.value(key, "consensus:n_parkinson")
        n_r = db.value(key, "consensus:n_raters")
        maj = db.value(key, "consensus:majority")
        self.consensus.setText(
            f"<b>{_fmt(n_pd)} of {_fmt(n_r)}</b> rating pathologist(s) called Parkinson "
            f"({n} pathologists in the study). Majority diagnosis: <b>{_fmt(maj)}</b>.")
        lay = self.path_rows
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for r in db.raters:
            frame = QFrame()
            frame.setStyleSheet("QFrame#rater { background: #273142; border: 1px solid #3e4f68;"
                                " border-radius: 5px; }")
            frame.setObjectName("rater")
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(8, 5, 8, 5)
            fl.setSpacing(3)
            head = QHBoxLayout()
            name = QLabel(f"<b>{r.code}</b> <span style='color:#9aa7ba'>({r.login})</span>")
            head.addWidget(name)
            head.addStretch(1)
            if key not in r.records:
                lab = QLabel("not rated")
                lab.setObjectName("muted")
                head.addWidget(lab)
                fl.addLayout(head)
                lay.addWidget(frame)
                continue
            diag = db.value(key, f"{r.code}:diagnosis")
            head.addWidget(chip(_fmt(diag), CATEGORY_COLORS.get(diag)))
            fl.addLayout(head)
            sub = QLabel(f"Plexus: <b>{_fmt(db.value(key, f'{r.code}:plexus'))}</b>"
                         f"  ·  Certainty: <b>{_fmt(db.value(key, f'{r.code}:certainty'))}</b>")
            sub.setWordWrap(True)
            fl.addWidget(sub)
            morph = db.value(key, f"{r.code}:morphology")
            comm = db.value(key, f"{r.code}:comments")
            text = []
            if morph:
                text.append(f"<i>Features:</i> {morph}")
            if comm:
                text.append(f"<i>Comment:</i> {comm}")
            if text:
                lab = QLabel("<br>".join(text))
                lab.setWordWrap(True)
                lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
                fl.addWidget(lab)
            lay.addWidget(frame)

    def _fill_spectral(self) -> None:
        """List the spectral stacks of this image, each with a button that opens it in the Spectral
        Analyzer.
        """
        lay = self.spec_layout
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        files = [p for p in (self.db.value(self.key, "info:spectral_files") or "").split("; ") if p]
        if not files:
            lab = QLabel("No spectral stack linked to this image." if self.db.spectral_root else
                         "No spectral folder configured (Settings › Data locations).")
            lab.setObjectName("muted")
            lay.addWidget(lab)
            return
        for p in files:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(QLabel(os.path.basename(p)), 1)
            b = QPushButton("Open in Spectral Analyzer")
            b.clicked.connect(lambda _=False, path=p: self.spectralRequested.emit(path))
            hl.addWidget(b)
            lay.addWidget(row)

    def _fill_all(self) -> None:
        """Fill the table of every stored field of the image."""
        db, key = self.db, self.key
        t = self.all_table
        rows = [(spec.group, spec.label, _fmt(db.value(key, cid))) for cid, spec in db.fields.items()]
        t.clear()
        t.setColumnCount(3)
        t.setHorizontalHeaderLabels(["Section", "Field", "Value"])
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, text in enumerate(r):
                t.setItem(i, j, QTableWidgetItem(text))
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        t.setMinimumHeight(min(600, 24 * len(rows) + 30))

    # --------------------------------------------------------------- misc
    def _open_folder(self) -> None:
        """Open the folder containing the image file in the system file browser."""
        if self.db and self.key:
            p = self.db.value(self.key, "info:file")
            if p:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(imagefiles.container(p))))

    def _one_to_one(self) -> None:
        """Show the image at 100 % (one screen pixel per image pixel)."""
        self.view.resetTransform()
        self.view._fit = False
