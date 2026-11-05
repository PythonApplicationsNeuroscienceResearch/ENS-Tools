"""Dialogs: data locations, clinical groups, data integrity report, value editor."""
from __future__ import annotations

import csv
import os
from typing import Dict, List

from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QToolButton,
                               QVBoxLayout, QWidget)

from ..database import CLINICAL_GROUPS, Database, Edit
from ..xlsx_patch import col_letter
from .theme import fit_to_screen


# --------------------------------------------------------------------------- #
class PathRow(QWidget):
    """Line edit plus 'Browse…' button to choose a file or a folder."""
    def __init__(self, value: str, folder: bool, filt: str = "", parent=None):
        """Build the row; folder=True opens a folder chooser, otherwise a file chooser with filter
        filt.
        """
        super().__init__(parent)
        self.folder, self.filt = folder, filt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value)
        b = QToolButton()
        b.setText("Browse…")
        b.clicked.connect(self._browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(b)

    def _browse(self) -> None:
        """Open the file or folder chooser and copy the choice into the line edit."""
        start = self.edit.text() or os.path.expanduser("~")
        if self.folder:
            p = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Choose file", start, self.filt)
        if p:
            self.edit.setText(os.path.normpath(p))

    def text(self) -> str:
        """Chosen path, without surrounding spaces."""
        return self.edit.text().strip()


class LocationsDialog(QDialog):
    """Where are the Excel database, the images and (optionally) the spectral stacks?"""

    def __init__(self, excel: str, images: str, spectral: str, parent=None):
        """Build the dialog with one path row per location (workbook, images, spectral stacks)."""
        super().__init__(parent)
        self.setWindowTitle("Data locations")
        self.setMinimumWidth(680)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Select the Excel database and the folder that contains the images "
            "(sub-folders and .zip archives are searched, so the per-slide archives can be used "
            "without extracting them).\nThe spectral folder is optional: when set, images with a "
            "matching spectral stack get a link to the Spectral Analyzer.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.excel = PathRow(excel, False, "Excel workbook (*.xlsx *.xlsm)")
        self.images = PathRow(images, True)
        self.spectral = PathRow(spectral, True)
        form.addRow("Excel database (.xlsx)", self.excel)
        form.addRow("Image folder", self.images)
        form.addRow("Spectral stacks folder (optional)", self.spectral)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._check)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _check(self) -> None:
        """Accept only if the workbook and the image folder exist."""
        if not os.path.isfile(self.excel.text()):
            QMessageBox.warning(self, "Data locations", "The Excel file does not exist.")
            return
        if not os.path.isdir(self.images.text()):
            QMessageBox.warning(self, "Data locations", "The image folder does not exist.")
            return
        if self.spectral.text() and not os.path.isdir(self.spectral.text()):
            QMessageBox.warning(self, "Data locations", "The spectral folder does not exist.")
            return
        self.accept()

    def values(self):
        """The three chosen paths: (Excel file, image folder, spectral folder)."""
        return self.excel.text(), self.images.text(), self.spectral.text()


# --------------------------------------------------------------------------- #
class ClinicalGroupsDialog(QDialog):
    """Assign each slide (= patient sample) to a clinical group."""

    def __init__(self, db: Database, parent=None):
        """One drop-down list per slide to set its clinical group (rule-based value preselected)."""
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Clinical group of each slide")
        self.resize(520, 560)
        lay = QVBoxLayout(self)
        lab = QLabel(
            "Clinical group of each patient (slide), as defined by the lab: slides BK11.. = "
            "Alzheimer, BK12.. = Parkinson, all other slides = Control. Change a group here only "
            "to override this rule (e.g. new slides with another code).\n"
            f"Overrides saved in: {db.groups_path}")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        counts = db.df["info:slide"].value_counts().to_dict()
        slides = db.slides()
        self.table = QTableWidget(len(slides), 3)
        self.table.setHorizontalHeaderLabels(["Slide", "Images (n)", "Clinical group"])
        self.table.verticalHeader().setVisible(False)
        self.combos: Dict[str, QComboBox] = {}
        for i, s in enumerate(slides):
            self.table.setItem(i, 0, QTableWidgetItem(s))
            self.table.setItem(i, 1, QTableWidgetItem(str(counts.get(s, 0))))
            cb = QComboBox()
            cb.setEditable(True)
            cb.addItems(CLINICAL_GROUPS)
            cb.setCurrentText(db.group_of(s))
            self.table.setCellWidget(i, 2, cb)
            self.combos[s] = cb
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def mapping(self) -> Dict[str, str]:
        """Chosen group of every slide (slide -> group)."""
        return {s: (cb.currentText().strip() or "Unassigned") for s, cb in self.combos.items()}


