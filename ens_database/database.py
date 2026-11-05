"""In-memory image database built from the workbook and the image folder.

Every image becomes one row of a pandas DataFrame indexed by its canonical key.
Column identifiers follow the pattern ``<source>:<field>``:

    info:*        identity (file, slide, clinical group, spectral data)
    DH:*          morphology sheet fields (raw values)
    PN:*, FC:* .. pathologist sheet fields (raw values)
    derived:*     quantities computed from the DH counts
    consensus:*   agreement between pathologists

Each column is described by a :class:`FieldSpec` that drives the user
interface (labels, filter widgets, editability).
"""
from __future__ import annotations

import csv
import os
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import imagefiles, xlsx_patch
from .excel_io import SheetData, WorkbookData, read_workbook
from .naming import slide_id, spectral_key, strip_prefix

# Ordinal scale used for granule load and infiltrating cells.
GRADE_ORDER = ["none", "+", "++", "+++"]
GRADE_VALUE = {g: i for i, g in enumerate(GRADE_ORDER)}

CLINICAL_GROUPS = ["Parkinson", "Alzheimer", "Control", "Other", "Unassigned"]

# Clinical group of each slide, as defined by the lab (D. Hazart, November 2026):
# slide codes BK11.. = Alzheimer group, BK12.. = Parkinson group, any other slide = control.
_GROUP_RULE = (("BK11", "Alzheimer"), ("BK12", "Parkinson"))


# Labels of the 'True Diagnosis' values of the workbook (summary sheet).  The two
# control categories are the aged and young controls of the cohort.
DIAGNOSIS_LABELS = {"healthy": "Control (aged)", "young": "Control (young)",
                    "parkinson": "Parkinson", "alzheimer": "Alzheimer"}
# Clinical group implied by each detailed diagnosis (used for the cross-check).
_DIAGNOSIS_GROUP = {"Control (aged)": "Control", "Control (young)": "Control",
                    "Parkinson": "Parkinson", "Alzheimer": "Alzheimer"}
# Pathologist call counted as correct for each clinical group.  Same convention
# as the lab's 'Nombre de réponses exactes sur 4' (correct answers out of 4): for Parkinson AND Alzheimer
# patients the pathological call ('Parkinson') is correct; for controls, 'Healthy'.
_CORRECT_CALL = {"Parkinson": "Parkinson", "Alzheimer": "Parkinson", "Control": "Healthy"}


def rule_group(slide: str) -> str:
    """Clinical group of a slide according to the lab's coding rule."""
    if not slide or slide == "Unknown":
        return "Unassigned"
    for prefix, group in _GROUP_RULE:
        if slide.upper().startswith(prefix):
            return group
    return "Control"

# Human-readable labels for the DH morphology fields.
_DH_LABELS = {
    "plexus": "Plexus (DH)",
    "empty_plexus": "Empty plexus",
    "small_nerve_cell_without_granules_with_nucleoli_nb": "Small neurons, no granules, nucleoli+ (n)",
    "small_nerve_cell_without_granules_without_nucleoli_nb": "Small neurons, no granules, nucleoli- (n)",
    "small_nerve_cell_with_granules_with_nucleoli_nb": "Small neurons, granules, nucleoli+ (n)",
    "small_nerve_cell_with_granules_with_nucleoli_granules": "Small neurons, granules, nucleoli+ (grade)",
    "small_nerve_cell_with_granules_without_nucleoli_nb": "Small neurons, granules, nucleoli- (n)",
    "small_nerve_cell_with_granules_without_nucleoli_granules": "Small neurons, granules, nucleoli- (grade)",
    "large_nerve_cell_without_granules_with_nucleoli_nb": "Large neurons, no granules, nucleoli+ (n)",
    "large_nerve_cell_without_granules_without_nucleoli_nb": "Large neurons, no granules, nucleoli- (n)",
    "large_nerve_cell_with_granules_with_nucleoli_nb": "Large neurons, granules, nucleoli+ (n)",
    "large_nerve_cell_with_granules_with_nucleoli_granules": "Large neurons, granules, nucleoli+ (grade)",
    "large_nerve_cell_with_granules_without_nucleoli_nb": "Large neurons, granules, nucleoli- (n)",
    "large_nerve_cell_with_granules_without_nucleoli_granules": "Large neurons, granules, nucleoli- (grade)",
    "infiltrating_cell_nb": "Infiltrating cells (grade)",
    "infiltrating_cell_in_env_tissue": "Infiltrating cells in surrounding tissue (grade)",
    "comments": "Comments (DH)",
    "nice_picture": "Picture quality (DH)",
    "login": "Login",
    "image_name": "Image name (Excel)",
}
_RATER_LABELS = {
    "plexus": "Plexus",
    "diagnosis": "Diagnosis",
    "certainty": "Certainty",
    "morphology": "Morphological features",
    "comments": "Comments",
    "login": "Login",
    "image_name": "Image name (Excel)",
}

