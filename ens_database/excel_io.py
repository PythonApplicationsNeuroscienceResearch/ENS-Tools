"""Reading of the lab workbook.

The workbook contains one sheet per observer:
  * one *morphology* sheet (Doriane Hazart, 'DH'): semi-quantitative counts of
    small / large nerve cells with or without granules and nucleoli;
  * one sheet per pathologist ('FC', 'MD', 'PD', 'PN'): plexus type,
    diagnosis, certainty, morphological description, comments.

A further sheet ('Fichier aligné ...') juxtaposes all observers row by row.  It
is deliberately ignored: it is a derived copy, and it contains row shifts
(comments attached to the wrong image).  The application rebuilds the joined
table itself by matching image names, which cannot drift.

Sheets are recognised from their header row, not from their position, so the
workbook may evolve (new rows, new raters, re-ordered columns).
"""
from __future__ import annotations

import os
import re
import unicodedata
import warnings
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import openpyxl
from openpyxl.utils import column_index_from_string

from .naming import IMAGE_EXTENSIONS, image_key, slide_id

# Header aliases -> canonical field names.
_ALIASES = {
    "nom_de_l_image": "image_name",
    "image_name": "image_name",
    "nom_image": "image_name",
    "reponse_image": "plexus",
    "diagnostic": "diagnosis",
    "diagnosis": "diagnosis",
    "certitude": "certainty",
    "certainty": "certainty",
    "caracteristiques_morphologiques": "morphology",
    "commentaires": "comments",
    "commentaire": "comments",
    "comments": "comments",
    "comment": "comments",
    "login": "login",
    "true_diagnosis": "true_diagnosis",
    "vrai_diagnostic_patient": "true_diagnosis",
    "patient": "patient",
}