# --------------------------------------------------------------------------- #
class IntegrityDialog(QDialog):
    """List every inconsistency found between the workbook and the image folder."""

    def __init__(self, db: Database, parent=None):
        """Table of all data problems found while loading, with a category filter and CSV export."""
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Data integrity report")
        fit_to_screen(self, 860, 560)
        lay = QVBoxLayout(self)
        n_img = len(db.df)
        n_file = int((db.df["info:file"] != "").sum())
        summary = QLabel(
            f"<b>{n_img}</b> images in '{db.morph.name}', <b>{n_file}</b> with an image file, "
            f"<b>{len(db.orphan_files)}</b> image file(s) without an Excel row, "
            f"<b>{len(db.raters)}</b> pathologist sheets ({', '.join(r.code for r in db.raters)}).")
        summary.setWordWrap(True)
        lay.addWidget(summary)
        filt = QComboBox()
        cats = sorted({c for c, _ in db.issues})
        filt.addItem("All categories")
        filt.addItems(cats)
        lay.addWidget(filt)
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Category", "Detail"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        filt.currentTextChanged.connect(self._fill)
        self._fill("All categories")
        hl = QHBoxLayout()
        b = QPushButton("Export as CSV…")
        b.clicked.connect(self._export)
        hl.addWidget(b)
        hl.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        hl.addWidget(bb)
        lay.addLayout(hl)

    def _fill(self, cat: str) -> None:
        """Show the problems of one category (or all of them)."""
        rows = [i for i in self.db.issues if cat == "All categories" or i[0] == cat]
        self.table.setRowCount(len(rows))
        for r, (c, m) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(c))
            self.table.setItem(r, 1, QTableWidgetItem(m))
        self.table.resizeColumnToContents(0)

    def _export(self) -> None:
        """Save the problems to a CSV file."""
        path, _ = QFileDialog.getSaveFileName(self, "Export report", "integrity_report.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["category", "detail"])
                w.writerows(self.db.issues)


# --------------------------------------------------------------------------- #
def _raw_text(v) -> str:
    """Value as editable text (empty for missing values and NaN)."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _parse(text: str, kind: str):
    """Convert typed text to the field type (number for numeric fields); raises ValueError if
    invalid.
    """
    text = text.strip()
    if text == "":
        return None
    if kind == "numeric":
        x = float(text.replace(",", "."))
        return int(x) if x.is_integer() else x
    return text


class EditDialog(QDialog):
    """Edit the workbook values of one image; returns a list of :class:`Edit`."""

    def __init__(self, db: Database, key: str, parent=None):
        """One input per editable field of the image, prefilled with its current value."""
        super().__init__(parent)
        self.db, self.key = db, key
        self.setWindowTitle(f"Edit values: {db.value(key, 'info:image')}")
        fit_to_screen(self, 720, 760)
        lay = QVBoxLayout(self)
        lab = QLabel("Changes are written into the Excel file (a backup copy is made first). "
                     "Leave a field empty to clear the cell. Formula columns are not editable: "
                     "the application recomputes the totals.")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        self.widgets: Dict[str, QWidget] = {}
        self.original: Dict[str, str] = {}
        groups: Dict[str, QFormLayout] = {}
        for cid, spec in db.fields.items():
            if not spec.editable:
                continue
            sheet = next(s for s in db.sheets if s.name == spec.source_sheet)
            if key not in sheet.row_of:
                continue
            if spec.group not in groups:
                box = QGroupBox(f"{spec.group}   (sheet '{spec.source_sheet}', row {sheet.row_of[key]})")
                groups[spec.group] = QFormLayout(box)
                bl.addWidget(box)
            raw = db.df.at[key, cid]
            text = _raw_text(raw)
            if spec.kind in ("ordinal", "categorical"):
                w = QComboBox()
                w.setEditable(True)
                opts = [""] + db.categories(cid)
                if text not in opts:
                    opts.append(text)
                w.addItems(opts)
                w.setCurrentText(text)
            else:
                w = QLineEdit(text)
                if spec.kind == "numeric":
                    w.setValidator(QDoubleValidator())
            col = sheet.columns[spec.source_field]
            w.setToolTip(f"Cell {col_letter(col)}{sheet.row_of[key]}")
            groups[spec.group].addRow(spec.label.split(": ", 1)[-1], w)
            self.widgets[cid] = w
            self.original[cid] = text
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def edits(self) -> List[Edit]:
        """List of the fields whose value was changed by the user."""
        out: List[Edit] = []
        for cid, w in self.widgets.items():
            text = w.currentText() if isinstance(w, QComboBox) else w.text()
            if text.strip() == self.original[cid].strip():
                continue
            out.append(Edit(cid, self.key, _parse(text, self.db.fields[cid].kind)))
        return out


def confirm_edits(parent, db: Database, edits: List[Edit]) -> bool:
    """Show the cells that will be overwritten (old -> new value) and ask for confirmation."""
    lines = []
    for e in edits:
        sheet, row, col = db.cell_of(e.column, e.key)
        old = _raw_text(db.df.at[e.key, e.column])
        new = "" if e.value is None else str(e.value)
        lines.append(f"<tr><td>{sheet}!{col_letter(col)}{row}</td><td>{db.fields[e.column].label}</td>"
                     f"<td>{old or '<i>empty</i>'}</td><td>→</td><td><b>{new or '<i>empty</i>'}</b></td></tr>")
    box = QMessageBox(parent)
    box.setWindowTitle("Write changes to Excel")
    box.setIcon(QMessageBox.Question)
    box.setText(f"Write {len(edits)} change(s) into the Excel file?")
    box.setInformativeText("<table cellspacing=6>" + "".join(lines) + "</table>")
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
    return box.exec() == QMessageBox.Yes


class AboutDialog(QDialog):
    """Version, authors and acknowledgements."""
    def __init__(self, text: str, parent=None):
        """Show the credits text in a read-only browser with clickable links."""
        super().__init__(parent)
        self.setWindowTitle("About")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setHtml(text)
        lay.addWidget(tb)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
