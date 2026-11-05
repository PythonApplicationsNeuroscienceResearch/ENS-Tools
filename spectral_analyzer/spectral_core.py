"""Numerical core: display composites, ROI masks, spectra and spectral descriptors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np
from matplotlib import colormaps
from matplotlib.path import Path

# --------------------------------------------------------------------------- #
# Colour
# --------------------------------------------------------------------------- #
# === Display of the stack (colours, projections) =============================
def wavelength_to_rgb(nm: float) -> Tuple[float, float, float]:
    """Approximate perceived colour of monochromatic light (Bruton's algorithm).

    Returns RGB in [0, 1]; intensity falls off towards the limits of vision.
    """
    w = float(nm)
    if 380 <= w < 440:
        r, g, b = -(w - 440) / (440 - 380), 0.0, 1.0
    elif 440 <= w < 490:
        r, g, b = 0.0, (w - 440) / (490 - 440), 1.0
    elif 490 <= w < 510:
        r, g, b = 0.0, 1.0, -(w - 510) / (510 - 490)
    elif 510 <= w < 580:
        r, g, b = (w - 510) / (580 - 510), 1.0, 0.0
    elif 580 <= w < 645:
        r, g, b = 1.0, -(w - 645) / (645 - 580), 0.0
    elif 645 <= w <= 780:
        r, g, b = 1.0, 0.0, 0.0
    else:
        r = g = b = 0.0
    if 380 <= w < 420:
        f = 0.3 + 0.7 * (w - 380) / (420 - 380)
    elif 420 <= w <= 700:
        f = 1.0
    elif 700 < w <= 780:
        f = 0.3 + 0.7 * (780 - w) / (780 - 700)
    else:
        f = 0.0
    gamma = 0.8
    return tuple((c * f) ** gamma if c > 0 else 0.0 for c in (r, g, b))  # type: ignore


LUTS = ["Physical (wavelength)", "Spectrum (blue → red)", "Turbo", "Fire"]


def channel_colors(wavelengths: np.ndarray, lut: str) -> np.ndarray:
    """(C, 3) colours assigned to each channel for colour-coded projections."""
    n = len(wavelengths)
    if lut.startswith("Physical"):
        cols = np.array([wavelength_to_rgb(w) for w in wavelengths], dtype=np.float32)
        # Channels outside the visible range would be black; give them a dim tint.
        dark = cols.max(axis=1) < 0.15
        if dark.any():
            cols[dark] = (np.array([0.35, 0.0, 0.35]) if wavelengths[dark].mean() < 450
                          else np.array([0.35, 0.35, 0.35]))
        return cols
    t = np.linspace(0, 1, n)
    if lut.startswith("Spectrum"):
        cmap = colormaps["rainbow"]
    elif lut == "Turbo":
        cmap = colormaps["turbo"]
    else:
        cmap = colormaps["hot"]
        t = np.linspace(0.25, 1.0, n)
    return np.asarray(cmap(t))[:, :3].astype(np.float32)


# --------------------------------------------------------------------------- #
# Display composites (all return float RGB in [0, 1], shape (Y, X, 3))
# --------------------------------------------------------------------------- #
def intensity_limits(data: np.ndarray, low_pct: float, high_pct: float) -> Tuple[float, float]:
    """Display limits (low and high percentiles) of a stack, computed on a subsample for speed."""
    step = max(1, int(np.ceil(np.sqrt(data[0].size / 250_000))))
    sample = data[:, ::step, ::step].astype(np.float32)
    lo, hi = np.percentile(sample, (low_pct, high_pct))
    if hi <= lo:
        hi = lo + 1.0
    return float(lo), float(hi)


def _norm(x: np.ndarray, lo: float, hi: float, gamma: float) -> np.ndarray:
    """Scale values to 0–1 between lo and hi, then apply the display gamma."""
    y = np.clip((x.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)
    if gamma != 1.0:
        y = y ** (1.0 / gamma)
    return y


def color_code_projection(data: np.ndarray, colors: np.ndarray, lo, hi,
                          gamma: float = 1.0) -> np.ndarray:
    """ImageJ 'Temporal-Color Code' applied to the spectral axis.

    Each channel is scaled to [0, 1], tinted with its colour, and the RGB stack is
    projected by maximum intensity.  ``lo``/``hi`` are either scalars (common
    limits: preserves the relative brightness of the channels) or arrays of one
    value per channel (equalised display: weak channels become visible).
    """
    n = data.shape[0]
    lo = np.broadcast_to(np.asarray(lo, dtype=np.float32), (n,))
    hi = np.broadcast_to(np.asarray(hi, dtype=np.float32), (n,))
    out = np.zeros(data.shape[1:] + (3,), dtype=np.float32)
    for c in range(n):
        span = max(float(hi[c] - lo[c]), 1e-9)
        ch = np.clip((data[c].astype(np.float32) - lo[c]) / span, 0.0, 1.0)
        np.maximum(out, ch[..., None] * colors[c][None, None, :], out=out)
    if gamma != 1.0:
        out = out ** (1.0 / gamma)
    return out


def channel_limits(data: np.ndarray, low_pct: float, high_pct: float):
    """Per-channel percentile limits (arrays of shape (C,))."""
    step = max(1, int(np.ceil(np.sqrt(data[0].size / 250_000))))
    sample = data[:, ::step, ::step].reshape(data.shape[0], -1).astype(np.float32)
    lo = np.percentile(sample, low_pct, axis=1)
    hi = np.percentile(sample, high_pct, axis=1)
    hi = np.where(hi <= lo, lo + 1.0, hi)
    return lo, hi


def true_color(data: np.ndarray, wavelengths: np.ndarray, low_pct: float, high_pct: float,
               gamma: float = 1.0) -> np.ndarray:
    """Weighted sum of the channels by the colour of their wavelength."""
    cols = np.array([wavelength_to_rgb(w) for w in wavelengths], dtype=np.float32)  # (C, 3)
    c, h, w = data.shape
    rgb = np.tensordot(cols.T, data.reshape(c, -1).astype(np.float32), axes=1)       # (3, YX)
    rgb = rgb.T.reshape(h, w, 3)
    rgb -= np.percentile(rgb, low_pct)
    top = np.percentile(rgb.max(axis=2), high_pct)
    rgb = np.clip(rgb / max(float(top), 1e-9), 0.0, 1.0)
    if gamma != 1.0:
        rgb = rgb ** (1.0 / gamma)
    return rgb


def gray(img2d: np.ndarray, low_pct: float, high_pct: float, gamma: float = 1.0) -> np.ndarray:
    """Grey-level display of a single 2-D image with percentile limits and gamma."""
    lo, hi = np.percentile(img2d, (low_pct, high_pct))
    if hi <= lo:
        hi = lo + 1.0
    g = _norm(img2d, float(lo), float(hi), gamma)
    return np.repeat(g[..., None], 3, axis=2)


def to_uint8(rgb: np.ndarray) -> np.ndarray:
    """Convert an RGB image in 0–1 to 8-bit for display."""
    return np.ascontiguousarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8))


# --------------------------------------------------------------------------- #
# ROI geometry
# --------------------------------------------------------------------------- #
SHAPES = ("freehand", "polygon", "rectangle", "ellipse", "point")


# === Regions of interest (ROI) ===============================================
@dataclass
class RoiGeometry:
    """ROI in image pixel coordinates; pixel (row i, col j) spans [j, j+1) x [i, i+1).

    params:
      freehand / polygon : {'points': [[x, y], ...]}
      rectangle          : {'x0', 'y0', 'x1', 'y1'}
      ellipse            : {'cx', 'cy', 'rx', 'ry'}
      point              : {'x', 'y', 'radius'}  (radius in pixels, 0 = single pixel)
    """
    shape: str
    params: Dict = field(default_factory=dict)

    def bbox(self) -> Tuple[float, float, float, float]:
        """Bounding box (x0, y0, x1, y1) of the ROI in image pixels."""
        p = self.params
        if self.shape in ("freehand", "polygon"):
            pts = np.asarray(p["points"], dtype=float)
            return pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()
        if self.shape == "rectangle":
            return (min(p["x0"], p["x1"]), min(p["y0"], p["y1"]),
                    max(p["x0"], p["x1"]), max(p["y0"], p["y1"]))
        if self.shape == "ellipse":
            return p["cx"] - p["rx"], p["cy"] - p["ry"], p["cx"] + p["rx"], p["cy"] + p["ry"]
        r = p.get("radius", 0)
        return p["x"] - r - 1, p["y"] - r - 1, p["x"] + r + 1, p["y"] + r + 1

    def outline(self) -> np.ndarray:
        """Closed outline (N, 2) used for drawing."""
        p = self.params
        if self.shape in ("freehand", "polygon"):
            return np.asarray(p["points"], dtype=float)
        if self.shape == "rectangle":
            x0, y0, x1, y1 = self.bbox()
            return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
        if self.shape == "ellipse":
            t = np.linspace(0, 2 * np.pi, 96, endpoint=False)
            return np.c_[p["cx"] + p["rx"] * np.cos(t), p["cy"] + p["ry"] * np.sin(t)]
        r = max(float(p.get("radius", 0)), 0.5)
        cx, cy = np.floor(p["x"]) + 0.5, np.floor(p["y"]) + 0.5
        if p.get("radius", 0) == 0:
            return np.array([[cx - .5, cy - .5], [cx + .5, cy - .5], [cx + .5, cy + .5],
                             [cx - .5, cy + .5]])
        t = np.linspace(0, 2 * np.pi, 48, endpoint=False)
        return np.c_[cx + (r + .5) * np.cos(t), cy + (r + .5) * np.sin(t)]


def roi_mask(shape_yx: Tuple[int, int], geom: RoiGeometry) -> np.ndarray:
    """Boolean mask of the pixels whose centre lies inside the ROI."""
    h, w = shape_yx
    mask = np.zeros((h, w), dtype=bool)
    x0, y0, x1, y1 = geom.bbox()
    j0, j1 = max(int(np.floor(x0)), 0), min(int(np.ceil(x1)), w)
    i0, i1 = max(int(np.floor(y0)), 0), min(int(np.ceil(y1)), h)
    if j1 <= j0 or i1 <= i0:
        return mask
    jj, ii = np.meshgrid(np.arange(j0, j1) + 0.5, np.arange(i0, i1) + 0.5)
    p = geom.params
    if geom.shape in ("freehand", "polygon"):
        pts = np.asarray(p["points"], dtype=float)
        if len(pts) < 3:
            return mask
        inside = Path(pts).contains_points(np.c_[jj.ravel(), ii.ravel()]).reshape(jj.shape)
    elif geom.shape == "rectangle":
        inside = (jj >= x0) & (jj <= x1) & (ii >= y0) & (ii <= y1)
    elif geom.shape == "ellipse":
        rx, ry = max(p["rx"], 1e-9), max(p["ry"], 1e-9)
        inside = ((jj - p["cx"]) / rx) ** 2 + ((ii - p["cy"]) / ry) ** 2 <= 1.0
    elif geom.shape == "point":
        cj, ci = np.floor(p["x"]) + 0.5, np.floor(p["y"]) + 0.5
        r = float(p.get("radius", 0))
        inside = (jj - cj) ** 2 + (ii - ci) ** 2 <= r * r + 1e-9
    else:
        raise ValueError(geom.shape)
    mask[i0:i1, j0:j1] = inside
    return mask


# --------------------------------------------------------------------------- #
# Spectra
# --------------------------------------------------------------------------- #
# === Spectra =================================================================
@dataclass
class Spectrum:
    """Spectrum of one ROI: mean and SD per channel and number of pixels."""
    mean: np.ndarray          # (C,) mean intensity per channel
    sd: np.ndarray            # (C,) standard deviation across ROI pixels
    n_pixels: int


def roi_spectrum(data: np.ndarray, mask: np.ndarray) -> Spectrum:
    """Mean and SD of every channel over the pixels of the mask (NaN if the mask is empty)."""
    n = int(mask.sum())
    if n == 0:
        nan = np.full(data.shape[0], np.nan)
        return Spectrum(nan, nan.copy(), 0)
    vals = data[:, mask].astype(np.float64)                     # (C, n)
    sd = vals.std(axis=1, ddof=1) if n > 1 else np.zeros(data.shape[0])
    return Spectrum(vals.mean(axis=1), sd, n)


def tissue_mask(data: np.ndarray) -> np.ndarray:
    """Tissue pixels of a stack: Otsu threshold on log(1 + summed intensity).

    Same rule as the whole-image comparison maps; background (near-zero) pixels are excluded.
    """
    from .spectral_maps import otsu_threshold
    total = data.sum(axis=0, dtype=np.float64)
    threshold = float(np.expm1(otsu_threshold(np.log1p(total.ravel()))))
    return (total > threshold) & (total > 0)


def tissue_spectrum(data: np.ndarray, mask: Optional[np.ndarray] = None) -> Spectrum:
    """Mean and SD spectrum of all tissue pixels (computed in row blocks to limit memory)."""
    if mask is None:
        mask = tissue_mask(data)
    c = data.shape[0]
    s1 = np.zeros(c)
    s2 = np.zeros(c)
    n = 0
    for r0 in range(0, data.shape[1], 128):
        m = mask[r0:r0 + 128]
        if not m.any():
            continue
        v = data[:, r0:r0 + 128][:, m].astype(np.float64)
        s1 += v.sum(axis=1)
        s2 += (v * v).sum(axis=1)
        n += v.shape[1]
    if n == 0:
        nan = np.full(c, np.nan)
        return Spectrum(nan, nan.copy(), 0)
    mean = s1 / n
    var = np.maximum(s2 / n - mean ** 2, 0) * (n / (n - 1) if n > 1 else 0)
    return Spectrum(mean, np.sqrt(var), n)


# --------------------------------------------------------------------------- #
# Background estimation (subtracted from ROI spectra before normalisation)
# --------------------------------------------------------------------------- #
BACKGROUND_MODES = [
    "None",
    "Background ROI(s) (drawn)",
    "Auto: dimmest 1 % of the image",
    "Auto: ring around each ROI",
]


# === Automatic background (ring around an ROI, dimmest pixels) ===============
def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary dilation by ``radius`` pixels (alternating 4- and 8-neighbour steps: near-disc)."""
    out = mask.copy()
    for k in range(int(radius)):
        m = out.copy()
        out[1:] |= m[:-1]
        out[:-1] |= m[1:]
        out[:, 1:] |= m[:, :-1]
        out[:, :-1] |= m[:, 1:]
        if k % 2 == 1:                        # diagonal neighbours every other step
            out[1:, 1:] |= m[:-1, :-1]
            out[:-1, :-1] |= m[1:, 1:]
            out[1:, :-1] |= m[:-1, 1:]
            out[:-1, 1:] |= m[1:, :-1]
    return out


def ring_mask(mask: np.ndarray, gap: int = 2, width: int = 10,
              exclude: Optional[np.ndarray] = None) -> np.ndarray:
    """Annulus of pixels around an ROI: from ``gap`` to ``gap + width`` pixels outside its edge.

    The local-background ring used in fluorescence photometry: it measures the immediate
    surroundings of the structure without the operator choosing where.  ``exclude``
    (e.g. the other ROIs of the image) is removed from the ring.
    """
    out = np.zeros_like(mask, dtype=bool)
    if not mask.any():
        return out
    ii, jj = np.nonzero(mask)
    r = int(gap) + int(width) + 1
    i0, i1 = max(ii.min() - r, 0), min(ii.max() + r + 1, mask.shape[0])
    j0, j1 = max(jj.min() - r, 0), min(jj.max() + r + 1, mask.shape[1])
    crop = mask[i0:i1, j0:j1]
    inner = _dilate(crop, gap)
    outer = _dilate(crop, int(gap) + int(width))
    out[i0:i1, j0:j1] = outer & ~inner
    if exclude is not None:
        out &= ~exclude
    return out


def dark_pixels_mask(data: np.ndarray, fraction: float = 0.01) -> np.ndarray:
    """The dimmest ``fraction`` of the pixels (summed intensity): low-percentile background.

    Estimates the detector offset and stray light.  On the ENS stacks there is no empty
    area: even these pixels are faint tissue with the tissue's spectrum, so the estimate
    stays small (a few % of the tissue signal) and mostly removes a constant level.
    """
    total = data.sum(axis=0, dtype=np.float64)
    return total <= np.quantile(total, fraction)


# === Spectrum under the cursor ===============================================
def cursor_spectrum(data: np.ndarray, x: float, y: float, size: int = 1) -> Optional[np.ndarray]:
    """Mean spectrum of a size × size pixel square under the mouse cursor (None outside the image).
    """
    h, w = data.shape[1:]
    j, i = int(np.floor(x)), int(np.floor(y))
    if not (0 <= i < h and 0 <= j < w):
        return None
    r = max(0, (int(size) - 1) // 2)
    block = data[:, max(i - r, 0):i + r + 1, max(j - r, 0):j + r + 1].astype(np.float64)
    return block.reshape(block.shape[0], -1).mean(axis=1)


NORMALISATIONS = ["Maximum = 1", "None (mean intensity)", "Sum = 1", "Min–max (0–1)"]


# === Normalisation ===========================================================
def normalise(mean: np.ndarray, sd: Optional[np.ndarray], method: str):
    """Return (normalised mean, normalised SD) for display/export."""
    m = np.asarray(mean, dtype=float)
    s = None if sd is None else np.asarray(sd, dtype=float)
    if not np.isfinite(m).any():              # nothing inside the analysed range
        return m, s
    if method.startswith("Maximum"):
        k = np.nanmax(m)
        if not (np.isfinite(k) and k > 0):
            # Spectrum entirely <= 0 (e.g. background brighter than the ROI): dividing by its
            # (negative) maximum would flip and inflate the curve; scale by the largest
            # absolute value instead, which keeps the sign (minimum = -1).
            k = np.nanmax(np.abs(m))
        k = k if k and np.isfinite(k) and k != 0 else 1.0
        return m / k, (None if s is None else s / abs(k))
    if method.startswith("Sum"):
        k = np.nansum(m)
        if not (np.isfinite(k) and k > 0):
            k = np.nansum(np.abs(m))          # same safeguard: never divide by a negative sum
        k = k if k and np.isfinite(k) and k != 0 else 1.0
        return m / k, (None if s is None else s / abs(k))
    if method.startswith("Min"):
        lo, hi = np.nanmin(m), np.nanmax(m)
        k = (hi - lo) if hi > lo else 1.0
        return (m - lo) / k, (None if s is None else s / k)
    return m, s


def y_label(method: str) -> str:
    """Y-axis label matching the normalisation method."""
    return {
        "Maximum = 1": "Normalised intensity (max = 1)",
        "Sum = 1": "Normalised intensity (sum = 1)",
        "Min–max (0–1)": "Normalised intensity (min–max)",
    }.get(method, "Mean intensity (a.u.)")


# === Wavelength range and spectral descriptors ===============================
@dataclass
class Descriptors:
    """Shape descriptors of one spectrum (peak, interpolated peak, FWHM, centroid)."""
    peak_nm: float            # centre wavelength of the brightest channel
    peak_interp_nm: float     # sub-channel peak (3-point parabolic interpolation)
    fwhm_nm: float            # full width at half maximum (linear interpolation), NaN if open
    centroid_nm: float        # intensity-weighted mean wavelength


def range_mask(wl: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Channels whose centre lies in [lo, hi] nm (bounds in either order)."""
    lo, hi = min(lo, hi), max(lo, hi)
    w = np.asarray(wl, dtype=float)
    return (w >= lo - 1e-6) & (w <= hi + 1e-6)


def restrict(values: Optional[np.ndarray], keep: np.ndarray) -> Optional[np.ndarray]:
    """Copy of ``values`` with the channels outside ``keep`` set to NaN (ignored downstream)."""
    if values is None:
        return None
    out = np.asarray(values, dtype=float).copy()
    out[~keep] = np.nan
    return out


def descriptors(wl: np.ndarray, spec: np.ndarray) -> Descriptors:
    """Compute the peak wavelength, interpolated peak, FWHM and centroid of a spectrum (NaN values
    ignored).
    """
    wl = np.asarray(wl, dtype=float)
    y = np.asarray(spec, dtype=float)
    if not np.isfinite(y).any():
        return Descriptors(np.nan, np.nan, np.nan, np.nan)
    i = int(np.nanargmax(y))
    peak = float(wl[i])
    interp = peak
    if 0 < i < len(y) - 1:
        a, b, c = y[i - 1], y[i], y[i + 1]
        den = a - 2 * b + c
        if den != 0 and np.isfinite(den):
            delta = 0.5 * (a - c) / den
            if abs(delta) <= 1:
                step = (wl[i + 1] - wl[i - 1]) / 2.0
                interp = float(wl[i] + delta * step)
    # FWHM relative to the baseline-free maximum (spectrum assumed >= 0).
    half = y[i] / 2.0
    fwhm = np.nan
    left = right = None
    for k in range(i, 0, -1):
        if y[k - 1] < half <= y[k]:
            left = wl[k - 1] + (half - y[k - 1]) * (wl[k] - wl[k - 1]) / (y[k] - y[k - 1])
            break
    for k in range(i, len(y) - 1):
        if y[k + 1] < half <= y[k]:
            right = wl[k] + (y[k] - half) * (wl[k + 1] - wl[k]) / (y[k] - y[k + 1])
            break
    if left is not None and right is not None:
        fwhm = float(right - left)
    pos = np.clip(y, 0, None)
    centroid = float(np.nansum(pos * wl) / np.nansum(pos)) if np.nansum(pos) > 0 else np.nan
    return Descriptors(peak, interp, fwhm, centroid)
