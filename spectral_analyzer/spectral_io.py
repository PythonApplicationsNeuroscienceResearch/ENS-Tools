"""Loading of spectral (lambda) stacks.

Supported input: any TIFF readable by ``tifffile`` (ImageJ hyperstacks,
OME-TIFF, Zeiss LSM).  The stack is returned as a ``(C, Y, X)`` array, C being
the spectral channels.

Wavelengths are read from the file when available (OME ``EmissionWavelength``,
LSM channel wavelengths, or 'nnn nm' in ImageJ slice labels).  The stacks
exported from ImageJ by the lab carry no wavelength information; for those the
user-defined calibration (first channel centre + channel width) is applied and
flagged as such.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Union

import numpy as np
import tifffile


# === Wavelength calibration ==================================================
@dataclass
class Calibration:
    """Linear (start, step) or explicit list of channel centre wavelengths (nm)."""
    # Default = acquisition settings confirmed by the lab (D. Hazart, November 2026):
    # excitation at 405 nm, emission collected from 414 nm with a 9 nm increment
    # between detection channels (32 channels: 414-693 nm).
    start_nm: float = 414.0
    step_nm: float = 9.0
    values: Optional[List[float]] = None   # explicit list overrides start/step
    verified: bool = True                  # the default above is confirmed by the lab

    def wavelengths(self, n_channels: int) -> np.ndarray:
        """Centre wavelength of every channel (explicit list if given, else start + step × index).
        """
        if self.values and len(self.values) == n_channels:
            return np.asarray(self.values, dtype=float)
        return self.start_nm + self.step_nm * np.arange(n_channels, dtype=float)

    def describe(self, n_channels: int) -> str:
        """Text description of the calibration, written in the exports."""
        wl = self.wavelengths(n_channels)
        if self.values and len(self.values) == n_channels:
            return f"user list, {wl[0]:.1f}–{wl[-1]:.1f} nm"
        return (f"linear: {self.start_nm:.1f} nm + {self.step_nm:.2f} nm × (channel − 1), "
                f"{wl[0]:.1f}–{wl[-1]:.1f} nm")


# === Loaded stack ============================================================
@dataclass
class SpectralStack:
    """One loaded spectral stack: pixel data (channels × Y × X), wavelengths, pixel size and
    metadata.
    """
    path: str
    data: np.ndarray                      # (C, Y, X), original dtype
    wavelengths: np.ndarray               # (C,) channel centres in nm
    wl_source: str                        # 'file' or 'calibration'
    pixel_size_um: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        """File name without extension."""
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def n_channels(self) -> int:
        """Number of spectral channels."""
        return int(self.data.shape[0])

    @property
    def shape_yx(self):
        """Image size (height, width) in pixels."""
        return self.data.shape[1], self.data.shape[2]


class StackFormatError(ValueError):
    """Raised when a file cannot be interpreted as a spectral stack."""
    pass


ZChoice = Union[int, str]                 # z index, or 'max' / 'mean'


# === Reading the metadata ====================================================
def _wavelengths_from_file(tf: tifffile.TiffFile, n: int) -> Optional[np.ndarray]:
    # 1) OME-XML
    """Read the channel wavelengths stored in the file (OME-XML, Zeiss LSM or ImageJ metadata), if
    any.
    """
    try:
        if tf.is_ome and tf.ome_metadata:
            root = ET.fromstring(tf.ome_metadata)
            vals = []
            for el in root.iter():
                if el.tag.endswith("Channel"):
                    v = el.get("EmissionWavelength")
                    if v is not None:
                        vals.append(float(v))
            if len(vals) == n:
                return np.asarray(vals, dtype=float)
    except Exception:  # noqa: BLE001 - metadata is optional
        pass
    # 2) Zeiss LSM
    try:
        if tf.is_lsm:
            meta = tf.lsm_metadata or {}
            cw = meta.get("ChannelWavelength")
            if cw is not None:
                arr = np.asarray(cw, dtype=float)
                if arr.ndim == 2 and arr.shape[0] == n:
                    centres = arr.mean(axis=1)
                    if np.nanmax(centres) < 1e-3:      # stored in metres
                        centres = centres * 1e9
                    if 250 < np.nanmin(centres) and np.nanmax(centres) < 1200:
                        return centres
    except Exception:  # noqa: BLE001
        pass
    # 3) ImageJ slice labels containing 'nnn nm'
    try:
        ij = tf.imagej_metadata or {}
        labels = ij.get("Labels")
        if labels and len(labels) >= n:
            vals = []
            for lab in labels[:n]:
                m = re.search(r"(\d{3}(?:\.\d+)?)\s*nm", str(lab))
                if not m:
                    break
                vals.append(float(m.group(1)))
            if len(vals) == n:
                return np.asarray(vals, dtype=float)
    except Exception:  # noqa: BLE001
        pass
    return None


def _pixel_size(tf: tifffile.TiffFile) -> Optional[float]:
    """Pixel size in micrometres, if recorded."""
    try:
        if tf.is_ome and tf.ome_metadata:
            root = ET.fromstring(tf.ome_metadata)
            for el in root.iter():
                if el.tag.endswith("Pixels") and el.get("PhysicalSizeX"):
                    unit = el.get("PhysicalSizeXUnit", "µm")
                    v = float(el.get("PhysicalSizeX"))
                    return v * {"nm": 1e-3, "mm": 1e3, "m": 1e6}.get(unit, 1.0)
        if tf.is_lsm:
            v = (tf.lsm_metadata or {}).get("VoxelSizeX")
            if v:
                return float(v) * 1e6
        page = tf.pages[0]
        res = page.tags.get("XResolution")
        if res is not None:
            num, den = res.value
            px_per_unit = num / den if den else 0
            if px_per_unit > 0:
                unit = (tf.imagej_metadata or {}).get("unit", "") if tf.is_imagej else ""
                unit = str(unit).lower()
                if unit in ("micron", "microns", "um", "µm", "\\u00b5m"):
                    return 1.0 / px_per_unit
                ru = page.tags.get("ResolutionUnit")
                ru = ru.value if ru is not None else 1
                if int(ru) == 3:        # centimetre
                    return 1e4 / px_per_unit
                if int(ru) == 2 and px_per_unit > 1000:   # inch, microscopy-scale only
                    return 25400.0 / px_per_unit
    except Exception:  # noqa: BLE001
        pass
    return None


# === Loading =================================================================
def load_stack(path: str, calibration: Calibration,
               choose_z: Optional[Callable[[int], Optional[ZChoice]]] = None) -> SpectralStack:
    """Read a spectral stack as (C, Y, X).

    ``choose_z(n)`` is called when the file also contains a Z axis; it must
    return a 0-based plane index, 'max' (maximum projection) or 'mean', or None
    to cancel.
    """
    notes: List[str] = []
    with tifffile.TiffFile(path) as tf:
        series = tf.series[0]
        arr = series.asarray()
        axes = series.axes.upper()
        # Drop singleton axes.
        keep = [i for i, s in enumerate(arr.shape) if s > 1 or axes[i] in "YX"]
        arr = arr.reshape([arr.shape[i] for i in keep])
        axes = "".join(axes[i] for i in keep)
        if "Y" not in axes or "X" not in axes:
            raise StackFormatError(f"Cannot find the image plane (axes '{axes}').")
        if "S" in axes and arr.shape[axes.index("S")] in (3, 4) and len(axes) == 3:
            raise StackFormatError("This is an RGB image, not a spectral stack.")
        others = [a for a in axes if a not in "YX"]
        if not others:
            raise StackFormatError("Single-plane image: no spectral axis.")
        if len(others) == 1:
            c_axis = others[0]
        elif "C" in others:
            c_axis = "C"
        elif "S" in others:
            c_axis = "S"
        else:
            # Pick the axis with the most planes as the spectral one.
            c_axis = max(others, key=lambda a: arr.shape[axes.index(a)])
        extra = [a for a in others if a != c_axis]
        if len(extra) > 1:
            raise StackFormatError(f"Unsupported dimensions '{axes}' (only one extra axis allowed).")
        if extra:
            z = extra[0]
            nz = arr.shape[axes.index(z)]
            choice = choose_z(nz) if choose_z else "max"
            if choice is None:
                raise StackFormatError("Loading cancelled.")
            zi = axes.index(z)
            if choice == "max":
                arr = arr.max(axis=zi)
                notes.append(f"maximum projection over {nz} planes ({z})")
            elif choice == "mean":
                arr = arr.mean(axis=zi)
                notes.append(f"mean projection over {nz} planes ({z})")
            else:
                arr = np.take(arr, int(choice), axis=zi)
                notes.append(f"plane {int(choice) + 1}/{nz} ({z})")
            axes = axes.replace(z, "")
        order = [axes.index(c_axis), axes.index("Y"), axes.index("X")]
        data = np.ascontiguousarray(np.transpose(arr, order))
        n = data.shape[0]
        wl = _wavelengths_from_file(tf, n)
        source = "file"
        if wl is None:
            wl = calibration.wavelengths(n)
            source = "calibration"
        px = _pixel_size(tf)
    if n < 2:
        raise StackFormatError("The stack has a single channel.")
    return SpectralStack(path=path, data=data, wavelengths=np.asarray(wl, dtype=float),
                         wl_source=source, pixel_size_um=px, notes=notes)


# === Checks ==================================================================
def subsample_for_stats(data: np.ndarray, max_values: int = 2_000_000) -> np.ndarray:
    """Regularly subsampled view of the stack, used to compute statistics quickly."""
    step = max(1, int(np.ceil(np.sqrt(data.size / max_values))))
    return data[:, ::step, ::step]


def sanity_report(stack: SpectralStack) -> Sequence[str]:
    """Short notes on saturation / clipping that affect spectral shape."""
    out = []
    d = stack.data
    if np.issubdtype(d.dtype, np.integer):
        top = np.iinfo(d.dtype).max
        sat = float((subsample_for_stats(d) >= top).mean())
        if sat > 0.0005:
            out.append(f"{sat * 100:.2f}% of sampled values are saturated ({top}); "
                       "saturated pixels distort the spectrum.")
    return out
