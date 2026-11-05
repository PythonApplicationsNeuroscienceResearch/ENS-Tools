"""Asynchronous thumbnail generation with an on-disk cache.

Decoding ~800 full-resolution images is slow, so thumbnails are produced in a
thread pool and cached as small JPEG files (keyed by path, size and modification
time) in the user's cache folder.  The second start-up is therefore immediate.
"""
from __future__ import annotations

import hashlib
import os
from typing import Dict, Set

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QStandardPaths, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from .. import imagefiles
from .common import pil_to_qimage

THUMB_PX = 256


# === Thumbnail cache on disk =================================================
def cache_dir() -> str:
    """Folder where generated thumbnails are cached between sessions."""
    base = QStandardPaths.writableLocation(QStandardPaths.CacheLocation) or os.path.expanduser("~")
    path = os.path.join(base, "ens_thumbnails")
    os.makedirs(path, exist_ok=True)
    return path


def _cache_file(path: str) -> str:
    """Cache file of one image; its name depends on the file signature, so an edited image gets a
    new thumbnail.
    """
    sig = f"{imagefiles.signature(path)}|{THUMB_PX}"
    return os.path.join(cache_dir(), hashlib.sha1(sig.encode("utf-8")).hexdigest() + ".jpg")


def to_8bit(im: Image.Image) -> Image.Image:
    """8-bit RGB copy of any image.

    16-bit and floating-point images (e.g. the raw TIFF exports, mode 'I;16B')
    cannot be resized by Pillow: they are first contrast-stretched between the
    0.5th and 99.8th percentiles, as for the full-size display.
    """
    if im.mode in ("RGB", "L", "RGBA", "P", "LA", "1", "CMYK", "YCbCr"):
        return im.convert("RGB")
    a = np.asarray(im, dtype=np.float32)
    if a.ndim == 3:
        a = a[..., :3].mean(axis=2)
    lo, hi = np.percentile(a, (0.5, 99.8))
    a = np.clip((a - lo) / max(float(hi - lo), 1e-9), 0, 1) * 255
    return Image.fromarray(a.astype(np.uint8)).convert("RGB")


def make_thumbnail(path: str) -> QImage:
    """Return the thumbnail of an image, from the cache if present, otherwise generated and cached.
    """
    cached = _cache_file(path)
    if os.path.isfile(cached):
        img = QImage(cached)
        if not img.isNull():
            return img
    with Image.open(imagefiles.source(path)) as im:
        if im.format == "JPEG":
            im.draft("RGB", (THUMB_PX, THUMB_PX))  # fast DCT-domain downscaling
        im = to_8bit(im)
        im.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
        qimg = pil_to_qimage(im)
        try:
            im.convert("RGB").save(cached, "JPEG", quality=88)
        except OSError:
            pass
    return qimg


# === Background generation ===================================================
class _Signals(QObject):
    """Qt signal emitted by a worker thread when a thumbnail is ready."""
    done = Signal(str, QImage)


class _Job(QRunnable):
    """Background task that generates one thumbnail."""
    def __init__(self, key: str, path: str, signals: _Signals):
        """Remember which image to process and where to report the result."""
        super().__init__()
        self.key, self.path, self.signals = key, path, signals

    def run(self) -> None:  # executed in a worker thread
        """Generate the thumbnail in a worker thread and emit it (an empty image on failure)."""
        try:
            img = make_thumbnail(self.path)
        except Exception:
            img = QImage()
        try:
            self.signals.done.emit(self.key, img)
        except RuntimeError:        # window already closed: nothing to update
            pass


class ThumbnailProvider(QObject):
    """Returns cached pixmaps immediately and schedules missing ones."""
    ready = Signal(str)

    def __init__(self, parent=None):
        """In-memory thumbnail store fed by a thread pool, so the interface never waits for disk
        access.
        """
        super().__init__(parent)
        self._pix: Dict[str, QPixmap] = {}
        self._pending: Set[str] = set()
        self._failed: Set[str] = set()
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(2, min(6, (os.cpu_count() or 4) - 1)))

    def get(self, key: str, path: str):
        """Return the thumbnail if ready; otherwise start generating it and return None."""
        pm = self._pix.get(key)
        if pm is not None:
            return pm
        if path and key not in self._pending and key not in self._failed:
            self._pending.add(key)
            self._pool.start(_Job(key, path, self._signals))
        return None

    def failed(self, key: str) -> bool:
        """True if the thumbnail of this image could not be generated."""
        return key in self._failed

    def shutdown(self) -> None:
        """Drop queued thumbnails and wait briefly for running ones (called on close)."""
        self._pool.clear()
        self._pool.waitForDone(3000)

    def clear(self) -> None:
        """Forget all thumbnails (e.g. after reloading the data)."""
        self._pix.clear()
        self._failed.clear()

    def _on_done(self, key: str, img: QImage) -> None:
        """Store a finished thumbnail (or record the failure) and notify the views."""
        self._pending.discard(key)
        if img.isNull():
            self._failed.add(key)
        else:
            self._pix[key] = QPixmap.fromImage(img)
        self.ready.emit(key)
