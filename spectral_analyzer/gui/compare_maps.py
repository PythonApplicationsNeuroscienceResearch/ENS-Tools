"""Bonus window: whole-image spectral comparison maps of two stacks.

The numerical work is done in :mod:`spectral_analyzer.spectral_maps`; this
module only provides the window and the figure.  It does not interact with the
ROI workflow of the main window, which remains the main analysis.
"""
from __future__ import annotations

import os
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import tifffile
from matplotlib import colormaps, patheffects
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QScrollArea, QSplitter, QVBoxLayout,
                               QWidget)

from .. import spectral_maps as sm
from ..export import FIGURE_FORMATS, save_figure
from ..plotting import DARK
from ..spectral_io import SpectralStack
from .style import fit_to_screen

ATYPICAL_COLOR = "#39ff14"      # lime: visible on both the diverging and the magma maps
UNCLASSIFIED_COLOR = "#8c939e"  # grey: A pixels dimmer than all of B (no matched reference)
COLOR_A, COLOR_B, COLOR_ATYP = "#ff924c", "#4cc9f0", "#39d353"
COLOR_ATYP_PRINT = "#1a9e3f"

HELP = (
    "<b>What this shows.</b> Each tissue pixel (after spatial binning) is reduced to one number "
    "describing the <i>shape</i> of its spectrum, independent of brightness. Both maps use the "
    "same colour scale. The images are not aligned, so A is compared with the whole "
    "<i>distribution</i> of B, not pixel to pixel.<br>"
    "<b>Spectral centroid</b>: mean emission wavelength (red = red-shifted compared with the "
    "median of B, blue = blue-shifted).<br>"
    "<b>Difference from B</b>: spectral angle between each pixel and the mean spectrum of B "
    "(0° = same shape).<br>"
    "<b>Atypical pixels</b> (green): outside the range of 95 % of the pixels of B of similar "
    "brightness (about 5 % of B by construction). Grey: pixels of A dimmer than every pixel "
    "of B, left unclassified (no reference of similar brightness).<br>"
    "Click a map to plot the spectrum of that pixel. Pixels are not independent: no p-value is "
    "given; compare several images per group before concluding.")


def _pct(v: float) -> str:
    """Percentage as text, or an explanation when it cannot be computed."""
    return "n/a (A dimmer than all of B)" if not np.isfinite(v) else f"{v:.1f} %"


def _overlay(mask: np.ndarray, color: str, alpha: float) -> np.ndarray:
    """Transparent coloured RGBA layer marking the pixels of a mask."""
    from matplotlib.colors import to_rgb
    rgba = np.zeros(mask.shape + (4,), dtype=np.float32)
    rgba[..., :3] = to_rgb(color)
    rgba[..., 3] = mask.astype(np.float32) * alpha
    return rgba


