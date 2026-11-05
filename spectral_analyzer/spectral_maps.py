"""Whole-image spectral comparison of two stacks (no ROI needed).

The two images are not registered (different tissue sections), so they cannot
be compared pixel by pixel.  Instead every pixel of each image is compared with
the *spectral distribution* of a reference image B:

* the stacks are binned spatially (``b`` × ``b`` pixels) to raise the
  signal-to-noise ratio of each spectrum;
* background is excluded with Otsu's threshold on the log of the summed
  intensity (or a user-defined percentile);
* each tissue pixel is reduced to one number describing the *shape* of its
  spectrum (independent of brightness):

  - **spectral centroid** (nm): intensity-weighted mean wavelength;
    higher = red-shifted emission;
  - **spectral angle to B** (degrees): angle between the pixel spectrum and the
    mean spectral shape of the tissue of B; 0° = same shape.

* a pixel of A is called *atypical* when its value lies outside the range
  covered by 95 % of the tissue pixels of B **of similar brightness**
  (centroid: outside the 2.5–97.5th percentiles; angle: above the 95th
  percentile).  Brightness matters because dim pixels have noisier spectra:
  on the lab stacks the spectral angle of B's own pixels falls from about 7°
  in the dimmest decile to about 5° in the brightest, whereas the centroid is
  stable.  The tissue pixels of B are therefore split into intensity strata
  (deciles) and each pixel of A is judged against the stratum matching its
  summed intensity (limits interpolated between strata), so that a dimmer
  image is not called 'different' merely because it is noisier.  Pixels of A
  dimmer than the dimmest stratum of B have no brightness-matched reference
  and are left *unclassified* (reported separately) rather than extrapolated; pixels brighter than B are judged
  against B's brightest stratum, which is conservative (they are less noisy).
  By construction about 5 % of the pixels of B are atypical, which is the
  baseline for the fraction in A (computed over its classified pixels).

Pixels are spatially correlated and belong to one image (one sample), so no
p-value is computed: the maps and distributions are descriptive, and a group
difference requires several images per group.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np

METRICS = {
    "centroid": "Spectral centroid (nm)",
    "angle": "Difference from reference B (spectral angle, °)",
}
BINNINGS = (1, 2, 4, 8, 16)


@dataclass
class PreparedImage:
    """Binned stack restricted to tissue pixels."""
    name: str
    binning: int
    shape: Tuple[int, int]                 # binned (rows, cols)
    fg: np.ndarray                         # (rows, cols) bool, tissue mask
    spectra: np.ndarray                    # (n_fg, C) float64, raw binned spectra of tissue pixels
    shapes: np.ndarray                     # (n_fg, C) spectra scaled to sum = 1
    threshold: float                       # summed-intensity threshold used (binned scale)
    fg_rule: str                           # description of the rule

    def to_map(self, values: np.ndarray) -> np.ndarray:
        """Put one value per tissue pixel back into a 2-D map (NaN outside the tissue)."""
        out = np.full(self.shape, np.nan, dtype=np.float64)
        out[self.fg] = values
        return out


@dataclass
class Comparison:
    """Result of comparing image A to image B: metric maps, reference ranges and summary numbers."""
    metric: str
    wavelengths: np.ndarray
    a: PreparedImage
    b: PreparedImage
    map_a: np.ndarray                      # (rows, cols), NaN outside tissue
    map_b: np.ndarray
    values_a: np.ndarray                   # tissue values
    values_b: np.ndarray
    atypical_a: np.ndarray                 # (rows, cols) bool
    atypical_b: np.ndarray
    unclassified_a: np.ndarray             # (rows, cols) bool: A tissue dimmer than all of B
    limits: Tuple[float, float]            # pooled B range (display only; lo may be -inf)
    n_strata: int                          # intensity strata used for 'atypical'
    mean_shape_a: np.ndarray               # (C,) mean spectral shape, max = 1
    mean_shape_b: np.ndarray
    mean_shape_atypical_a: Optional[np.ndarray]
    summary: Dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# === Preparation (binning, tissue mask) ======================================
def bin_stack(data: np.ndarray, b: int) -> np.ndarray:
    """(C, Y, X) -> (C, Y//b, X//b) block means (edges that do not fill a block are dropped)."""
    if b <= 1:
        return data.astype(np.float32, copy=False)
    c, y, x = data.shape
    yb, xb = y // b, x // b
    if yb == 0 or xb == 0:
        raise ValueError(f"Binning {b} is larger than the image ({y} × {x}).")
    d = data[:, :yb * b, :xb * b].reshape(c, yb, b, xb, b)
    return d.mean(axis=(2, 4), dtype=np.float32)


def otsu_threshold(values: np.ndarray, bins: int = 256) -> float:
    """Otsu's threshold of a 1-D sample."""
    v = values[np.isfinite(values)]
    if v.size == 0 or v.min() == v.max():
        return float(v.min()) if v.size else 0.0
    hist, edges = np.histogram(v, bins=bins)
    centres = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist).astype(float)
    w1 = w0[-1] - w0
    m = np.cumsum(hist * centres)
    mu0 = m / np.maximum(w0, 1)
    mu1 = (m[-1] - m) / np.maximum(w1, 1)
    between = w0 * w1 * (mu0 - mu1) ** 2
    return float(centres[int(np.argmax(between))])