# === Text clean-up ===========================================================
def fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was decoded as Latin-1 ('RÃ©ponse' -> 'Réponse')."""
    if "Ã" in text or "Â" in text:
        try:
            return text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text
    return text


def canonical_header(header: object) -> Optional[str]:
    """Normalise a header to a lower-case ASCII identifier."""
    if header is None:
        return None
    text = fix_mojibake(str(header)).strip()
    if not text:
        return None
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower()
    return _ALIASES.get(text, text)


@dataclass
class SheetData:
    """Content of one observer sheet."""
    name: str                          # sheet name in the workbook
    role: str                          # 'morphology' or 'rater'
    code: str                          # short observer code, e.g. 'DH', 'PN'
    header_row: int                    # 1-based row index of the header
    columns: Dict[str, int]            # canonical field -> 1-based column index
    headers: Dict[str, str]            # canonical field -> original header text
    formula_fields: Set[str] = field(default_factory=set)
    records: Dict[str, Dict[str, object]] = field(default_factory=dict)  # key -> values
    row_of: Dict[str, int] = field(default_factory=dict)                 # key -> row
    duplicates: List[str] = field(default_factory=list)
    login: str = ""


@dataclass
class WorkbookData:
    """Everything read from the workbook: the usable sheets, the ignored ones and the per-slide
    information.
    """
    sheets: List[SheetData]
    skipped: List[str]                 # human-readable reasons
    # Per-slide (= per-patient) information found in the workbook:
    # slide -> (true diagnosis, patient id), with the sheet it came from.
    slide_info: Dict[str, Tuple[Optional[str], Optional[str]]] = field(default_factory=dict)
    slide_info_sheet: str = ""
    slide_info_conflicts: List[str] = field(default_factory=list)


# === Sheet reading helpers ===================================================
def _sheet_code(sheet_name: str) -> str:
    """Short code of a sheet ('All results FC' -> 'FC'), used to prefix its columns."""
    m = re.search(r"results?\s+([A-Za-z]{1,4})\b", sheet_name, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    letters = re.sub(r"[^A-Za-z0-9]", "", sheet_name)
    return letters[:6].upper() or sheet_name


def _find_header(rows: List[tuple]) -> Optional[int]:
    """Index (0-based) of the first row that contains an image-name header."""
    for i, row in enumerate(rows[:10]):
        if any(canonical_header(v) == "image_name" for v in row):
            return i
    return None


_SAME_ROW_REF = re.compile(r"^=\$?([A-Za-z]{1,3})\$?(\d+)$")


def _resolve(row: tuple, j: int, row_number: int):
    """Value of column j; a formula '=C12' pointing to the same row is followed."""
    v = row[j] if j < len(row) else None
    if isinstance(v, str):
        m = _SAME_ROW_REF.match(v.strip())
        if m and int(m.group(2)) == row_number:
            k = column_index_from_string(m.group(1).upper()) - 1
            if k != j:
                return row[k] if k < len(row) else None
    return v


def _looks_like_image_name(v: object) -> bool:
    """True when a cell value is a file name with an image extension."""
    return isinstance(v, str) and os.path.splitext(v.strip())[1].lower() in IMAGE_EXTENSIONS


def _slide_info(ws_title: str, rows: List[tuple], out: WorkbookData) -> None:
    """Collect the 'True Diagnosis' / 'Patient' columns of a sheet, per slide.

    In the lab workbook these columns sit in a block of the summary sheet whose
    image-name column is the nearest column to their left holding image file
    names.  The values are aggregated per slide (one slide = one patient), which
    makes the result insensitive to row shifts inside a slide; a slide with
    several different values is reported as a conflict and left undefined.
    """
    h_idx = None
    for i, row in enumerate(rows[:10]):
        if any(canonical_header(v) == "true_diagnosis" for v in row):
            h_idx = i
            break
    if h_idx is None or out.slide_info:
        return
    canon = [canonical_header(v) for v in rows[h_idx]]
    c_dx = canon.index("true_diagnosis")
    c_pt = next((j for j in range(c_dx + 1, len(canon)) if canon[j] == "patient"), None)
    if c_pt is None and "patient" in canon:
        c_pt = canon.index("patient")
    data = list(enumerate(rows[h_idx + 1:], start=h_idx + 2))    # (Excel row number, values)
    c_name = None
    for j in range(c_dx - 1, -1, -1):
        vals = [_resolve(r, j, n) for n, r in data]
        vals = [v for v in vals if v not in (None, "")]
        if vals and sum(_looks_like_image_name(v) for v in vals) >= 0.9 * len(vals):
            c_name = j
            break
    if c_name is None:
        return
    seen: Dict[str, Set[Tuple[Optional[str], Optional[str]]]] = {}
    for n, r in data:
        name = _resolve(r, c_name, n)
        if not _looks_like_image_name(name):
            continue
        dx = _resolve(r, c_dx, n)
        pt = _resolve(r, c_pt, n) if c_pt is not None else None
        dx = str(dx).strip() if dx not in (None, "") else None
        pt = str(pt).strip() if pt not in (None, "") else None
        if dx is None and pt is None:
            continue
        seen.setdefault(slide_id(image_key(name)), set()).add((dx, pt))
    for sl, vals in seen.items():
        if len(vals) == 1:
            out.slide_info[sl] = next(iter(vals))
        else:
            out.slide_info_conflicts.append(
                f"'{ws_title}': slide {sl} has several diagnosis/patient values "
                f"({'; '.join(sorted(f'{a} / {b}' for a, b in vals))}); left undefined.")
    out.slide_info_sheet = ws_title


# === Workbook reading ========================================================
def read_workbook(path: str) -> WorkbookData:
    """Parse every observer sheet of the workbook."""
    # openpyxl warns about Excel extensions it does not model (conditional
    # formatting); they are irrelevant for reading values.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
        return _read_sheets(wb)


def _read_sheets(wb) -> WorkbookData:
    """Read every worksheet: find its header row, map the headers to canonical field names and keep
    the rows that name an image.
    """
    try:
        sheets: List[SheetData] = []
        skipped: List[str] = []
        used_codes: Set[str] = set()
        info = WorkbookData(sheets=[], skipped=[])
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            _slide_info(ws.title, rows, info)
            h_idx = _find_header(rows)
            if h_idx is None:
                skipped.append(f"'{ws.title}': no 'Nom de l'image' header found.")
                continue
            header = rows[h_idx]
            canon = [canonical_header(h) for h in header]
            counts = Counter(c for c in canon if c)
            if counts.get("login", 0) > 1 or counts.get("image_name", 0) > 1:
                skipped.append(
                    f"'{ws.title}': derived sheet juxtaposing several observers; "
                    "the application rebuilds this table from the individual sheets.")
                continue
            if "empty_plexus" in counts:
                role = "morphology"
            elif "diagnosis" in counts:
                role = "rater"
            else:
                skipped.append(f"'{ws.title}': neither morphology nor diagnosis columns.")
                continue

            code = _sheet_code(ws.title)
            base, n = code, 2
            while code in used_codes:
                code = f"{base}{n}"
                n += 1
            used_codes.add(code)

            columns: Dict[str, int] = {}
            headers: Dict[str, str] = {}
            for j, (c, raw) in enumerate(zip(canon, header), start=1):
                if c and c not in columns:
                    columns[c] = j
                    headers[c] = fix_mojibake(str(raw)).strip()

            sheet = SheetData(name=ws.title, role=role, code=code, header_row=h_idx + 1,
                              columns=columns, headers=headers)
            img_col = columns["image_name"] - 1
            logins = Counter()
            for r_off, row in enumerate(rows[h_idx + 1:], start=h_idx + 2):
                if img_col >= len(row):
                    continue
                name = row[img_col]
                if name is None or str(name).strip() == "":
                    continue
                if str(name).strip().lower() in ("no analyse", "no analysis"):
                    continue
                key = image_key(name)
                values: Dict[str, object] = {}
                for fld, col in columns.items():
                    v = row[col - 1] if col - 1 < len(row) else None
                    if isinstance(v, str) and v.startswith("="):
                        sheet.formula_fields.add(fld)
                        v = None  # formulas are recomputed by the application
                    elif isinstance(v, str):
                        v = v.strip()
                    values[fld] = v
                if key in sheet.records:
                    sheet.duplicates.append(key)
                    continue  # first occurrence wins
                sheet.records[key] = values
                sheet.row_of[key] = r_off
                if values.get("login"):
                    logins[str(values["login"])] += 1
            sheet.login = logins.most_common(1)[0][0] if logins else ""
            sheets.append(sheet)
        return WorkbookData(sheets=sheets, skipped=skipped, slide_info=info.slide_info,
                            slide_info_sheet=info.slide_info_sheet,
                            slide_info_conflicts=info.slide_info_conflicts)
    finally:
        wb.close()