def draw_comparison(fig, c: sm.Comparison, *, dark: bool, show_atypical: bool = True,
                    picked: Optional[Tuple[str, int, int]] = None, font_size: float = 9.0,
                    pixel_um: Optional[float] = None) -> Dict[str, object]:
    """Draw the two maps, the distributions and the mean spectra on ``fig``.

    Returns the axes of the two maps ({'A': ax, 'B': ax}) for mouse picking.
    """
    fg = DARK["fg"] if dark else "#222222"
    bg = DARK["bg"] if dark else "white"
    grid = DARK["grid"] if dark else "#bbbbbb"
    c_atyp = COLOR_ATYP if dark else COLOR_ATYP_PRINT
    fig.clear()
    fig.set_facecolor(bg)
    outer = fig.add_gridspec(2, 1, height_ratios=[1.6, 1], hspace=0.32, left=0.07, right=0.93,
                             top=0.88, bottom=0.08)
    top = outer[0].subgridspec(1, 3, width_ratios=[1, 1, 0.045], wspace=0.1)
    bottom = outer[1].subgridspec(1, 2, wspace=0.3)
    ax_a, ax_b, cax = fig.add_subplot(top[0]), fig.add_subplot(top[1]), fig.add_subplot(top[2])
    ax_h, ax_s = fig.add_subplot(bottom[0]), fig.add_subplot(bottom[1])
    for ax in (ax_a, ax_b, cax, ax_h, ax_s):
        ax.set_facecolor(bg)
        ax.tick_params(colors=fg, labelsize=font_size - 1)
        for s in ax.spines.values():
            s.set_color(grid)

    lo, hi = sm.display_limits(c)
    if c.metric == "centroid":
        centre = float(np.median(c.values_b))
        half = max(centre - lo, hi - centre, 1e-6)
        norm = TwoSlopeNorm(vcenter=centre, vmin=centre - half, vmax=centre + half)
        cmap = colormaps["RdBu_r"].copy()
        cbar_label = "Spectral centroid (nm)"
    else:
        norm = Normalize(vmin=lo, vmax=hi)
        cmap = colormaps["magma"].copy()
        cbar_label = "Spectral angle to mean spectrum of B (°)"
    cmap.set_bad("#3a3f47" if dark else "#d9d9d9")

    im = None
    for ax, m, atyp, title, key in ((ax_a, c.map_a, c.atypical_a, "A", "A"),
                                     (ax_b, c.map_b, c.atypical_b, "B (reference)", "B")):
        img = c.a if key == "A" else c.b
        im = ax.imshow(m, cmap=cmap, norm=norm, interpolation="nearest")
        if show_atypical:
            ax.imshow(_overlay(atyp, ATYPICAL_COLOR, 0.6), interpolation="nearest")
            if key == "A" and c.unclassified_a.any():
                ax.imshow(_overlay(c.unclassified_a, UNCLASSIFIED_COLOR, 0.9),
                          interpolation="nearest")
        name = img.name if len(img.name) <= 38 else img.name[:35] + "…"
        if key == "A":
            sub = f"atypical {_pct(c.summary['Atypical pixels in A (%)'])}"
            if c.unclassified_a.any():
                sub += (f" · too dim to classify "
                        f"{c.summary['A pixels dimmer than all of B, unclassified (%)']:.1f} %")
        else:
            sub = f"atypical {100 * float(atyp[img.fg].mean()):.1f} % (baseline)"
        ax.set_title(f"{title}: {name}\n{sub}", color=fg, fontsize=font_size)
        ax.set_xticks([])
        ax.set_yticks([])
        if picked and picked[0] == key:
            ax.plot(picked[2], picked[1], marker="+", ms=14, mew=2.2, color="#ffffff" if dark
                    else "#000000")
        if pixel_um:
            # 50 µm scale bar (binned pixels)
            bar_px = 50.0 / (pixel_um * img.binning)
            if bar_px < img.shape[1] * 0.5:
                y = img.shape[0] * 0.95
                x0 = img.shape[1] * 0.04
                halo = [patheffects.withStroke(linewidth=1.5, foreground="black")]
                ax.plot([x0, x0 + bar_px], [y, y], color="white", lw=3, solid_capstyle="butt",
                        path_effects=[patheffects.withStroke(linewidth=4.5, foreground="black")])
                ax.text(x0 + bar_px / 2, y - img.shape[0] * 0.02, "50 µm", color="white",
                        ha="center", va="bottom", fontsize=font_size - 1, path_effects=halo)
    cb = fig.colorbar(im, cax=cax)
    cb.set_label(cbar_label, color=fg, fontsize=font_size - 1)
    cb.ax.tick_params(colors=fg, labelsize=font_size - 1)
    cb.outline.set_edgecolor(grid)

    # Distributions (density, shared bins).
    both = np.concatenate([c.values_a, c.values_b])
    b_lo, b_hi = np.percentile(both, (0.5, 99.5))
    if b_hi <= b_lo:
        b_hi = b_lo + 1
    bins = np.linspace(b_lo, b_hi, 80)
    ax_h.hist(c.values_b, bins=bins, density=True, histtype="stepfilled", alpha=0.35,
              color=COLOR_B, label="B (reference)")
    ax_h.hist(c.values_a, bins=bins, density=True, histtype="step", lw=1.6, color=COLOR_A,
              label="A")
    for v in c.limits:
        if np.isfinite(v):
            ax_h.axvline(v, color=c_atyp, lw=1, ls="--")
    ax_h.set_xlabel(cbar_label, color=fg, fontsize=font_size)
    ax_h.set_ylabel("Density of tissue pixels", color=fg, fontsize=font_size)
    ax_h.set_title(f"Distributions (overlap {c.summary['Overlap of the A and B distributions (0–1)']})",
                   color=fg, fontsize=font_size)
    leg = ax_h.legend(fontsize=font_size - 1.5, frameon=True)
    leg.get_frame().set_facecolor(DARK["legend"] if dark else "white")
    for t in leg.get_texts():
        t.set_color(fg)

    # Mean spectral shapes (max = 1).
    wl = c.wavelengths
    ax_s.plot(wl, c.mean_shape_b, color=COLOR_B, lw=1.8, label="B, all tissue")
    ax_s.plot(wl, c.mean_shape_a, color=COLOR_A, lw=1.8, label="A, all tissue")
    if c.mean_shape_atypical_a is not None:
        ax_s.plot(wl, c.mean_shape_atypical_a, color=c_atyp, lw=1.6, ls="--",
                  label="A, atypical pixels")
    if picked:
        img = c.a if picked[0] == "A" else c.b
        shape = sm.pixel_shape(img, picked[1], picked[2])
        if shape is not None:
            ax_s.plot(wl, shape, color=fg, lw=1.1, ls=":", marker="o", ms=2.5,
                      label=f"clicked pixel ({picked[0]})")
    ax_s.set_xlabel("Wavelength (nm)", color=fg, fontsize=font_size)
    ax_s.set_ylabel("Normalised intensity (max = 1)", color=fg, fontsize=font_size)
    ax_s.set_title("Mean spectra", color=fg, fontsize=font_size)
    ax_s.grid(True, color=grid, alpha=0.35, lw=0.6)
    ax_s.set_ylim(bottom=0)
    leg = ax_s.legend(fontsize=font_size - 1.5, frameon=True)
    leg.get_frame().set_facecolor(DARK["legend"] if dark else "white")
    for t in leg.get_texts():
        t.set_color(fg)
    fig.suptitle(sm.METRICS[c.metric], color=fg, fontsize=font_size + 1, fontweight="bold",
                 y=0.995)
    return {"A": ax_a, "B": ax_b}


