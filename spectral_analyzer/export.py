"""Export of spectra (Excel / CSV) and of publication figures (TIFF / PNG / JPEG / PDF / SVG)."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from . import __version__


# === Tables ==================================================================
@dataclass
class ExportRow:
    """Everything known about one ROI spectrum."""
    name: str
    image: str
    shape: str
    n_pixels: int
    area_um2: Optional[float]
    centroid_xy: tuple
    wl: np.ndarray
    raw_mean: np.ndarray
    raw_sd: np.ndarray
    proc: np.ndarray               # background-subtracted + normalised (as plotted)
    proc_sd: Optional[np.ndarray]
    peak_nm: float
    peak_interp_nm: float
    fwhm_nm: float
    centroid_nm: float
    wl_source: str


def _col(r: ExportRow, multi_image: bool) -> str:
    """Column name of one ROI in the export (the image name is added when several images are
    exported).
    """
    return f"{r.name} [{os.path.splitext(os.path.basename(r.image))[0]}]" if multi_image else r.name


def spectra_tables(rows: List[ExportRow], normalisation: str, background: str):
    """Build the four export tables: processed spectra (as plotted), raw mean ± SD, ROI descriptors
    and settings.
    """
    multi = len({r.image for r in rows}) > 1
    base_wl = rows[0].wl
    same_axis = all(len(r.wl) == len(base_wl) and np.allclose(r.wl, base_wl) for r in rows)

    def table(kind: str) -> pd.DataFrame:
        """Wide table, one column per ROI, of the processed ('proc') or raw values ('raw')."""
        if same_axis:
            df = pd.DataFrame({"Channel": np.arange(1, len(base_wl) + 1),
                               "Wavelength (nm)": np.round(base_wl, 3)})
            for r in rows:
                c = _col(r, multi)
                if kind == "proc":
                    df[c] = r.proc
                    if r.proc_sd is not None:
                        df[f"{c} SD"] = r.proc_sd
                else:
                    df[f"{c} mean"] = r.raw_mean
                    df[f"{c} SD"] = r.raw_sd
            return df
        # Different wavelength axes: one wavelength column per ROI.
        parts = []
        for r in rows:
            c = _col(r, multi)
            d = pd.DataFrame({f"{c} wavelength (nm)": np.round(r.wl, 3)})
            if kind == "proc":
                d[c] = r.proc
            else:
                d[f"{c} mean"] = r.raw_mean
                d[f"{c} SD"] = r.raw_sd
            parts.append(d)
        return pd.concat(parts, axis=1)

    proc = table("proc")
    raw = table("raw")
    info = pd.DataFrame([{
        "ROI": r.name,
        "Image": os.path.basename(r.image),
        "Image path": r.image,
        "Shape": r.shape,
        "Pixels (n)": r.n_pixels,
        "Area (µm²)": None if r.area_um2 is None else round(r.area_um2, 3),
        "Centroid x (px)": round(r.centroid_xy[0], 2),
        "Centroid y (px)": round(r.centroid_xy[1], 2),
        "Peak (nm, channel centre)": round(r.peak_nm, 2),
        "Peak (nm, interpolated)": round(r.peak_interp_nm, 2),
        "FWHM (nm)": None if not np.isfinite(r.fwhm_nm) else round(r.fwhm_nm, 2),
        "Spectral centroid (nm)": round(r.centroid_nm, 2),
        "Wavelength source": r.wl_source,
    } for r in rows])
    settings = pd.DataFrame({
        "Item": ["Software", "Exported", "Normalisation", "Background subtraction",
                 "Raw sheet", "Processed sheet",
                 "Peak (interpolated)", "FWHM", "Spectral centroid"],
        "Value": [f"Spectral Analyzer {__version__}", dt.datetime.now().isoformat(timespec="seconds"),
                  normalisation, background or "none",
                  "Mean and SD of the raw intensities of the pixels inside each ROI (no processing).",
                  "Background-subtracted (if selected), then normalised; this is the plotted curve.",
                  "3-point parabolic interpolation around the brightest channel.",
                  "Full width at half maximum, linear interpolation between channels; empty when "
                  "the spectrum does not fall below half maximum on both sides.",
                  "Intensity-weighted mean wavelength of the processed spectrum (negative values "
                  "set to 0)."]})
    return proc, raw, info, settings


# === Files ===================================================================
def export_excel(path: str, rows: List[ExportRow], normalisation: str, background: str,
                 calibration_text: str, extra: Optional[List[tuple]] = None) -> None:
    """Write the spectra, the ROI table and every analysis setting to an .xlsx file."""
    proc, raw, info, settings = spectra_tables(rows, normalisation, background)
    settings.loc[len(settings)] = ["Wavelength calibration", calibration_text]
    for item, value in extra or []:
        settings.loc[len(settings)] = [item, value]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        proc.to_excel(xw, sheet_name="Spectra (processed)", index=False)
        raw.to_excel(xw, sheet_name="Spectra (raw)", index=False)
        info.to_excel(xw, sheet_name="ROIs", index=False)
        settings.to_excel(xw, sheet_name="Settings", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:60])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)
            ws.freeze_panes = "A2"


def export_csv(path: str, rows: List[ExportRow], normalisation: str, background: str) -> None:
    """Write the processed spectra to a CSV file (UTF-8, readable by Excel)."""
    proc, _raw, _info, _settings = spectra_tables(rows, normalisation, background)
    proc.to_csv(path, index=False, encoding="utf-8-sig")


FIGURE_FORMATS = {
    "TIFF (publication, LZW)": ".tif",
    "PNG": ".png",
    "JPEG": ".jpg",
    "PDF (vector)": ".pdf",
    "SVG (vector)": ".svg",
}


def save_figure(fig, path: str, dpi: int) -> None:
    """Save a matplotlib figure at the requested resolution (TIFF files compressed with LZW)."""
    ext = os.path.splitext(path)[1].lower()
    kw = {"dpi": dpi, "bbox_inches": "tight", "facecolor": "white"}
    if ext in (".tif", ".tiff"):
        kw["pil_kwargs"] = {"compression": "tiff_lzw"}
        fig.savefig(path, format="tiff", **kw)
    elif ext in (".jpg", ".jpeg"):
        kw["pil_kwargs"] = {"quality": 95}
        fig.savefig(path, format="jpeg", **kw)
    else:
        fig.savefig(path, **kw)