def prepare(name: str, data: np.ndarray, binning: int = 4, auto: bool = True,
            exclude_pct: float = 30.0) -> PreparedImage:
    """Bin the stack and keep tissue pixels.

    ``auto``: Otsu threshold on log(1 + summed intensity); otherwise the dimmest
    ``exclude_pct`` % of the binned pixels are excluded.
    """
    binned = bin_stack(data, binning)
    total = binned.sum(axis=0, dtype=np.float64)
    if auto:
        t_log = otsu_threshold(np.log1p(total.ravel()))
        threshold = float(np.expm1(t_log))
        rule = f"automatic (Otsu), binning {binning}×{binning}"
    else:
        threshold = float(np.percentile(total, exclude_pct))
        rule = f"dimmest {exclude_pct:g} % excluded, binning {binning}×{binning}"
    fg = (total > threshold) & (total > 0)
    if fg.sum() < 10:
        raise ValueError(f"'{name}': fewer than 10 tissue pixels with this threshold.")
    spectra = binned[:, fg].T.astype(np.float64)                 # (n, C)
    shapes = spectra / spectra.sum(axis=1, keepdims=True)
    return PreparedImage(name=name, binning=binning, shape=total.shape, fg=fg, spectra=spectra,
                         shapes=shapes, threshold=threshold, fg_rule=rule)


# === Per-pixel spectral metrics ==============================================
def spectral_centroid(shapes: np.ndarray, wl: np.ndarray) -> np.ndarray:
    """Intensity-weighted mean wavelength of each normalised pixel spectrum."""
    return shapes @ np.asarray(wl, dtype=np.float64)