# Count columns of the DH sheet, organised by (size, granules, nucleoli).
_SIZES = ("small", "large")
_COUNT_FIELDS = {
    (size, gran, nucl): f"{size}_nerve_cell_{gran}_granules_{nucl}_nucleoli_nb"
    for size in _SIZES for gran in ("without", "with") for nucl in ("with", "without")
}
_GRADE_FIELDS = {
    (size, nucl): f"{size}_nerve_cell_with_granules_{nucl}_nucleoli_granules"
    for size in _SIZES for nucl in ("with", "without")
}
# Ordered quality scale of the 'nice_picture' / 'certainty' answers.
QUALITY_ORDER = ["No", "Maybe", "NearlySure", "Sure"]


@dataclass
class FieldSpec:
    """Description of one column of the joined table: label, data type and origin."""
    column: str                 # DataFrame column id
    label: str                  # label shown in the interface
    kind: str                   # 'numeric' | 'ordinal' | 'categorical' | 'text'
    group: str                  # section name in the interface
    source_sheet: Optional[str] = None   # workbook sheet (None for computed fields)
    source_field: Optional[str] = None   # canonical field in that sheet
    editable: bool = False
    order: Optional[List[str]] = None    # ordinal levels


@dataclass
class Edit:
    """One value to write back into the workbook (column, image key, new value)."""
    column: str
    key: str
    value: object