class SpectralMapsWindow(QMainWindow):
    """Bonus tool: where do two images differ spectrally?"""

    def __init__(self, stacks_provider: Callable[[], Dict[str, SpectralStack]],
                 short_name: Callable[[str], str], default_dir: Callable[[str], str],
                 parent=None):
        """Bonus window comparing two whole images pixel by pixel; stacks are taken from the main
        window.
        """
        super().__init__(parent)
        self.setWindowTitle("Spectral difference maps (bonus): whole-image comparison")
        fit_to_screen(self, 1450, 900)
        self._stacks = stacks_provider
        self._short = short_name
        self._default_dir = default_dir
        self._prepared: Dict[tuple, sm.PreparedImage] = {}
        self.comparison: Optional[sm.Comparison] = None
        self._axes: Dict[str, object] = {}
        self._picked: Optional[Tuple[str, int, int]] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.compute)
        self._build()
        self.refresh_images()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        """Build the side panel (images, metric, binning, tissue mask, thresholds) and the figure.
        """
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(6, 6, 6, 6)

        box = QGroupBox("Images")
        form = QFormLayout(box)
        self.img_a = QComboBox()
        self.img_b = QComboBox()
        for cb in (self.img_a, self.img_b):
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(18)
        form.addRow("Image A", self.img_a)
        form.addRow("Reference B", self.img_b)
        swap = QPushButton("⇄ Swap A and B")
        swap.clicked.connect(self._swap)
        form.addRow(swap)
        sl.addWidget(box)

        box = QGroupBox("Settings")
        form = QFormLayout(box)
        self.metric_box = QComboBox()
        for key, label in sm.METRICS.items():
            self.metric_box.addItem(label, key)
        self.binning = QComboBox()
        for b in sm.BINNINGS:
            self.binning.addItem(f"{b} × {b} px", b)
        self.binning.setCurrentIndex(sm.BINNINGS.index(4))
        self.binning.setToolTip("Pixels averaged together before comparing spectra: more binning "
                                "= less noise but coarser maps. Compare images at the same binning.")
        self.auto_fg = QCheckBox("Automatic tissue detection (Otsu)")
        self.auto_fg.setChecked(True)
        self.excl = QDoubleSpinBox()
        self.excl.setRange(0, 99)
        self.excl.setValue(30)
        self.excl.setSuffix(" % dimmest excluded")
        self.excl.setEnabled(False)
        self.auto_fg.toggled.connect(lambda on: self.excl.setEnabled(not on))
        self.show_atyp = QCheckBox("Highlight atypical pixels (green)")
        self.show_atyp.setChecked(False)
        self.show_atyp.setToolTip("Overlay, on the maps, the pixels outside the range of 95 % of "
                                  "the pixels of B of similar brightness (and, in grey, the pixels "
                                  "of A too dim to be classified).")
        form.addRow("Metric", self.metric_box)
        form.addRow("Binning", self.binning)
        form.addRow(self.auto_fg)
        form.addRow("Manual threshold", self.excl)
        form.addRow(self.show_atyp)
        sl.addWidget(box)

        box = QGroupBox("Summary")
        bl = QVBoxLayout(box)
        self.summary = QLabel("Open at least one stack in the main window.")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bl.addWidget(self.summary)
        sl.addWidget(box)

        hl = QHBoxLayout()
        b = QPushButton("Export figure…")
        b.clicked.connect(self.export_figure)
        hl.addWidget(b)
        b = QPushButton("Export data…")
        b.setToolTip("Summary and mean spectra (.xlsx) + the two maps as 32-bit TIFF")
        b.clicked.connect(self.export_data)
        hl.addWidget(b)
        sl.addLayout(hl)

        box = QGroupBox("How to read")
        bl = QVBoxLayout(box)
        lab = QLabel(HELP)
        lab.setWordWrap(True)
        lab.setStyleSheet("color: #9aa7ba;")
        bl.addWidget(lab)
        sl.addWidget(box)
        sl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        scroll.setMinimumWidth(360)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.fig = Figure(figsize=(9, 7), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.mpl_connect("button_press_event", self._clicked)
        self.info = QLabel(" ")
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.addWidget(self.canvas, 1)
        rl.addWidget(self.info)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(scroll)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([430, 1020])
        self.setCentralWidget(split)

        for w in (self.img_a, self.img_b, self.metric_box, self.binning):
            w.currentIndexChanged.connect(self._schedule)
        self.auto_fg.toggled.connect(self._schedule)
        self.excl.valueChanged.connect(self._schedule)
        self.show_atyp.toggled.connect(self._redraw)

    # ---------------------------------------------------------- images
    def refresh_images(self, preferred: Tuple[Optional[str], Optional[str]] = (None, None)) -> None:
        """Re-list the stacks open in the main window (keeps or sets the selection)."""
        stacks = self._stacks()
        cur = (preferred[0] or self.img_a.currentData(), preferred[1] or self.img_b.currentData())
        for cb, want in ((self.img_a, cur[0]), (self.img_b, cur[1])):
            cb.blockSignals(True)
            cb.clear()
            for path, st in stacks.items():
                cb.addItem(self._short(st.name), path)
            i = cb.findData(want)
            if i >= 0:
                cb.setCurrentIndex(i)
            cb.blockSignals(False)
        if self.img_b.currentData() == self.img_a.currentData() and self.img_b.count() > 1:
            self.img_b.setCurrentIndex(1 if self.img_a.currentIndex() == 0 else 0)
        alive = set(stacks)
        self._prepared = {k: v for k, v in self._prepared.items() if k[0] in alive}
        self._schedule()

    def _swap(self) -> None:
        """Exchange images A and B."""
        a, b = self.img_a.currentIndex(), self.img_b.currentIndex()
        for cb, i in ((self.img_a, b), (self.img_b, a)):
            cb.blockSignals(True)
            cb.setCurrentIndex(i)
            cb.blockSignals(False)
        self._schedule()

    def _schedule(self, *args) -> None:
        """Recompute shortly after a setting changes (one computation for several quick changes)."""
        self._timer.start()

    def _prepare(self, path: str) -> sm.PreparedImage:
        """Binned stack and tissue mask of one image, cached for the current settings."""
        key = (path, self.binning.currentData(), self.auto_fg.isChecked(),
               round(self.excl.value(), 3))
        if key not in self._prepared:
            st = self._stacks()[path]
            self._prepared[key] = sm.prepare(self._short(st.name), st.data, key[1], key[2], key[3])
        return self._prepared[key]

    # ---------------------------------------------------------- compute
    def compute(self) -> None:
        """Compare A to B with the chosen metric and redraw the maps."""
        stacks = self._stacks()
        pa, pb = self.img_a.currentData(), self.img_b.currentData()
        if not pa or not pb or pa not in stacks or pb not in stacks:
            self.comparison = None
            self.summary.setText("Open at least one stack in the main window, then choose A and B.")
            self.fig.clear()
            self.canvas.draw_idle()
            return
        sa, sb = stacks[pa], stacks[pb]
        if sa.n_channels != sb.n_channels or not np.allclose(sa.wavelengths, sb.wavelengths):
            self.comparison = None
            self.summary.setText("<span style='color:#ef6f78'>A and B do not have the same "
                                 "channels / wavelengths: they cannot be compared.</span>")
            self.fig.clear()
            self.canvas.draw_idle()
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.comparison = sm.compare(self._prepare(pa), self._prepare(pb), sa.wavelengths,
                                         self.metric_box.currentData())
        except ValueError as exc:
            QApplication.restoreOverrideCursor()
            self.comparison = None
            self.summary.setText(f"<span style='color:#ef6f78'>{exc}</span>")
            return
        QApplication.restoreOverrideCursor()
        self._picked = None
        self.info.setText(" ")
        self._show_summary(same=(pa == pb))
        self._redraw()

    def _show_summary(self, same: bool) -> None:
        """Write the summary numbers of the comparison under the figure."""
        s = self.comparison.summary
        unit = "nm" if self.comparison.metric == "centroid" else "°"
        dim = s["A pixels dimmer than all of B, unclassified (%)"]
        bright = s["A pixels brighter than all of B (%)"]
        rows = [
            ("Tissue pixels A / B", f"{s['Tissue pixels A (binned)']} / {s['Tissue pixels B (binned)']}"),
            ("Median A / B", f"{s['Median A']:.2f} / {s['Median B']:.2f} {unit}"),
            ("Median difference (A − B)", f"{s['Median difference (A − B)']:+.2f} {unit}"),
            ("Spread (IQR) A / B", f"{s['IQR A']:.2f} / {s['IQR B']:.2f} {unit}"),
            ("Distribution overlap", f"{s['Overlap of the A and B distributions (0–1)']:.2f}"
                                     " (1 = identical)"),
            ("Atypical pixels in A", f"<b>{_pct(s['Atypical pixels in A (%)'])}</b>"),
            ("Atypical in B (baseline)", f"{s['Atypical pixels in B (%) (baseline ≈ 5)']:.1f} %"),
            ("A too dim to classify (grey)", f"{dim:.1f} %"),
            ("A brighter than all of B", f"{bright:.1f} %"),
        ]
        html = "<table cellspacing='3'>" + "".join(
            f"<tr><td style='color:#9aa7ba'>{k}</td><td>{v}</td></tr>" for k, v in rows) + "</table>"
        if same:
            html += "<p style='color:#f0b45a'>A and B are the same image (self-comparison).</p>"
        self.summary.setText(html)

    def _redraw(self, *args) -> None:
        """Redraw the maps, histograms and mean spectra (e.g. after a display change)."""
        if self.comparison is None:
            return
        px = self._stacks().get(self.img_a.currentData())
        self._axes = draw_comparison(self.fig, self.comparison, dark=True,
                                     show_atypical=self.show_atyp.isChecked(), picked=self._picked,
                                     pixel_um=px.pixel_size_um if px else None)
        self.canvas.draw_idle()

    def _clicked(self, event) -> None:
        """Click on a map: show the value of that binned pixel and mark it."""
        if self.comparison is None or event.xdata is None:
            return
        for key, ax in self._axes.items():
            if event.inaxes is ax:
                img = self.comparison.a if key == "A" else self.comparison.b
                r, c = int(round(event.ydata)), int(round(event.xdata))
                if not (0 <= r < img.shape[0] and 0 <= c < img.shape[1]):
                    return
                m = self.comparison.map_a if key == "A" else self.comparison.map_b
                if not img.fg[r, c]:
                    self.info.setText(f"{key}: background pixel (excluded).")
                    return
                self._picked = (key, r, c)
                unit = "nm" if self.comparison.metric == "centroid" else "°"
                b = img.binning
                self.info.setText(f"{key}: binned pixel row {r}, col {c} (original x {c * b}–"
                                  f"{c * b + b - 1}, y {r * b}–{r * b + b - 1}): "
                                  f"{m[r, c]:.2f} {unit}")
                self._redraw()
                return

    # ----------------------------------------------------------- export
    def _base_name(self) -> str:
        """Default file name of the exports (metric and image names)."""
        c = self.comparison
        return f"spectral_maps_{c.metric}_{c.a.name}_vs_{c.b.name}".replace(" ", "_")

    def export_figure(self) -> None:
        """Save the whole figure (maps, histograms, spectra) as an image or PDF."""
        if self.comparison is None:
            return
        filters = ";;".join(f"{k} (*{v})" for k, v in FIGURE_FORMATS.items())
        path, flt = QFileDialog.getSaveFileName(self, "Save figure",
                                                self._default_dir(self._base_name() + ".tif"),
                                                filters)
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in FIGURE_FORMATS.values():
            ext = next((v for k, v in FIGURE_FORMATS.items() if flt.startswith(k)), ".tif")
            path += ext
        fig = Figure(figsize=(180 / 25.4, 150 / 25.4))
        px = self._stacks().get(self.img_a.currentData())
        draw_comparison(fig, self.comparison, dark=False, show_atypical=self.show_atyp.isChecked(),
                        picked=self._picked, font_size=8,
                        pixel_um=px.pixel_size_um if px else None)
        try:
            save_figure(fig, path, 300)
        except PermissionError:
            QMessageBox.warning(self, "Export", "The file is open in another program.")
            return
        self.info.setText(f"Figure saved: {path}")

    def export_data(self) -> None:
        """Save the summary, mean spectra and value distributions to an Excel file."""
        if self.comparison is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export data",
                                              self._default_dir(self._base_name() + ".xlsx"),
                                              "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        c = self.comparison
        summary = pd.DataFrame(list(c.summary.items()), columns=["Item", "Value"])
        summary["Value"] = summary["Value"].astype(str)
        spectra = pd.DataFrame({"Wavelength (nm)": c.wavelengths,
                                "A, all tissue (max = 1)": c.mean_shape_a,
                                "B, all tissue (max = 1)": c.mean_shape_b})
        if c.mean_shape_atypical_a is not None:
            spectra["A, atypical pixels (max = 1)"] = c.mean_shape_atypical_a
        q = [1, 5, 25, 50, 75, 95, 99]
        dist = pd.DataFrame({"Percentile": q,
                             "A": np.percentile(c.values_a, q), "B": np.percentile(c.values_b, q)})
        stem = os.path.splitext(path)[0]
        try:
            with pd.ExcelWriter(path, engine="openpyxl") as xw:
                summary.to_excel(xw, sheet_name="Summary", index=False)
                spectra.to_excel(xw, sheet_name="Mean spectra", index=False)
                dist.to_excel(xw, sheet_name="Distributions", index=False)
            for key, m, atyp in (("A", c.map_a, c.atypical_a), ("B", c.map_b, c.atypical_b)):
                tifffile.imwrite(f"{stem}_map{key}.tif", m.astype(np.float32),
                                 imagej=True, metadata={"unit": "binned pixel"})
                tifffile.imwrite(f"{stem}_atypical{key}.tif", atyp.astype(np.uint8) * 255)
        except PermissionError:
            QMessageBox.warning(self, "Export", "A file is open in another program.")
            return
        self.info.setText(f"Data saved: {path} (+ maps as TIFF next to it)")