def spectral_angle(spectra: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Angle (degrees) between each spectrum (rows) and a reference spectrum."""
    ref = np.asarray(reference, dtype=np.float64)
    num = spectra @ ref
    den = np.linalg.norm(spectra, axis=1) * np.linalg.norm(ref)
    cos = np.clip(num / np.where(den > 0, den, np.nan), -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def _max1(v: np.ndarray) -> np.ndarray:
    """Scale a spectrum so that its maximum is 1."""
    m = np.nanmax(v)
    return v / m if m > 0 else v


# === Comparison of two images ================================================
def compare(a: PreparedImage, b: PreparedImage, wavelengths: np.ndarray,
            metric: str = "angle") -> Comparison:
    """Compare image A with the reference image B."""
    if metric not in METRICS:
        raise ValueError(f"Unknown metric '{metric}'.")
    wl = np.asarray(wavelengths, dtype=np.float64)
    if a.spectra.shape[1] != b.spectra.shape[1] or a.spectra.shape[1] != wl.size:
        raise ValueError("The two stacks do not have the same number of channels.")
    ref_shape = b.shapes.mean(axis=0)
    if metric == "centroid":
        va, vb = spectral_centroid(a.shapes, wl), spectral_centroid(b.shapes, wl)
        pct = (2.5, 97.5)
        lo, hi = np.percentile(vb, pct)
    else:
        va, vb = spectral_angle(a.spectra, ref_shape), spectral_angle(b.spectra, ref_shape)
        pct = (None, 95.0)
        lo, hi = -np.inf, float(np.percentile(vb, 95))
    # Intensity-matched limits (see module docstring): B is split into strata of
    # equal size along log intensity; the limits are interpolated linearly between
    # the stratum centres, so that each pixel is judged against B pixels of the
    # same brightness.  Pixels dimmer than the centre of the dimmest stratum have
    # no matched reference and stay unclassified.
    tot_a, tot_b = a.spectra.sum(axis=1), b.spectra.sum(axis=1)
    la, lb = np.log(tot_a), np.log(tot_b)
    n_strata = int(max(1, min(20, vb.size // 100)))
    groups = np.array_split(np.argsort(lb, kind="stable"), n_strata)
    centres = np.array([np.median(lb[g]) for g in groups])
    centres = np.maximum.accumulate(centres) + np.arange(n_strata) * 1e-9   # strictly increasing
    hi_k = np.array([np.percentile(vb[g], pct[1]) for g in groups])
    lo_k = (np.array([np.percentile(vb[g], pct[0]) for g in groups]) if pct[0] is not None
            else np.full(n_strata, -np.inf))

    def outside(v: np.ndarray, logi: np.ndarray) -> np.ndarray:
        """True for pixels whose value falls outside the reference range of B at their brightness.
        """
        hi_i = np.interp(logi, centres, hi_k)
        lo_i = np.interp(logi, centres, lo_k) if pct[0] is not None else -np.inf
        return (v < lo_i) | (v > hi_i)

    too_dim = la < centres[0]
    too_dim_b = lb < centres[0]
    atyp_a = outside(va, la) & ~too_dim
    atyp_b = outside(vb, lb) & ~too_dim_b
    n_classified = int((~too_dim).sum())
    pct_atyp_a = 100 * float(atyp_a.sum()) / n_classified if n_classified else float("nan")
    mean_a, mean_b = _max1(a.shapes.mean(axis=0)), _max1(ref_shape)
    mean_atyp = _max1(a.shapes[atyp_a].mean(axis=0)) if atyp_a.sum() >= 5 else None

    def _fg_map(img: PreparedImage, mask: np.ndarray) -> np.ndarray:
        """Map of a tissue-pixel mask back to the full image grid."""
        out = np.zeros(img.shape, dtype=bool)
        out[img.fg] = mask
        return out

    med_a, med_b = float(np.median(va)), float(np.median(vb))
    summary = {
        "Metric": METRICS[metric],
        "Image A": a.name,
        "Image B (reference)": b.name,
        "Tissue pixels A (binned)": int(va.size),
        "Tissue pixels B (binned)": int(vb.size),
        "Tissue rule": a.fg_rule,
        "Median A": round(med_a, 3),
        "Median B": round(med_b, 3),
        "Median difference (A − B)": round(med_a - med_b, 3),
        "IQR A": round(float(np.subtract(*np.percentile(va, (75, 25)))), 3),
        "IQR B": round(float(np.subtract(*np.percentile(vb, (75, 25)))), 3),
        "Atypical = outside 95 % of B (pooled range)": (
            f"< {lo:.2f} or > {hi:.2f}" if metric == "centroid" else f"> {hi:.2f}"),
        "Brightness matching": f"limits interpolated between {n_strata} intensity strata of B",
        "A pixels dimmer than all of B, unclassified (%)": round(100 * float(too_dim.mean()), 2),
        "A pixels brighter than all of B (%)": round(100 * float((tot_a > tot_b.max()).mean()), 2),
        "Atypical pixels in A (%)": round(pct_atyp_a, 2),
        "Atypical pixels in B (%) (baseline ≈ 5)": round(
            100 * float(atyp_b.sum()) / max(1, int((~too_dim_b).sum())), 2),
        "Overlap of the A and B distributions (0–1)": round(distribution_overlap(va, vb), 3),
    }
    return Comparison(metric=metric, wavelengths=wl, a=a, b=b, map_a=a.to_map(va),
                      map_b=b.to_map(vb), values_a=va, values_b=vb,
                      atypical_a=_fg_map(a, atyp_a), atypical_b=_fg_map(b, atyp_b),
                      unclassified_a=_fg_map(a, too_dim),
                      limits=(float(lo), float(hi)), n_strata=n_strata, mean_shape_a=mean_a, mean_shape_b=mean_b,
                      mean_shape_atypical_a=mean_atyp, summary=summary)


# === Summary and display helpers =============================================
def distribution_overlap(x: np.ndarray, y: np.ndarray, bins: int = 100) -> float:
    """Overlapping coefficient of two samples (shared area of their histograms, 0 to 1)."""
    lo = float(min(np.min(x), np.min(y)))
    hi = float(max(np.max(x), np.max(y)))
    if hi <= lo:
        return 1.0
    edges = np.linspace(lo, hi, bins + 1)
    px = np.histogram(x, edges)[0] / x.size
    py = np.histogram(y, edges)[0] / y.size
    return float(np.minimum(px, py).sum())


def display_limits(c: Comparison) -> Tuple[float, float]:
    """Shared colour scale of the two maps (robust to outliers)."""
    both = np.concatenate([c.values_a, c.values_b])
    if c.metric == "angle":
        return 0.0, float(np.percentile(both, 99))
    lo, hi = np.percentile(both, (1, 99))
    return float(lo), float(hi) if hi > lo else float(lo + 1)


def pixel_shape(img: PreparedImage, data_binned_row: int, data_binned_col: int) -> Optional[np.ndarray]:
    """Spectral shape (max = 1) of one binned tissue pixel, or None outside tissue."""
    r, c = data_binned_row, data_binned_col
    if not (0 <= r < img.shape[0] and 0 <= c < img.shape[1]) or not img.fg[r, c]:
        return None
    idx = int(np.count_nonzero(img.fg.ravel()[: r * img.shape[1] + c]))
    return _max1(img.shapes[idx])
