"""ROI persistence (JSON) and import of ImageJ ROI files (.roi / RoiSet .zip)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from .spectral_core import RoiGeometry


@dataclass
class RoiRecord:
    """One ROI as saved to file: id, name, stack it belongs to, geometry, role and colour."""
    uid: int
    name: str
    image: str                 # path of the spectral stack the ROI was drawn on
    geom: RoiGeometry
    color: str
    visible: bool = True
    role: str = "Signal"           # 'Signal' or 'Background'


def save_rois(path: str, rois: List[RoiRecord], extra: dict) -> None:
    """Save a set of ROIs (and the analysis settings in extra) to a JSON file, for reproducibility.
    """
    data = {
        "format": "SpectralAnalyzer ROI set",
        "version": 1,
        **extra,
        "rois": [{
            "name": r.name, "image": r.image, "image_name": os.path.basename(r.image),
            "shape": r.geom.shape, "params": r.geom.params, "color": r.color, "visible": r.visible,
            "role": r.role,
        } for r in rois],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def load_rois(path: str) -> List[dict]:
    """Read a JSON ROI set saved by save_rois; returns one dict per ROI with its geometry."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for r in data.get("rois", []):
        out.append({
            "name": r["name"], "image": r.get("image", ""), "image_name": r.get("image_name", ""),
            "geom": RoiGeometry(r["shape"], r["params"]), "color": r.get("color"),
            "visible": r.get("visible", True), "role": r.get("role", "Signal"),
        })
    return out


def import_imagej(path: str) -> Tuple[List[Tuple[str, RoiGeometry]], List[str]]:
    """Convert ImageJ ROIs into geometries. Returns (rois, skipped messages)."""
    try:
        from roifile import ROI_TYPE, ImagejRoi
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("The 'roifile' package is required to read ImageJ ROIs.") from exc
    loaded = ImagejRoi.fromfile(path)
    if not isinstance(loaded, list):
        loaded = [loaded]
    rois: List[Tuple[str, RoiGeometry]] = []
    skipped: List[str] = []
    for k, roi in enumerate(loaded, start=1):
        name = roi.name or f"ImageJ {k}"
        if getattr(roi, "composite", False):
            skipped.append(f"{name}: composite ROI not supported")
            continue
        t = roi.roitype
        if t == ROI_TYPE.RECT:
            rois.append((name, RoiGeometry("rectangle", {
                "x0": float(roi.left), "y0": float(roi.top),
                "x1": float(roi.right), "y1": float(roi.bottom)})))
        elif t == ROI_TYPE.OVAL:
            rois.append((name, RoiGeometry("ellipse", {
                "cx": (roi.left + roi.right) / 2.0, "cy": (roi.top + roi.bottom) / 2.0,
                "rx": (roi.right - roi.left) / 2.0, "ry": (roi.bottom - roi.top) / 2.0})))
        elif t in (ROI_TYPE.POLYGON, ROI_TYPE.FREEHAND, ROI_TYPE.TRACED):
            pts = np.asarray(roi.coordinates(), dtype=float)
            if len(pts) >= 3:
                rois.append((name, RoiGeometry("polygon", {"points": pts.tolist()})))
            else:
                skipped.append(f"{name}: fewer than 3 vertices")
        elif t == ROI_TYPE.POINT:
            for j, (x, y) in enumerate(np.asarray(roi.coordinates(), dtype=float), start=1):
                rois.append((f"{name}.{j}" if j > 1 else name,
                             RoiGeometry("point", {"x": float(x), "y": float(y), "radius": 0})))
        else:
            skipped.append(f"{name}: ROI type {t.name} has no area")
    return rois, skipped
