"""Search panel: quick filters plus a free condition builder.

Quick filters (check boxes) express 'is any of' choices for the most frequent
questions (clinical group, majority diagnosis, plexus, slide).  The condition
builder covers everything else, e.g. 'Large neurons with nucleoli >= 1 AND
Granule load, large neurons >= ++'.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from ..database import CLINICAL_GROUPS, Database, _clean
from ..query import (NO_VALUE_OPS, OPERATORS, Condition, apply_query, describe, load_query,
                     save_query)

PRESETS = {
    "Parkinson group: large neurons with granules AND nucleolus": (
        [Condition("info:clinical_group", "is", "Parkinson"),
         Condition("derived:large_with_granules_and_nucleoli", "≥", 1)], "all"),
    "Alzheimer group: large neurons with granules AND nucleolus": (
        [Condition("info:clinical_group", "is", "Alzheimer"),
         Condition("derived:large_with_granules_and_nucleoli", "≥", 1)], "all"),
    "Control group: large neurons with granules AND nucleolus": (
        [Condition("info:clinical_group", "is", "Control"),
         Condition("derived:large_with_granules_and_nucleoli", "≥", 1)], "all"),
    "Parkinson calls (≥ 2/4) with large neurons": (
        [Condition("consensus:n_parkinson", "≥", 2), Condition("derived:total_large", "≥", 1)], "all"),
    "Large neurons with nucleoli and granule load ≥ ++": (
        [Condition("derived:large_with_nucleoli", "≥", 1),
         Condition("derived:max_granule_grade_large", "≥", "++")], "all"),
    "Heavy granule load (+++) in large neurons": (
        [Condition("derived:max_granule_grade_large", "=", "+++")], "all"),
    "Empty plexus": ([Condition("DH:empty_plexus", "=", 1)], "all"),
    "Pathologists split (tie)": ([Condition("consensus:majority", "is", "Tie")], "all"),
    "Unanimous Parkinson call (4/4)": ([Condition("consensus:n_parkinson", "≥", 4)], "all"),
    "Images with a spectral stack": ([Condition("info:has_spectral", "is", "Yes")], "all"),
}


# === Quick-filter check boxes ================================================
class CheckGroup(QGroupBox):
    """Group of check boxes, one per category ('is any of' semantics)."""
    changed = Signal()

    def __init__(self, title: str, column: str, parent=None):
        """Framed group of check boxes, one per category of a column (e.g. clinical group)."""
        super().__init__(title, parent)
        self.column = column
        self.boxes: Dict[str, QCheckBox] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(6, 4, 6, 4)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(2)

    def set_categories(self, cats: List[str], counts: Dict[str, int]) -> None:
        """Rebuild the check boxes for the given categories, showing their counts and keeping
        previous ticks.
        """
        previous = {k: b.isChecked() for k, b in self.boxes.items()}
        for b in self.boxes.values():
            b.setParent(None)
            b.deleteLater()
        self.boxes = {}
        for i, c in enumerate(cats):
            b = QCheckBox(f"{c} ({counts.get(c, 0)})")
            b.setChecked(previous.get(c, True))
            b.toggled.connect(self.changed)
            self._grid.addWidget(b, i // 2, i % 2)
            self.boxes[c] = b

    def checked(self) -> List[str]:
        """Categories currently ticked."""
        return [c for c, b in self.boxes.items() if b.isChecked()]

    def all_checked(self) -> bool:
        """True when every category is ticked (the filter then excludes nothing)."""
        return all(b.isChecked() for b in self.boxes.values())

    def set_all(self, state: bool = True) -> None:
        """Tick or untick every box without triggering one search per box."""
        for b in self.boxes.values():
            b.blockSignals(True)
            b.setChecked(state)
            b.blockSignals(False)


# === Advanced condition row ==================================================
class ConditionRow(QWidget):
    """One line of the advanced search: field, operator and value, plus a remove button."""
    changed = Signal()
    removeRequested = Signal(object)

    def __init__(self, db: Database, cond: Optional[Condition] = None, parent=None):
        """Build the field, operator and value widgets (optionally prefilled from cond)."""
        super().__init__(parent)
        self.db = db
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        top = QHBoxLayout()
        self.field = QComboBox()
        self.field.setMinimumContentsLength(18)
        self.field.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        last_group = None
        for cid, spec in db.fields.items():
            if cid in ("info:file", "info:image") or cid.endswith(":login") or cid.endswith(":image_name"):
                continue
            if spec.group != last_group:
                if last_group is not None:
                    self.field.insertSeparator(self.field.count())
                self.field.addItem(f"[ {spec.group} ]")
                self.field.model().item(self.field.count() - 1).setEnabled(False)
                last_group = spec.group
            self.field.addItem(spec.label, cid)
        rm = QToolButton()
        rm.setText("✕")
        rm.setToolTip("Remove this condition")
        rm.clicked.connect(lambda: self.removeRequested.emit(self))
        top.addWidget(self.field, 1)
        top.addWidget(rm)
        bottom = QHBoxLayout()
        self.op = QComboBox()
        bottom.addWidget(self.op)
        self.value_holder = QHBoxLayout()
        bottom.addLayout(self.value_holder, 1)
        lay.addLayout(top)
        lay.addLayout(bottom)
        self.value_widget: Optional[QWidget] = None

        self.field.currentIndexChanged.connect(self._field_changed)
        self.op.currentIndexChanged.connect(self._op_changed)
        if cond is not None and self.field.findData(cond.column) >= 0:
            self.field.setCurrentIndex(self.field.findData(cond.column))
            self._field_changed()
            if self.op.findText(cond.op) >= 0:
                self.op.setCurrentText(cond.op)
            self._set_value(cond.value)
        else:
            first = next(i for i in range(self.field.count()) if self.field.itemData(i))
            self.field.setCurrentIndex(first)
            self._field_changed()

    def _spec(self):
        """FieldSpec of the selected field."""
        return self.db.fields.get(self.field.currentData())

    def _field_changed(self) -> None:
        """Adapt the operators and the value widget to the type of the selected field."""
        spec = self._spec()
        if spec is None:
            return
        self.op.blockSignals(True)
        self.op.clear()
        self.op.addItems(OPERATORS[spec.kind])
        self.op.blockSignals(False)
        if self.value_widget is not None:
            self.value_widget.setParent(None)
            self.value_widget.deleteLater()
        if spec.kind == "numeric":
            w = QDoubleSpinBox()
            s = pd.to_numeric(self.db.df[spec.column].map(_clean), errors="coerce").dropna()
            integer = len(s) == 0 or bool((s % 1 == 0).all())
            w.setDecimals(0 if integer else 3)
            w.setRange(-1e9, 1e9)
            w.setValue(1 if integer else 0.5)
            w.valueChanged.connect(self.changed)
        elif spec.kind in ("ordinal", "categorical"):
            w = QComboBox()
            w.addItems(self.db.categories(spec.column))
            w.currentIndexChanged.connect(self.changed)
        else:
            w = QLineEdit()
            w.setPlaceholderText("text…")
            w.textChanged.connect(self.changed)
        self.value_widget = w
        self.value_holder.addWidget(w)
        self._op_changed()

    def _op_changed(self) -> None:
        """Hide the value widget for operators that need none ('is empty')."""
        if self.value_widget is not None:
            self.value_widget.setVisible(self.op.currentText() not in NO_VALUE_OPS)
        self.changed.emit()

    def _set_value(self, value) -> None:
        """Put a saved value back into the value widget (used when loading a query)."""
        w = self.value_widget
        if isinstance(w, QDoubleSpinBox):
            try:
                w.setValue(float(value))
            except (TypeError, ValueError):
                pass
        elif isinstance(w, QComboBox):
            i = w.findText(str(value))
            if i < 0:
                w.addItem(str(value))
                i = w.count() - 1
            w.setCurrentIndex(i)
        elif isinstance(w, QLineEdit):
            w.setText("" if value is None else str(value))

    def condition(self) -> Optional[Condition]:
        """The condition described by this row, or None if incomplete."""
        spec = self._spec()
        if spec is None:
            return None
        op = self.op.currentText()
        w = self.value_widget
        if op in NO_VALUE_OPS:
            value = None
        elif isinstance(w, QDoubleSpinBox):
            value = w.value()
            if w.decimals() == 0:
                value = int(value)
        elif isinstance(w, QComboBox):
            value = w.currentText()
            if value == "":
                return None
        else:
            value = w.text()
        return Condition(spec.column, op, value)


# === Whole filter panel ======================================================
class FilterPanel(QScrollArea):
    """Left-hand search panel; emits ``changed`` (debounced) when any filter changes."""
    changed = Signal()

    def __init__(self, parent=None):
        """Build the left panel: text search, quick filters, slide list and advanced conditions."""
        super().__init__(parent)
        self.db: Optional[Database] = None
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(330)
        body = QWidget()
        self.setWidget(body)
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(8, 8, 8, 8)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.changed)

        # --- text search
        box = QGroupBox("Search")
        bl = QVBoxLayout(box)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Image name, comment, morphological description…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._kick)
        bl.addWidget(self.search)
        self.lay.addWidget(box)

        # --- quick filters
        self.g_group = CheckGroup("Clinical group (per slide)", "info:clinical_group")
        self.g_major = CheckGroup("Majority diagnosis (pathologists)", "consensus:majority")
        self.g_plexus = CheckGroup("Plexus (DH)", "DH:plexus")
        for g in (self.g_group, self.g_major, self.g_plexus):
            g.changed.connect(self._kick)
            self.lay.addWidget(g)

        box = QGroupBox("Parkinson calls")
        hl = QHBoxLayout(box)
        hl.addWidget(QLabel("At least"))
        self.min_pd = QSpinBox()
        self.min_pd.setRange(0, 4)
        self.min_pd.valueChanged.connect(self._kick)
        hl.addWidget(self.min_pd)
        self.min_pd_label = QLabel("of 4 pathologists")
        hl.addWidget(self.min_pd_label)
        hl.addStretch(1)
        self.lay.addWidget(box)

        box = QGroupBox("Slides")
        vl = QVBoxLayout(box)
        self.slides = QListWidget()
        self.slides.setMaximumHeight(170)
        self.slides.itemChanged.connect(self._kick)
        vl.addWidget(self.slides)
        hl = QHBoxLayout()
        for text, state in (("All", True), ("None", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: self._set_slides(s))
            hl.addWidget(b)
        hl.addStretch(1)
        vl.addLayout(hl)
        self.lay.addWidget(box)

        # --- condition builder
        box = QGroupBox("Advanced conditions")
        self.cond_layout = QVBoxLayout(box)
        self.rows_layout = QVBoxLayout()
        self.cond_layout.addLayout(self.rows_layout)
        hl = QHBoxLayout()
        add = QPushButton("+ Add condition")
        add.clicked.connect(lambda: self.add_condition())
        hl.addWidget(add)
        hl.addStretch(1)
        self.mode_all = QRadioButton("match all")
        self.mode_any = QRadioButton("match any")
        self.mode_all.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.mode_all)
        grp.addButton(self.mode_any)
        self.mode_all.toggled.connect(self._kick)
        hl.addWidget(self.mode_all)
        hl.addWidget(self.mode_any)
        self.cond_layout.addLayout(hl)
        hl = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItem("Example queries…")
        self.preset.addItems(list(PRESETS))
        self.preset.activated.connect(self._apply_preset)
        hl.addWidget(self.preset, 1)
        self.cond_layout.addLayout(hl)
        hl = QHBoxLayout()
        b = QPushButton("Save query…")
        b.clicked.connect(self._save_query)
        hl.addWidget(b)
        b = QPushButton("Load query…")
        b.clicked.connect(self._load_query)
        hl.addWidget(b)
        self.cond_layout.addLayout(hl)
        self.lay.addWidget(box)
        self.rows: List[ConditionRow] = []

        reset = QPushButton("Reset all filters")
        reset.clicked.connect(self.reset)
        self.lay.addWidget(reset)
        self.lay.addStretch(1)

    # ---------------------------------------------------------------- setup
    def set_database(self, db: Database) -> None:
        """(Re)populate the widgets, keeping the user's current choices."""
        old_conditions = [r.condition() for r in self.rows]
        self.db = db
        df = db.df
        for g in (self.g_group, self.g_major, self.g_plexus):
            if g.column not in df.columns:
                g.setVisible(False)
                continue
            vals = df[g.column].map(_clean).map(lambda v: "(not rated)" if v is None else str(v))
            counts = vals.value_counts().to_dict()
            cats = list(counts)
            if g is self.g_group:
                cats = [c for c in CLINICAL_GROUPS if c in counts] + \
                       [c for c in counts if c not in CLINICAL_GROUPS]
            else:
                cats = sorted(cats, key=lambda c: (c == "(not rated)", -counts[c]))
            g.set_categories(cats, counts)
        n = len(db.raters)
        self.min_pd.setMaximum(n)
        self.min_pd_label.setText(f"of {n} pathologists")

        previous = {self.slides.item(i).data(Qt.UserRole): self.slides.item(i).checkState()
                    for i in range(self.slides.count())}
        self.slides.blockSignals(True)
        self.slides.clear()
        counts = df["info:slide"].value_counts().to_dict()
        for s in db.slides():
            it = QListWidgetItem(f"{s}  ({counts.get(s, 0)})")
            it.setData(Qt.UserRole, s)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(previous.get(s, Qt.Checked))
            self.slides.addItem(it)
        self.slides.blockSignals(False)

        for r in list(self.rows):
            self._remove_row(r, emit=False)
        for c in old_conditions:
            if c is not None and c.column in db.fields:
                self.add_condition(c, emit=False)

    def _set_slides(self, state: bool) -> None:
        """Tick (All) or untick (None) every slide."""
        self.slides.blockSignals(True)
        for i in range(self.slides.count()):
            self.slides.item(i).setCheckState(Qt.Checked if state else Qt.Unchecked)
        self.slides.blockSignals(False)
        self._kick()

    def add_condition(self, cond: Optional[Condition] = None, emit: bool = True) -> None:
        """Add a condition row (empty, or prefilled from cond)."""
        if self.db is None:
            return
        row = ConditionRow(self.db, cond)
        row.changed.connect(self._kick)
        row.removeRequested.connect(self._remove_row)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        if emit:
            self._kick()

    def _remove_row(self, row: ConditionRow, emit: bool = True) -> None:
        """Remove one condition row and update the results."""
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        if emit:
            self._kick()

    def _apply_preset(self, index: int) -> None:
        """Replace the conditions by one of the example queries."""
        name = self.preset.itemText(index)
        if name not in PRESETS:
            return
        conds, mode = PRESETS[name]
        for r in list(self.rows):
            self._remove_row(r, emit=False)
        for c in conds:
            if c.column in self.db.fields:
                self.add_condition(c, emit=False)
        (self.mode_all if mode == "all" else self.mode_any).setChecked(True)
        self.preset.setCurrentIndex(0)
        self._kick()

    def _save_query(self) -> None:
        """Save the current conditions to a JSON file."""
        path, _ = QFileDialog.getSaveFileName(self, "Save query", "query.json", "Query (*.json)")
        if path:
            save_query(path, self.conditions(), self.mode())

    def _load_query(self) -> None:
        """Load conditions from a JSON file saved earlier."""
        path, _ = QFileDialog.getOpenFileName(self, "Load query", "", "Query (*.json)")
        if not path:
            return
        try:
            conds, mode = load_query(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Load query", f"Could not read the query file:\n{exc}")
            return
        for r in list(self.rows):
            self._remove_row(r, emit=False)
        for c in conds:
            if c.column in self.db.fields:
                self.add_condition(c, emit=False)
        (self.mode_all if mode == "all" else self.mode_any).setChecked(True)
        self._kick()

    def reset(self) -> None:
        """Clear every filter: all categories and slides ticked, no text, no condition."""
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        for g in (self.g_group, self.g_major, self.g_plexus):
            g.set_all(True)
        self.min_pd.blockSignals(True)
        self.min_pd.setValue(0)
        self.min_pd.blockSignals(False)
        for r in list(self.rows):
            self._remove_row(r, emit=False)
        self._set_slides(True)

    def _kick(self, *args) -> None:
        """Restart the short timer so that the search runs once after the user stops typing or
        clicking.
        """
        self._timer.start()

    # -------------------------------------------------------------- result
    def conditions(self) -> List[Condition]:
        """Complete conditions of the advanced search."""
        return [c for c in (r.condition() for r in self.rows) if c is not None]

    def mode(self) -> str:
        """'all' (AND) or 'any' (OR) for the advanced conditions."""
        return "all" if self.mode_all.isChecked() else "any"

    def mask(self) -> pd.Series:
        """Boolean mask of the images that pass every filter of the panel."""
        df = self.db.df
        m = pd.Series(True, index=df.index)
        text = self.search.text().strip().lower()
        if text:
            cols = [c for c, s in self.db.fields.items()
                    if s.kind == "text" or c in ("info:image", "info:slide")]
            hay = df[cols].apply(lambda col: col.map(
                lambda v: "" if _clean(v) is None else str(_clean(v)).lower())).agg(" | ".join, axis=1)
            m &= hay.str.contains(text, regex=False)
        for g in (self.g_group, self.g_major, self.g_plexus):
            if g.column in df.columns and g.boxes and not g.all_checked():
                vals = df[g.column].map(_clean).map(lambda v: "(not rated)" if v is None else str(v))
                m &= vals.isin(g.checked())
        if self.min_pd.value() > 0 and "consensus:n_parkinson" in df:
            m &= df["consensus:n_parkinson"] >= self.min_pd.value()
        chosen = {self.slides.item(i).data(Qt.UserRole) for i in range(self.slides.count())
                  if self.slides.item(i).checkState() == Qt.Checked}
        if self.slides.count() and len(chosen) < self.slides.count():
            m &= df["info:slide"].isin(chosen)
        conds = self.conditions()
        if conds:
            m &= apply_query(df, self.db.fields, conds, self.mode())
        return m

    def summary(self) -> str:
        """Text description of the active filters (written in the export)."""
        parts = []
        if self.search.text().strip():
            parts.append(f'text contains "{self.search.text().strip()}"')
        for g in (self.g_group, self.g_major, self.g_plexus):
            if g.boxes and not g.all_checked():
                parts.append(f"{g.title()} in [{', '.join(g.checked())}]")
        if self.min_pd.value() > 0:
            parts.append(f"Parkinson calls >= {self.min_pd.value()}")
        n_checked = sum(self.slides.item(i).checkState() == Qt.Checked for i in range(self.slides.count()))
        if n_checked < self.slides.count():
            chosen = [self.slides.item(i).data(Qt.UserRole) for i in range(self.slides.count())
                      if self.slides.item(i).checkState() == Qt.Checked]
            parts.append(f"Slides in [{', '.join(chosen)}]")
        conds = self.conditions()
        if conds:
            parts.append("(" + describe(conds, self.db.fields, self.mode()) + ")")
        return " AND ".join(parts) if parts else "All images"
