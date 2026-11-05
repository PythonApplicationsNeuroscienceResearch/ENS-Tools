"""Dialogs of the Spectral Analyzer."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QGroupBox, QInputDialog,
                               QLabel, QPlainTextEdit, QPushButton, QRadioButton, QTextBrowser,
                               QVBoxLayout)

from ..export import FIGURE_FORMATS
from ..spectral_io import Calibration


class CalibrationDialog(QDialog):
    """Dialog to set the wavelength of each channel (linear rule or explicit list)."""
    def __init__(self, cal: Calibration, n_channels: int, parent=None):
        """Build the dialog prefilled with the current calibration, with a live preview of the
        first and last wavelengths.
        """
        super().__init__(parent)
        self.n = n_channels
        self.setWindowTitle("Wavelength calibration")
        self.resize(540, 520)
        lay = QVBoxLayout(self)
        info = QLabel(
            "The TIFF stacks exported from ImageJ do not store the emission wavelength of each "
            "channel. Default = acquisition settings confirmed by the lab: excitation 405 nm, "
            "emission collected from 414 nm with 9 nm between channels. Change these values only "
            "for stacks acquired with other settings (e.g. another tissue). The calibration is "
            "applied to every stack without wavelength metadata and is written into each export.")
        info.setWordWrap(True)
        lay.addWidget(info)

        self.rb_lin = QRadioButton("Linear: centre of channel 1 and channel width")
        self.rb_list = QRadioButton(f"Explicit list ({n_channels} values, one per line, in nm)")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_lin)
        grp.addButton(self.rb_list)
        lay.addWidget(self.rb_lin)
        form = QFormLayout()
        self.start = QDoubleSpinBox()
        self.start.setRange(200, 1500)
        self.start.setDecimals(2)
        self.start.setSuffix(" nm")
        self.start.setValue(cal.start_nm)
        self.step = QDoubleSpinBox()
        self.step.setRange(0.1, 100)
        self.step.setDecimals(3)
        self.step.setSuffix(" nm")
        self.step.setValue(cal.step_nm)
        form.addRow("Channel 1 centre", self.start)
        form.addRow("Channel width (step)", self.step)
        lay.addLayout(form)
        lay.addWidget(self.rb_list)
        self.text = QPlainTextEdit()
        if cal.values:
            self.text.setPlainText("\n".join(f"{v:g}" for v in cal.values))
        lay.addWidget(self.text, 1)
        self.result = QLabel()
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        self.verified = QCheckBox("I have checked these values against the acquisition metadata")
        self.verified.setChecked(cal.verified)
        lay.addWidget(self.verified)
        reset = QPushButton("Restore lab settings (414 nm, +9 nm)")
        reset.clicked.connect(self._restore_default)
        lay.addWidget(reset)
        (self.rb_list if cal.values else self.rb_lin).setChecked(True)
        for w in (self.start, self.step):
            w.valueChanged.connect(self._preview)
        self.text.textChanged.connect(self._preview)
        self.rb_lin.toggled.connect(self._preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._preview()

    def _restore_default(self) -> None:
        """Put back the lab settings (414 nm, +9 nm per channel)."""
        d = Calibration()
        self.rb_lin.setChecked(True)
        self.start.setValue(d.start_nm)
        self.step.setValue(d.step_nm)
        self.verified.setChecked(d.verified)
        self._preview()

    def _values(self):
        """Explicit list of wavelengths typed by the user, or None in linear mode; raises
        ValueError on bad input.
        """
        if self.rb_lin.isChecked():
            return None
        vals = []
        for line in self.text.toPlainText().replace(";", "\n").replace(",", ".").split():
            vals.append(float(line))
        return vals

    def _preview(self) -> None:
        """Show the resulting wavelength range, or what is wrong with the input."""
        try:
            vals = self._values()
        except ValueError:
            self.result.setText("<span style='color:#ef6f78'>The list contains a non-numeric value.</span>")
            return
        if vals is not None and len(vals) != self.n:
            self.result.setText(f"<span style='color:#ef6f78'>{len(vals)} values entered; "
                                f"{self.n} expected.</span>")
            return
        wl = np.asarray(vals) if vals is not None else \
            self.start.value() + self.step.value() * np.arange(self.n)
        self.result.setText(f"Channel 1 = {wl[0]:.1f} nm … channel {self.n} = {wl[-1]:.1f} nm")

    def _accept(self) -> None:
        """Close with OK only if the input is valid."""
        try:
            vals = self._values()
        except ValueError:
            return
        if vals is not None and len(vals) != self.n:
            return
        self.accept()

    def calibration(self) -> Calibration:
        """Calibration object built from the dialog."""
        vals = self._values()
        return Calibration(start_nm=self.start.value(), step_nm=self.step.value(),
                           values=vals, verified=self.verified.isChecked())


class FigureDialog(QDialog):
    """Options of the publication figure: format, size in mm, resolution, font size and content."""
    def __init__(self, parent=None, defaults: Optional[dict] = None):
        """Build the form, prefilled with the last values used."""
        super().__init__(parent)
        d = defaults or {}
        self.setWindowTitle("Export spectrum figure")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.fmt = QComboBox()
        self.fmt.addItems(list(FIGURE_FORMATS))
        self.fmt.setCurrentText(d.get("format", "TIFF (publication, LZW)"))
        self.width = QDoubleSpinBox()
        self.width.setRange(30, 400)
        self.width.setSuffix(" mm")
        self.width.setValue(d.get("width", 85.0))
        self.height = QDoubleSpinBox()
        self.height.setRange(30, 400)
        self.height.setSuffix(" mm")
        self.height.setValue(d.get("height", 65.0))
        self.dpi = QComboBox()
        self.dpi.addItems(["300", "600", "1200"])
        self.dpi.setCurrentText(str(d.get("dpi", 600)))
        self.font = QDoubleSpinBox()
        self.font.setRange(5, 20)
        self.font.setValue(d.get("font", 8.0))
        self.font.setSuffix(" pt")
        self.title = QComboBox()
        self.title.setEditable(True)
        self.title.addItem(d.get("title", ""))
        form.addRow("Format", self.fmt)
        form.addRow("Width (85 mm = 1 column)", self.width)
        form.addRow("Height", self.height)
        form.addRow("Resolution (dpi)", self.dpi)
        form.addRow("Font size", self.font)
        form.addRow("Title (optional)", self.title)
        lay.addLayout(form)
        box = QGroupBox("Content")
        bl = QVBoxLayout(box)
        self.legend = QCheckBox("Legend")
        self.legend.setChecked(d.get("legend", True))
        self.sd = QCheckBox("± SD band")
        self.sd.setChecked(d.get("sd", False))
        self.peaks = QCheckBox("Peak markers")
        self.peaks.setChecked(d.get("peaks", False))
        self.strip = QCheckBox("Wavelength colour band")
        self.strip.setChecked(d.get("strip", True))
        for w in (self.legend, self.sd, self.peaks, self.strip):
            bl.addWidget(w)
        lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        """Chosen options as a dict, also remembered for the next export."""
        return {"format": self.fmt.currentText(), "width": self.width.value(),
                "height": self.height.value(), "dpi": int(self.dpi.currentText()),
                "font": self.font.value(), "legend": self.legend.isChecked(),
                "sd": self.sd.isChecked(), "peaks": self.peaks.isChecked(),
                "strip": self.strip.isChecked(), "title": self.title.currentText().strip()}


def ask_z(parent, n: int):
    """Ask how to reduce a Z axis; returns 'max', 'mean', a 0-based index, or None."""
    items = ["Maximum projection", "Mean projection"] + [f"Plane {i + 1}" for i in range(n)]
    item, ok = QInputDialog.getItem(parent, "Z-stack",
                                    f"The file contains {n} optical sections.\n"
                                    "Which data should be analysed?", items, 0, False)
    if not ok:
        return None
    if item.startswith("Maximum"):
        return "max"
    if item.startswith("Mean"):
        return "mean"
    return int(item.split()[-1]) - 1


class AboutDialog(QDialog):
    """Version, authors (with clickable GitHub links) and acknowledgements."""

    def __init__(self, html: str, parent=None):
        """Show the credits (HTML) with clickable GitHub links."""
        super().__init__(parent)
        self.setWindowTitle("About")
        self.resize(520, 360)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setHtml(html)
        lay.addWidget(tb)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
