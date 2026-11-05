"""Surgical, loss-free writing of individual cells into an existing .xlsx file.

Why not openpyxl?  Loading and re-saving a workbook with openpyxl silently drops
everything it does not model: the 169 thumbnails embedded in the lab file,
conditional-formatting extensions, external links, etc.  This module instead
edits only the XML of the targeted cells and copies every other part of the
.xlsx package unchanged.

Safety measures
---------------
* A timestamped backup copy is written before any modification.
* The new file is first written to a temporary file, then atomically swapped in.
* Cells that contain a formula are never overwritten.
* ``fullCalcOnLoad`` is set so Excel recalculates formulas (e.g. the SUM
  totals) the next time the file is opened.
"""
from __future__ import annotations

import datetime as _dt
import glob
import os
import re
import shutil
import tempfile
import zipfile
from typing import Dict, Iterable, List, Optional, Tuple

from lxml import etree

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"

_PARSER = etree.XMLParser(huge_tree=True, remove_blank_text=False)


class XlsxWriteError(RuntimeError):
    """Raised when the workbook cannot be modified safely."""


class FileLockedError(XlsxWriteError):
    """Raised when the workbook is currently open in Excel (or another program)."""


# --------------------------------------------------------------------------- #
# Cell reference helpers
# --------------------------------------------------------------------------- #
def col_letter(col: int) -> str:
    """1 -> 'A', 27 -> 'AA'."""
    if col < 1:
        raise ValueError("Column index is 1-based")
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def col_index(letters: str) -> int:
    """'A' -> 1, 'AA' -> 27."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


def split_ref(ref: str) -> Tuple[int, int]:
    """'C12' -> (row 12, column 3)."""
    m = _REF_RE.match(ref.upper())
    if not m:
        raise ValueError(f"Invalid cell reference: {ref}")
    return int(m.group(2)), col_index(m.group(1))


# --------------------------------------------------------------------------- #
# Lock detection
# --------------------------------------------------------------------------- #
def is_locked(path: str) -> bool:
    """Best-effort detection that the workbook is open in Excel or LibreOffice.

    Excel (Windows and macOS) creates a hidden owner file '~$<name>' next to the
    workbook (for long names the first one or two characters of the name are
    dropped); LibreOffice creates '.~lock.<name>#'.  On Windows an open workbook
    also cannot be opened for writing.
    """
    folder, name = os.path.split(os.path.abspath(path))
    if os.path.exists(os.path.join(folder, f".~lock.{name}#")):
        return True
    for candidate in glob.glob(os.path.join(glob.escape(folder), "~$*")):
        tail = os.path.basename(candidate)[2:]
        if tail and (name == tail or name.endswith(tail) and len(tail) >= len(name) - 2):
            return True
    try:
        with open(path, "r+b"):
            pass
    except PermissionError:
        return True
    except OSError:
        return False
    return False


# --------------------------------------------------------------------------- #
# Package inspection
# --------------------------------------------------------------------------- #
def _resolve_target(base_dir: str, target: str) -> str:
    """Resolve a relationship target relative to the part that owns it."""
    if target.startswith("/"):
        return target.lstrip("/")
    parts: List[str] = [p for p in base_dir.split("/") if p]
    for piece in target.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def sheet_part_map(zf: zipfile.ZipFile) -> Dict[str, str]:
    """Map every worksheet name to its XML part inside the package."""
    wb = etree.fromstring(zf.read("xl/workbook.xml"), _PARSER)
    rels = etree.fromstring(zf.read("xl/_rels/workbook.xml.rels"), _PARSER)
    rid_to_target = {
        r.get("Id"): _resolve_target("xl", r.get("Target"))
        for r in rels.iter(f"{{{NS_PKG_REL}}}Relationship")
    }
    mapping: Dict[str, str] = {}
    for sheet in wb.iter(f"{{{NS_MAIN}}}sheet"):
        rid = sheet.get(f"{{{NS_REL}}}id")
        if rid in rid_to_target:
            mapping[sheet.get("name")] = rid_to_target[rid]
    return mapping


# --------------------------------------------------------------------------- #
# XML editing
# --------------------------------------------------------------------------- #
def _q(tag: str) -> str:
    """Qualified XML tag name in the SpreadsheetML namespace."""
    return f"{{{NS_MAIN}}}{tag}"


def _is_number(value: object) -> bool:
    """True for int and float values (booleans excluded)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format_number(value: float) -> str:
    """Number as written in the sheet XML (integers without a decimal point)."""
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return str(int(value))
    return repr(float(value))


