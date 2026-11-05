"""Spectrum plotting shared by the on-screen canvas and the publication export."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .spectral_core import wavelength_to_rgb


@dataclass
class Series:
    """One curve to plot: label, wavelengths, values, optional SD band, colour and line style."""
    label: str
    wl: np.ndarray
    y: np.ndarray
    sd: Optional[np.ndarray]
    color: str
    peak_nm: float = float("nan")
    ls: str = "-"                  # line style ('-' ROIs, '--' / '-.' reference curves)


# On-screen (dark theme) colours; exported figures always use a white background.
DARK = {"bg": "#1f2735", "fg": "#d8dee8", "grid": "#5a6b82", "legend": "#273142"}


def _ink(color: str, dark: bool) -> str:
    """Very light ROI colours (e.g. white) are invisible on white paper: darken them."""
    from matplotlib.colors import to_rgb
    r, g, b = to_rgb(color)
    if not dark and 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.85:
        return "#333333"
    return color


def draw_spectra(fig, series: List[Series], ylabel: str, *, live: Optional[Series] = None,
                 show_sd: bool = True, show_peaks: bool = False, color_strip: bool = True,
                 legend: bool = True, font_size: float = 9.0, line_width: float = 1.6,
                 title: str = "", dark: bool = False) -> None:
    """Draw all spectra on ``fig`` (cleared first). ``dark`` = on-screen theme."""
    fig.clear()
    fig.set_facecolor(DARK["bg"] if dark else "white")
    ax = fig.add_subplot(111)
    ax.set_facecolor(DARK["bg"] if dark else "white")
    for s in series:
        c = _ink(s.color, dark)
        ax.plot(s.wl, s.y, color=c, lw=line_width, label=s.label, marker="o",
                ms=line_width * 1.3, mew=0, ls=s.ls)
        if show_sd and s.sd is not None and np.isfinite(s.sd).any():
            ax.fill_between(s.wl, s.y - s.sd, s.y + s.sd, color=c, alpha=0.18, lw=0)
        if show_peaks and np.isfinite(s.peak_nm):
            ax.axvline(s.peak_nm, color=c, lw=0.8, ls=":", alpha=0.9)
    if live is not None:
        ax.plot(live.wl, live.y, color=DARK["fg"] if dark else "#555555", lw=1.2, ls="--",
                label=live.label)
    # x-range = channels that carry a value (a restricted wavelength range leaves NaN elsewhere)
    curves = series + ([live] if live is not None else [])
    all_wl = [np.asarray(s.wl)[np.isfinite(s.y)] for s in curves]
    all_wl = [w for w in all_wl if w.size]
    if all_wl:
        lo = min(float(np.nanmin(w)) for w in all_wl)
        hi = max(float(np.nanmax(w)) for w in all_wl)
        pad = (hi - lo) * 0.02 if hi > lo else 1.0
        ax.set_xlim(lo - pad, hi + pad)
    ax.set_xlabel("Wavelength (nm)", fontsize=font_size)
    ax.set_ylabel(ylabel, fontsize=font_size)
    ax.tick_params(labelsize=font_size * 0.9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if dark:
        ax.xaxis.label.set_color(DARK["fg"])
        ax.yaxis.label.set_color(DARK["fg"])
        ax.tick_params(colors=DARK["fg"])
        for side in ("left", "bottom"):
            ax.spines[side].set_color(DARK["grid"])
        ax.grid(True, color=DARK["grid"], alpha=0.25, lw=0.6)
    if title:
        ax.set_title(title, fontsize=font_size, color=DARK["fg"] if dark else "black")
    if legend and (series or live is not None):
        leg = ax.legend(fontsize=font_size * 0.85, frameon=True, loc="best", framealpha=0.85)
        leg.get_frame().set_edgecolor("none")
        if dark:
            leg.get_frame().set_facecolor(DARK["legend"])
            for t in leg.get_texts():
                t.set_color(DARK["fg"])
    if color_strip and all_wl:
        # Thin band showing the colour of each wavelength, inside the bottom of the axes.
        y0, y1 = ax.get_ylim()
        ax.set_ylim(y0 - 0.07 * (y1 - y0), y1)
        x0, x1 = ax.get_xlim()
        xs = np.linspace(x0, x1, 400)
        strip = np.array([wavelength_to_rgb(x) for x in xs])[None, :, :]
        cax = ax.inset_axes([0.0, 0.0, 1.0, 0.035], sharex=ax)
        cax.imshow(strip, extent=(x0, x1, 0, 1), aspect="auto")
        cax.set_axis_off()
    if not series and live is None:
        ax.text(0.5, 0.5, "Draw a region of interest on the image\n(or enable the live cursor spectrum)",
                transform=ax.transAxes, ha="center", va="center",
                color="#9aa7ba" if dark else "#7a8591",
                fontsize=font_size)
    fig.tight_layout()
