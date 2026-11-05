"""Name normalisation helpers.

The Excel database refers to images as e.g. ``images/Lame_BK1245_EA5_01_99.jpg``
whereas the files delivered by the lab are named ``Lame BK1245 EA5 01_99.jpg``
(spaces instead of underscores, different letter case, no ``images/`` prefix).
Every name is therefore reduced to a canonical *key* before any comparison.
"""
from __future__ import annotations

import os
import re

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")

_EXT_RE = re.compile(r"\.(jpe?g|tiff?|png|bmp)$", re.IGNORECASE)
_SEP_RE = re.compile(r"[\s_\-]+")


def image_key(name: object) -> str:
    """Return the canonical key of an image name or path.

    Steps: keep the base name only (both / and \\ separators are handled),
    drop the extension, collapse runs of spaces, underscores and hyphens into a
    single underscore, lower-case the result.

    >>> image_key("images/Lame_BK1245_EA5_01_99.jpg")
    'lame_bk1245_ea5_01_99'
    >>> image_key(r"C:\\data\\Lame BK1245 EA5 01_99.jpg")
    'lame_bk1245_ea5_01_99'
    """
    text = str(name).replace("\\", "/").strip()
    base = os.path.basename(text)
    base = _EXT_RE.sub("", base.strip())
    return _SEP_RE.sub("_", base).strip("_").lower()


def spectral_key(filename: object) -> str:
    """Return the image key that a spectral stack file refers to.

    ``Spectre Lame BK1238 EA5 03_6-slice14.tif`` -> ``lame_bk1238_ea5_03_6``.
    The leading 'Spectre'/'Spectrum' word and the trailing '-sliceN' suffix
    added when the optical section was extracted in ImageJ are removed.
    """
    key = image_key(filename)
    key = re.sub(r"^(spectre|spectres|spectrum|spectral|spectra)_", "", key)
    key = re.sub(r"_slice_?\d+$", "", key)
    return key


def strip_prefix(key: str) -> str:
    """Key without the leading 'lame_' word (used as a fallback match)."""
    return re.sub(r"^lame_", "", key)


def slide_id(key: str) -> str:
    """Infer the slide identifier from an image key.

    Two naming schemes exist in the dataset:
      * ``lame_bk1245_ea5_01_99`` / ``bk1210_ea5_02_96`` / ``lame_bk_1192_ea5_02_32``
        -> ``BK1245 EA5``
      * ``lame_424c10`` / ``lame_456_1`` / ``lame_4346``
        -> ``424C`` / ``456`` / ``434`` (slide numbers have three digits)
    Returns 'Unknown' when neither pattern applies.
    """
    m = re.search(r"bk_?(\d{3,5})_?ea_?(\d)", key)
    if m:
        return f"BK{m.group(1)} EA{m.group(2)}"
    m = re.search(r"lame_?(\d{3})([a-z]?)", key)
    if m:
        return (m.group(1) + m.group(2)).upper()
    return "Unknown"