def _set_cell(sheet_data: etree._Element, rows: Dict[int, etree._Element],
              row: int, col: int, value: object) -> None:
    """Write ``value`` into cell (row, col), creating the row/cell if needed."""
    ref = f"{col_letter(col)}{row}"

    row_el = rows.get(row)
    if row_el is None:
        row_el = etree.Element(_q("row"))
        row_el.set("r", str(row))
        # Keep rows sorted by index, as required by the OOXML schema.
        following = [r for idx, r in rows.items() if idx > row]
        if following:
            min(following, key=lambda e: int(e.get("r"))).addprevious(row_el)
        else:
            sheet_data.append(row_el)
        rows[row] = row_el
    # 'spans' is an optional optimisation hint; drop it so it can never be wrong.
    if "spans" in row_el.attrib:
        del row_el.attrib["spans"]

    cell = None
    insert_before = None
    for c in row_el.findall(_q("c")):
        c_ref = c.get("r")
        if c_ref is None:
            continue
        _, c_col = split_ref(c_ref)
        if c_col == col:
            cell = c
            break
        if c_col > col and insert_before is None:
            insert_before = c
    if cell is None:
        cell = etree.Element(_q("c"))
        cell.set("r", ref)
        if insert_before is not None:
            insert_before.addprevious(cell)
        else:
            row_el.append(cell)

    if cell.find(_q("f")) is not None:
        raise XlsxWriteError(f"Cell {ref} contains a formula and cannot be edited.")

    for child in list(cell):
        if child.tag in (_q("v"), _q("is")):
            cell.remove(child)
    if "t" in cell.attrib:
        del cell.attrib["t"]

    if value is None or (isinstance(value, str) and value == ""):
        return  # empty cell (style attribute is preserved)
    if _is_number(value):
        v = etree.SubElement(cell, _q("v"))
        v.text = _format_number(value)
        return
    if isinstance(value, bool):
        cell.set("t", "b")
        v = etree.SubElement(cell, _q("v"))
        v.text = "1" if value else "0"
        return
    text = str(value)
    cell.set("t", "inlineStr")
    is_el = etree.SubElement(cell, _q("is"))
    t_el = etree.SubElement(is_el, _q("t"))
    t_el.text = text
    if text != text.strip():
        t_el.set(f"{{{XML_NS}}}space", "preserve")


def _patch_sheet_xml(xml_bytes: bytes, edits: Iterable[Tuple[int, int, object]]) -> bytes:
    """Apply the cell edits to the XML of one worksheet and return the new XML."""
    root = etree.fromstring(xml_bytes, _PARSER)
    sheet_data = root.find(_q("sheetData"))
    if sheet_data is None:
        raise XlsxWriteError("Worksheet has no <sheetData> element.")
    rows = {int(r.get("r")): r for r in sheet_data.findall(_q("row")) if r.get("r")}
    for row, col, value in edits:
        _set_cell(sheet_data, rows, int(row), int(col), value)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _patch_workbook_xml(xml_bytes: bytes) -> bytes:
    """Ask Excel to recalculate every formula when the file is next opened."""
    root = etree.fromstring(xml_bytes, _PARSER)
    calc = root.find(_q("calcPr"))
    if calc is None:
        calc = etree.SubElement(root, _q("calcPr"))
        # calcPr must precede these optional elements.
        for tag in ("oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr",
                    "smartTagTypes", "webPublishing", "fileRecoveryPr",
                    "webPublishObjects", "extLst"):
            nxt = root.find(_q(tag))
            if nxt is not None:
                nxt.addprevious(calc)
                break
    calc.set("fullCalcOnLoad", "1")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def make_backup(path: str, backup_dir: Optional[str] = None, keep: int = 10) -> str:
    """Copy the workbook into ``backup_dir`` with a timestamp; keep the last ``keep``."""
    folder, name = os.path.split(os.path.abspath(path))
    stem, ext = os.path.splitext(name)
    backup_dir = backup_dir or os.path.join(folder, "_backups_ENS_database")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"{stem}_{stamp}{ext}")
    shutil.copy2(path, dest)
    existing = sorted(glob.glob(os.path.join(glob.escape(backup_dir), f"{glob.escape(stem)}_*{ext}")))
    for old in existing[:-keep] if keep > 0 else []:
        try:
            os.remove(old)
        except OSError:
            pass
    return dest


def write_cells(path: str, edits: Dict[str, List[Tuple[int, int, object]]],
                backup: bool = True, backup_dir: Optional[str] = None) -> Optional[str]:
    """Write cell values into an existing workbook without altering anything else.

    Parameters
    ----------
    path : str
        The .xlsx file.
    edits : dict
        ``{sheet name: [(row, column, value), ...]}`` with 1-based indices.
        ``value`` may be a number, a string or ``None`` (clears the cell).
    backup : bool
        Write a timestamped copy before modifying the file.

    Returns
    -------
    The path of the backup copy (or None when ``backup`` is False).
    """
    if not edits:
        return None
    if not os.access(path, os.W_OK):
        raise XlsxWriteError(
            "The Excel file is read-only (file attribute or folder permissions). "
            "Remove the read-only attribute and save again.")
    if is_locked(path):
        raise FileLockedError(
            "The Excel file is open in another program. Close it in Excel, then save again.")

    with zipfile.ZipFile(path, "r") as zin:
        mapping = sheet_part_map(zin)
        missing = [s for s in edits if s not in mapping]
        if missing:
            raise XlsxWriteError(f"Sheet(s) not found in workbook: {', '.join(missing)}")
        new_parts: Dict[str, bytes] = {}
        for sheet_name, sheet_edits in edits.items():
            part = mapping[sheet_name]
            new_parts[part] = _patch_sheet_xml(zin.read(part), sheet_edits)
        new_parts["xl/workbook.xml"] = _patch_workbook_xml(zin.read("xl/workbook.xml"))

        backup_path = make_backup(path, backup_dir) if backup else None

        folder = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=folder)
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp_path, "w") as zout:
                for info in zin.infolist():
                    data = new_parts.get(info.filename)
                    if data is None:
                        data = zin.read(info.filename)
                    out_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                    out_info.compress_type = (zipfile.ZIP_DEFLATED
                                              if info.compress_type != zipfile.ZIP_STORED
                                              else zipfile.ZIP_STORED)
                    out_info.external_attr = info.external_attr
                    out_info.create_system = info.create_system
                    zout.writestr(out_info, data)
        except Exception:
            os.remove(tmp_path)
            raise

    try:
        os.replace(tmp_path, path)
    except PermissionError as exc:
        os.remove(tmp_path)
        raise FileLockedError(
            "Windows refused to replace the Excel file (it is probably open in Excel). "
            "Close it and save again.") from exc
    return backup_path