class Database:
    """Joined view of the workbook, the image files and the spectral stacks."""

    def __init__(self, excel_path: str, image_root: str, spectral_root: str = "",
                 groups_path: str = ""):
        """Store the paths; nothing is read until load() is called."""
        self.excel_path = excel_path
        self.image_root = image_root
        self.spectral_root = spectral_root
        self.groups_path = groups_path or default_groups_path(excel_path)
        self.df = pd.DataFrame()
        self.fields: Dict[str, FieldSpec] = {}
        self.sheets: List[SheetData] = []
        self.morph: Optional[SheetData] = None
        self.raters: List[SheetData] = []
        self.issues: List[Tuple[str, str]] = []    # (category, message)
        self.orphan_files: List[str] = []
        self.slide_groups: Dict[str, str] = {}     # manual overrides of rule_group()
        self.slide_info: Dict[str, Tuple[Optional[str], Optional[str]]] = {}

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        """Read the workbook and the image folders, then build the joined table (one row per image)
        with derived fields, consensus and integrity issues.
        """
        self.issues = []
        wbd: WorkbookData = read_workbook(self.excel_path)
        self.sheets = wbd.sheets
        for reason in wbd.skipped:
            self.issues.append(("Sheet ignored", reason))
        self.slide_info = wbd.slide_info
        for msg in wbd.slide_info_conflicts:
            self.issues.append(("Diagnosis / patient conflict", msg))

        morph = [s for s in self.sheets if s.role == "morphology"]
        if not morph:
            raise ValueError("No morphology sheet (with an 'empty_plexus' column) was found.")
        self.morph = morph[0]
        for extra in morph[1:]:
            self.issues.append(("Sheet ignored",
                                f"'{extra.name}': second morphology sheet; '{self.morph.name}' is used."))
            self.sheets.remove(extra)
        self.raters = [s for s in self.sheets if s.role == "rater"]

        files = imagefiles.scan(self.image_root)
        spectra = scan_spectral(self.spectral_root) if self.spectral_root else {}
        self.slide_groups = load_groups(self.groups_path)

        keys = list(self.morph.records.keys())
        file_of = _match_keys(keys, files)
        self.orphan_files = sorted(p for k, p in files.items() if k not in set(file_of.values_keys))

        rows = []
        for key in keys:
            rec: Dict[str, object] = {"key": key}
            path = file_of.get(key)
            sl = slide_id(key)
            rec["info:image"] = imagefiles.display_name(path) if path else \
                os.path.basename(str(self.morph.records[key].get("image_name")))
            rec["info:file"] = path or ""
            rec["info:slide"] = sl
            rec["info:clinical_group"] = self.group_of(sl)
            dx, patient = self.slide_info.get(sl, (None, None))
            rec["info:diagnosis"] = DIAGNOSIS_LABELS.get(str(dx).lower(), dx) if dx else None
            rec["info:patient"] = patient
            spec_paths = spectra.get(key) or spectra.get(strip_prefix(key)) or []
            rec["info:spectral_files"] = "; ".join(spec_paths)
            rec["info:has_spectral"] = "Yes" if spec_paths else "No"
            for sheet in self.sheets:
                values = sheet.records.get(key)
                for fld in sheet.columns:
                    if fld in sheet.formula_fields:
                        continue
                    rec[f"{sheet.code}:{fld}"] = None if values is None else values.get(fld)
            rows.append(rec)
        df = pd.DataFrame(rows).set_index("key", drop=False)
        self.df = df
        self._build_fields()
        self._compute_derived()
        self._compute_consensus()
        self._collect_issues(keys, file_of, files, spectra)

    # --------------------------------------------------------------- fields
    def _build_fields(self) -> None:
        """Create the FieldSpec of every column shown in the interface (identification, sheet
        fields, derived values).
        """
        f: Dict[str, FieldSpec] = {}
        f["info:image"] = FieldSpec("info:image", "Image", "text", "Identification")
        f["info:slide"] = FieldSpec("info:slide", "Slide", "categorical", "Identification")
        f["info:clinical_group"] = FieldSpec("info:clinical_group", "Clinical group", "categorical",
                                             "Identification")
        if self.slide_info:
            f["info:diagnosis"] = FieldSpec("info:diagnosis", "Diagnosis (detailed)", "categorical",
                                            "Identification")
            f["info:patient"] = FieldSpec("info:patient", "Patient", "categorical", "Identification")
        f["info:has_spectral"] = FieldSpec("info:has_spectral", "Spectral stack available",
                                           "categorical", "Identification")
        f["info:file"] = FieldSpec("info:file", "File path", "text", "Identification")
        for sheet in self.sheets:
            for fld, col in sheet.columns.items():
                if fld in sheet.formula_fields:
                    continue
                cid = f"{sheet.code}:{fld}"
                if sheet.role == "morphology":
                    label = _DH_LABELS.get(fld, sheet.headers.get(fld, fld))
                    group = "Morphology (DH)"
                else:
                    label = f"{sheet.code}: {_RATER_LABELS.get(fld, sheet.headers.get(fld, fld))}"
                    group = f"Pathologist {sheet.code}"
                kind = self._infer_kind(cid, fld)
                order = GRADE_ORDER if kind == "ordinal" and fld not in ("nice_picture", "certainty") \
                    else (QUALITY_ORDER if kind == "ordinal" else None)
                f[cid] = FieldSpec(cid, label, kind, group, source_sheet=sheet.name,
                                   source_field=fld,
                                   editable=fld not in ("image_name", "login"),
                                   order=order)
        self.fields = f

    def _infer_kind(self, cid: str, fld: str) -> str:
        """Guess the data type of a sheet column (numeric, ordinal grade, categorical or free
        text).
        """
        s = self.df[cid].dropna()
        s = s[s.astype(str) != "-"]
        if fld.endswith("_granules") or fld.startswith("infiltrating_cell"):
            return "ordinal"
        if fld in ("nice_picture", "certainty"):
            return "ordinal"
        if fld in ("comments", "morphology", "image_name"):
            return "text"
        if len(s) and pd.to_numeric(s, errors="coerce").notna().all():
            return "numeric"
        if s.astype(str).nunique() <= 25:
            return "categorical"
        return "text"

    # -------------------------------------------------------------- derived
    def _num(self, fld: str) -> Optional[pd.Series]:
        """Return a morphology count column as numbers (None if the column is absent)."""
        cid = f"{self.morph.code}:{fld}"
        if cid not in self.df:
            return None
        return pd.to_numeric(self.df[cid], errors="coerce")

    def _grade(self, fld: str) -> Optional[pd.Series]:
        """Return a granule grade column as numbers (none = 0, + = 1, ++ = 2, +++ = 3)."""
        cid = f"{self.morph.code}:{fld}"
        if cid not in self.df:
            return None
        return self.df[cid].map(lambda v: GRADE_VALUE.get(str(v).strip().lower(), np.nan)
                                if v is not None else np.nan)

    def _add_derived(self, name: str, label: str, values: pd.Series, kind: str = "numeric",
                     order: Optional[List[str]] = None) -> None:
        """Add a computed column to the table and register its FieldSpec."""
        cid = f"derived:{name}"
        self.df[cid] = values
        self.fields[cid] = FieldSpec(cid, label, kind, "Derived morphometry", order=order)

    def _compute_derived(self) -> None:
        """Recompute the neuron totals (small, large, with or without granules, with nucleoli) and
        the maximal granule grade from the raw counts.
        """
        counts = {k: self._num(v) for k, v in _COUNT_FIELDS.items()}
        missing = [v for k, v in _COUNT_FIELDS.items() if counts[k] is None]
        if missing:
            self.issues.append(("Derived fields",
                                "Columns missing in the morphology sheet, some totals are not "
                                "computed: " + ", ".join(missing)))

        def total(pred) -> Optional[pd.Series]:
            """Sum the count columns whose (size, granules, nucleoli) category matches pred."""
            parts = [s for k, s in counts.items() if pred(*k)]
            if any(p is None for p in parts) or not parts:
                return None
            return sum(p.fillna(0) for p in parts)

        spec = [
            ("total_neurons", "Total neurons (n)", lambda s, g, n: True),
            ("total_small", "Small neurons, total (n)", lambda s, g, n: s == "small"),
            ("total_large", "Large neurons, total (n)", lambda s, g, n: s == "large"),
            ("large_with_granules", "Large neurons with granules (n)",
             lambda s, g, n: s == "large" and g == "with"),
            ("large_without_granules", "Large neurons without granules (n)",
             lambda s, g, n: s == "large" and g == "without"),
            ("large_with_nucleoli", "Large neurons with nucleoli (n)",
             lambda s, g, n: s == "large" and n == "with"),
            ("large_with_granules_and_nucleoli", "Large neurons with granules AND nucleolus (n)",
             lambda s, g, n: s == "large" and g == "with" and n == "with"),
            ("small_with_granules", "Small neurons with granules (n)",
             lambda s, g, n: s == "small" and g == "with"),
            ("small_without_granules", "Small neurons without granules (n)",
             lambda s, g, n: s == "small" and g == "without"),
            ("small_with_nucleoli", "Small neurons with nucleoli (n)",
             lambda s, g, n: s == "small" and n == "with"),
            ("with_granules", "Neurons with granules, all sizes (n)", lambda s, g, n: g == "with"),
        ]
        for name, label, pred in spec:
            t = total(pred)
            if t is not None:
                self._add_derived(name, label, t)
        tl, tn = self.df.get("derived:total_large"), self.df.get("derived:total_neurons")
        if tl is not None and tn is not None:
            frac = np.where(tn > 0, tl / tn.replace(0, np.nan), np.nan)
            self._add_derived("fraction_large", "Fraction of large neurons (0-1)",
                              pd.Series(frac, index=self.df.index).round(3))

        for size in _SIZES:
            grades = [self._grade(_GRADE_FIELDS[(size, n)]) for n in ("with", "without")]
            if all(g is not None for g in grades):
                mx = pd.concat(grades, axis=1).max(axis=1, skipna=True)
                labels = mx.map(lambda v: GRADE_ORDER[int(v)] if not pd.isna(v) else None)
                self._add_derived(f"max_granule_grade_{size}",
                                  f"Granule load, {size} neurons (max grade)",
                                  labels, kind="ordinal", order=GRADE_ORDER)

    # ------------------------------------------------------------ consensus
    def _compute_consensus(self) -> None:
        """Count the pathologists' calls per image (Parkinson, Healthy, other) and derive the
        majority diagnosis.
        """
        if not self.raters:
            return
        diag = pd.DataFrame({r.code: self.df.get(f"{r.code}:diagnosis") for r in self.raters})
        diag = diag.apply(lambda col: col.map(lambda v: _clean(v)))
        n_rated = diag.notna().sum(axis=1)
        n_pd = (diag == "Parkinson").sum(axis=1)
        n_h = (diag == "Healthy").sum(axis=1)
        n_o = (diag == "OtherPathology").sum(axis=1)

        def majority(row) -> Optional[str]:
            """Most frequent diagnosis of one image, or 'Tie' when two diagnoses have the same
            count.
            """
            vals = [v for v in row if v is not None and not (isinstance(v, float) and np.isnan(v))]
            if not vals:
                return None
            c = Counter(vals).most_common()
            if len(c) > 1 and c[0][1] == c[1][1]:
                return "Tie"
            return c[0][0]

        maj = diag.apply(majority, axis=1)
        self._diag = diag
        add = [
            ("n_raters", f"Pathologists who rated the image (n/{len(self.raters)})", n_rated, "numeric"),
            ("n_parkinson", f"Pathologists calling Parkinson (n/{len(self.raters)})", n_pd, "numeric"),
            ("n_healthy", f"Pathologists calling Healthy (n/{len(self.raters)})", n_h, "numeric"),
            ("n_other", f"Pathologists calling Other pathology (n/{len(self.raters)})", n_o, "numeric"),
            ("majority", "Majority pathologist diagnosis", maj, "categorical"),
        ]
        for name, label, series, kind in add:
            cid = f"consensus:{name}"
            self.df[cid] = series
            self.fields[cid] = FieldSpec(cid, label, kind, "Pathologist consensus")
        self.fields["consensus:n_correct"] = FieldSpec(
            "consensus:n_correct",
            f"Pathologists matching the clinical group (n/{len(self.raters)})", "numeric",
            "Pathologist consensus")
        self._compute_correct()

    def _compute_correct(self) -> None:
        """Calls matching the clinical group ('Parkinson' for PD and AD, 'Healthy' for controls)."""
        if not self.raters:
            return
        expected = self.df["info:clinical_group"].map(_CORRECT_CALL)
        n = sum((self._diag[c] == expected).astype(int) for c in self._diag.columns)
        self.df["consensus:n_correct"] = n.where(expected.notna())

    # --------------------------------------------------------------- issues
    def _collect_issues(self, keys, file_of, files, spectra) -> None:
        """List data problems for the integrity report: missing files, unmatched images, unrated
        images, duplicates.
        """
        missing = [k for k in keys if k not in file_of]
        for k in missing:
            self.issues.append(("Image file missing",
                                f"{self.morph.records[k].get('image_name')}: no file found in the image folder."))
        for p in self.orphan_files:
            self.issues.append(("Image without Excel row (excluded)",
                                f"{imagefiles.display_name(p)} is in the image folder but not in "
                                f"'{self.morph.name}': no annotation, not shown."))
        keyset = set(keys)
        for r in self.raters:
            not_rated = [k for k in keys if k not in r.records
                         or _clean(r.records[k].get("diagnosis")) is None]
            for k in not_rated:
                self.issues.append((f"Not rated by {r.code}",
                                    f"{self.morph.records[k].get('image_name')}"))
            for k in r.records:
                if k not in keyset:
                    self.issues.append((f"Unknown image in {r.code}",
                                        f"{r.records[k].get('image_name')} (row {r.row_of[k]}) is not "
                                        f"in '{self.morph.name}'."))
        for s in self.sheets:
            for k in s.duplicates:
                self.issues.append(("Duplicate row",
                                    f"'{s.name}': {k} appears more than once; the first row is used."))
        for k in keys:
            if slide_id(k) == "Unknown":
                self.issues.append(("Slide not recognised", f"{k}"))
        if self.slide_info:
            for sl in sorted(set(self.df["info:slide"])):
                if sl not in self.slide_info:
                    self.issues.append(("Diagnosis / patient missing",
                                        f"Slide {sl}: no 'True Diagnosis' in the workbook."))
                    continue
                dx = DIAGNOSIS_LABELS.get(str(self.slide_info[sl][0]).lower())
                if dx and _DIAGNOSIS_GROUP[dx] != self.group_of(sl):
                    self.issues.append(("Diagnosis / group mismatch",
                                        f"Slide {sl}: workbook diagnosis '{dx}' but clinical group "
                                        f"'{self.group_of(sl)}'."))
        used_spec = set()
        for k in keys:
            if k in spectra:
                used_spec.add(k)
            elif strip_prefix(k) in spectra:
                used_spec.add(strip_prefix(k))
        for sk, paths in spectra.items():
            if sk not in used_spec:
                for p in paths:
                    self.issues.append(("Spectral stack without database image", os.path.basename(p)))

    # ------------------------------------------------------------- queries
    def keys(self) -> List[str]:
        """Image keys (normalised image names) in table order."""
        return list(self.df.index)

    def value(self, key: str, column: str):
        """Cleaned value of one cell of the joined table (None when empty)."""
        if column not in self.df.columns:
            return None
        return _clean(self.df.at[key, column])

    def categories(self, column: str) -> List[str]:
        """Observed values of a categorical/ordinal column, in a sensible order."""
        spec = self.fields.get(column)
        vals = [_clean(v) for v in self.df[column].tolist()] if column in self.df else []
        present = sorted({str(v) for v in vals if v is not None})
        if spec and spec.order:
            ordered = [o for o in spec.order if o in present]
            return ordered + [p for p in present if p not in ordered]
        return present

    # --------------------------------------------------------------- edits
    def cell_of(self, column: str, key: str) -> Tuple[str, int, int]:
        """(sheet name, row, column) of the workbook cell behind a field."""
        spec = self.fields[column]
        if not spec.editable or spec.source_sheet is None:
            raise ValueError(f"'{spec.label}' is not stored in the workbook.")
        sheet = next(s for s in self.sheets if s.name == spec.source_sheet)
        if key not in sheet.row_of:
            raise KeyError(f"{key} has no row in sheet '{sheet.name}'. Add the row in Excel first.")
        return sheet.name, sheet.row_of[key], sheet.columns[spec.source_field]

    def write_edits(self, edits: Sequence[Edit]) -> Optional[str]:
        """Write edited values into the workbook (backup first), then reload."""
        grouped: Dict[str, List[Tuple[int, int, object]]] = {}
        for e in edits:
            sheet, row, col = self.cell_of(e.column, e.key)
            grouped.setdefault(sheet, []).append((row, col, e.value))
        backup = xlsx_patch.write_cells(self.excel_path, grouped)
        self.load()
        return backup

    # -------------------------------------------------------- clinical groups
    def group_of(self, slide: str) -> str:
        """Clinical group of a slide: manual override if any, else the lab's rule."""
        return self.slide_groups.get(slide) or rule_group(slide)

    def set_slide_groups(self, mapping: Dict[str, str]) -> None:
        """Store the groups chosen in the dialog; only departures from the rule are saved."""
        overrides = {s: g for s, g in mapping.items()
                     if g and g != "Unassigned" and g != rule_group(s)}
        if overrides or os.path.isfile(self.groups_path):
            save_groups(self.groups_path, overrides)
        self.slide_groups = overrides
        self.df["info:clinical_group"] = self.df["info:slide"].map(self.group_of)
        self._compute_correct()

    def slides(self) -> List[str]:
        """Sorted list of the slide identifiers present in the table."""
        return sorted(self.df["info:slide"].unique().tolist())


