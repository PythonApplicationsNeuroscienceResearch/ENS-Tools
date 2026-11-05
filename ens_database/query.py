"""Multi-criteria selection of images.

A query is a list of :class:`Condition` objects combined with AND ('all') or
OR ('any').  Each condition compares one database column with a value; the
operators available depend on the column kind (numeric, ordinal, categorical,
text).  Missing values never satisfy a comparison, except for the negative
operators ('is not', 'does not contain') and 'is empty'.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

from .database import FieldSpec, _clean

OPERATORS = {
    "numeric": ["≥", "≤", "=", "≠", ">", "<", "is empty", "is not empty"],
    "ordinal": ["≥", "≤", "=", "≠", "is empty", "is not empty"],
    "categorical": ["is", "is not", "is empty", "is not empty"],
    "text": ["contains", "does not contain", "is empty", "is not empty"],
}
NO_VALUE_OPS = {"is empty", "is not empty"}


# === Conditions ==============================================================
@dataclass
class Condition:
    """One search condition: a column, an operator (e.g. '≥', 'contains') and a value."""
    column: str
    op: str
    value: object = None


def _compare(series: pd.Series, op: str, value: float) -> pd.Series:
    """Apply a numeric comparison operator to a whole column; empty cells never match."""
    if op == "≥":
        res = series >= value
    elif op == "≤":
        res = series <= value
    elif op == "=":
        res = series == value
    elif op == "≠":
        res = series != value
    elif op == ">":
        res = series > value
    elif op == "<":
        res = series < value
    else:
        raise ValueError(op)
    return res & series.notna()


def condition_mask(df: pd.DataFrame, spec: FieldSpec, cond: Condition) -> pd.Series:
    """Boolean mask of the rows that satisfy one condition, with the comparison adapted to the
    field type (number, ordinal grade, category, text).
    """
    raw = df[cond.column].map(_clean)
    if cond.op == "is empty":
        return raw.isna()
    if cond.op == "is not empty":
        return raw.notna()
    if spec.kind == "numeric":
        s = pd.to_numeric(raw, errors="coerce")
        return _compare(s, cond.op, float(cond.value))
    if spec.kind == "ordinal":
        order = [o.lower() for o in (spec.order or [])]
        s = raw.map(lambda v: order.index(str(v).lower()) if v is not None and str(v).lower() in order
                    else np.nan)
        target = order.index(str(cond.value).lower())
        return _compare(s.astype(float), cond.op, float(target))
    if spec.kind == "categorical":
        s = raw.map(lambda v: None if v is None else str(v))
        if cond.op == "is":
            return s == str(cond.value)
        if cond.op == "is not":
            return s != str(cond.value)
        raise ValueError(cond.op)
    # text
    needle = str(cond.value or "").lower()
    s = raw.map(lambda v: "" if v is None else str(v).lower())
    if cond.op == "contains":
        return s.str.contains(needle, regex=False)
    if cond.op == "does not contain":
        return ~s.str.contains(needle, regex=False)
    raise ValueError(cond.op)


# === Whole query =============================================================
def apply_query(df: pd.DataFrame, fields: Dict[str, FieldSpec], conditions: Sequence[Condition],
                mode: str = "all") -> pd.Series:
    """Boolean mask of the rows that satisfy the query."""
    valid = [c for c in conditions if c.column in df.columns and c.column in fields]
    if not valid:
        return pd.Series(True, index=df.index)
    masks = [condition_mask(df, fields[c.column], c).fillna(False).astype(bool) for c in valid]
    out = masks[0]
    for m in masks[1:]:
        out = (out & m) if mode == "all" else (out | m)
    return out


def describe(conditions: Sequence[Condition], fields: Dict[str, FieldSpec], mode: str = "all") -> str:
    """Human-readable text of a query, e.g. 'Pathologists calling Parkinson ≥ 2 AND ...'."""
    parts = []
    for c in conditions:
        label = fields[c.column].label if c.column in fields else c.column
        parts.append(f"{label} {c.op}" + ("" if c.op in NO_VALUE_OPS else f" {c.value}"))
    joiner = " AND " if mode == "all" else " OR "
    return joiner.join(parts) if parts else "(no condition)"


# === Saving and loading queries ==============================================
def save_query(path: str, conditions: List[Condition], mode: str) -> None:
    """Save a query (conditions and AND/OR mode) to a JSON file."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"mode": mode, "conditions": [asdict(c) for c in conditions]}, fh, indent=2,
                  ensure_ascii=False)


def load_query(path: str):
    """Load a query saved by save_query; returns (conditions, mode)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [Condition(**c) for c in data.get("conditions", [])], data.get("mode", "all")