# ======================================================================= #
# Helpers
# ======================================================================= #
# Markers of an image that a pathologist's interface did not display: no rating.
_NOT_ANALYSED = {"no analyse", "no analysis", "not analysed", "not analyzed"}


def _clean(v):
    """Missing-value normalisation used for display and analysis."""
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, str):
        s = v.strip()
        if s in ("", "-", "--") or s.lower() in _NOT_ANALYSED:
            return None
        return s
    return v


class _KeyMatch(dict):
    """dict key -> path, also remembering which file keys were used."""
    values_keys: set


def _match_keys(keys: Sequence[str], files: Dict[str, str]) -> _KeyMatch:
    """Match the image names of the workbook to the image files found on disk, tolerating an
    'images/' prefix.
    """
    out = _KeyMatch()
    out.values_keys = set()
    by_stripped: Dict[str, List[str]] = {}
    for fk in files:
        by_stripped.setdefault(strip_prefix(fk), []).append(fk)
    for k in keys:
        if k in files:
            out[k] = files[k]
            out.values_keys.add(k)
            continue
        cands = by_stripped.get(strip_prefix(k), [])
        if len(cands) == 1:
            out[k] = files[cands[0]]
            out.values_keys.add(cands[0])
    return out


def scan_spectral(root: str) -> Dict[str, List[str]]:
    """Index spectral stacks (TIFF) by the image key they refer to."""
    out: Dict[str, List[str]] = {}
    if not root or not os.path.isdir(root):
        return out
    for folder, _dirs, names in os.walk(root):
        for n in sorted(names):
            if n.lower().endswith((".tif", ".tiff")) and not n.startswith("~$"):
                out.setdefault(spectral_key(n), []).append(os.path.join(folder, n))
    return out


def default_groups_path(excel_path: str) -> str:
    """Path of the CSV that stores the clinical-group overrides, next to the workbook."""
    stem = os.path.splitext(os.path.abspath(excel_path))[0]
    return stem + "_clinical_groups.csv"


def load_groups(path: str) -> Dict[str, str]:
    """Read the clinical-group overrides (slide -> group) from the CSV file, if it exists."""
    if not path or not os.path.isfile(path):
        return {}
    out: Dict[str, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            slide = (row.get("slide") or "").strip()
            group = (row.get("clinical_group") or "").strip()
            # 'Unassigned' entries (written by earlier versions) defer to the lab's rule.
            if slide and group and group != "Unassigned":
                out[slide] = group
    return out


def save_groups(path: str, mapping: Dict[str, str]) -> None:
    """Write the clinical-group overrides (slide -> group) to the CSV file."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["slide", "clinical_group"])
        for slide in sorted(mapping):
            w.writerow([slide, mapping[slide]])
