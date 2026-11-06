"""Main window of the Spectral Analyzer."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QColorDialog,
                               QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
                               QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPushButton, QScrollArea, QSlider,
                               QSizePolicy, QSpinBox,
                               QSplitter, QTableWidget, QTableWidgetItem, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

from .. import COPYRIGHT_SHORT, __version__, credits_html
from .. import spectral_core as sc
from ..export import FIGURE_FORMATS, ExportRow, export_csv, export_excel, save_figure
from ..plotting import Series, draw_spectra
from ..roi_io import RoiRecord, import_imagej, load_rois, save_rois
from ..spectral_io import Calibration, SpectralStack, StackFormatError, load_stack, sanity_report
from .compare_maps import SpectralMapsWindow
from .dialogs import AboutDialog, CalibrationDialog, FigureDialog, ask_z
from .style import fit_to_screen
from .viewer import SpectralViewer

# Bright colours that stay visible on the dark, mostly cyan autofluorescence images.
ROI_COLORS = ["#ff595e", "#ffca3a", "#8ac926", "#f15bb5", "#ff924c", "#ffffff", "#c77dff",
              "#06d6a0", "#1982c4", "#b5838d"]
DISPLAY_MODES = [
    "Colour-coded projection (ImageJ temporal colour code)",
    "True colour (wavelength-weighted sum)",
    "Sum projection (grey)",
    "Maximum projection (grey)",
    "Single channel (grey)",
]
TOOL_LABELS = [("pan", "✋ Pan / select"), ("freehand", "✎ Freehand"), ("polygon", "⬠ Polygon"),
               ("rectangle", "▭ Rectangle"), ("ellipse", "◯ Ellipse"), ("point", "• Point")]
COL = {"show": 0, "color": 1, "name": 2, "image": 3, "role": 4, "shape": 5, "n": 6, "area": 7,
       "peak": 8, "peak_i": 9, "fwhm": 10, "centroid": 11}
HEADERS = ["Show", "", "Name", "Image", "Role", "Shape", "Pixels", "Area (µm²)", "Peak (nm)",
           "Peak interp. (nm)", "FWHM (nm)", "Centroid (nm)"]


def short_name(name: str) -> str:
    """'Spectre Lame BK1238 EA5 03_6-slice14' -> 'Lame BK1238 EA5 03_6'."""
    import re
    name = re.sub(r"^(spectre|spectrum|spectral)\s+", "", name, flags=re.IGNORECASE)
    return re.sub(r"[-_ ]slice\d+$", "", name, flags=re.IGNORECASE)


class InfoLabel(QLabel):
    """One-line label that never changes the width of its pane: text that does not fit is cut
    with '…' (the full text is in the tooltip).

    The pixel information under an image changes length while the mouse moves. With a normal
    QLabel the pane widened and narrowed with it; in 'Compare two images' mode the image was then
    refitted to the new size, so it grew and shrank in a loop under the mouse.
    """

    def __init__(self, text: str = " ", parent=None):
        """Create the label with a size policy that ignores the text width."""
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._full = ""
        self.setText(text)

    def setText(self, text: str) -> None:
        """Store the full text and show the part that fits the current width."""
        self._full = text
        self.setToolTip(text if text.strip() else "")
        self._refresh()

    def fullText(self) -> str:
        """Full, non-shortened text."""
        return self._full

    def resizeEvent(self, event) -> None:
        """Shorten the text again when the pane is resized."""
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        """Show the full text cut with '…' at the label width."""
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(0, self.width())))

    def sizeHint(self) -> QSize:
        """Width 0: the text never asks for more room (height of one line)."""
        return QSize(0, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        """Width 0: the text never imposes a minimum pane width."""
        return QSize(0, super().minimumSizeHint().height())


class Pane(QWidget):
    """One image viewer with its image selector."""

    def __init__(self, title: str, parent=None):
        """Image pane: stack selector, zoom buttons, the image viewer and a line of pixel
        information.
        """
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        top = QHBoxLayout()
        lab = QLabel(title[-1])
        lab.setToolTip(title)
        lab.setStyleSheet("font-weight: 600; color: #d6e6fb; background: #2c4466;"
                          " border: 1px solid #5a7194; border-radius: 4px; padding: 2px 8px;")
        top.addWidget(lab)
        self.selector = QComboBox()
        self.selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(8)
        top.addWidget(self.selector, 1)
        for text, f in (("Fit", lambda: self.viewer.fit()), ("+", lambda: self.viewer.zoom(1.25)),
                        ("−", lambda: self.viewer.zoom(0.8))):
            b = QToolButton()
            b.setText(text)
            b.clicked.connect(f)
            top.addWidget(b)
        lay.addLayout(top)
        self.viewer = SpectralViewer()
        lay.addWidget(self.viewer, 1)
        self.info = InfoLabel(" ")
        self.info.setStyleSheet("color: #9aa7ba;")
        lay.addWidget(self.info)

    def current_path(self) -> Optional[str]:
        """Path of the stack shown in this pane (None if empty)."""
        return self.selector.currentData()


class MainWindow(QMainWindow):
    """Main window of the Spectral Analyzer: stacks and display settings on the left, image(s) in
    the centre, spectra plot and ROI table on the right.
    """
    def __init__(self, files: Optional[List[str]] = None):
        """Create the state (open stacks, ROIs, spectra, caches, undo list), build the interface
        and open the files given at start.
        """
        super().__init__()
        self.setWindowTitle("Spectral Analyzer")
        fit_to_screen(self, 1600, 950)
        self.setAcceptDrops(True)
        self.settings = QSettings("SPPIN", "Spectral Analyzer")
        self.cal = self._load_calibration()
        self.stacks: Dict[str, SpectralStack] = {}
        self.rois: List[RoiRecord] = []
        self.spectra: Dict[int, sc.Spectrum] = {}
        self.masks_centroid: Dict[int, Tuple[float, float]] = {}
        self.selected: Optional[int] = None
        self._uid = 0
        self._render_cache: Dict[tuple, np.ndarray] = {}
        self._live: Optional[Series] = None
        self._filling_table = False
        self._fig_defaults = {}
        self._bg_by_image: Dict[str, np.ndarray] = {}
        self._dark_bg: Dict[str, sc.Spectrum] = {}            # path -> dimmest-1 % spectrum
        self._ring_bg: Dict[tuple, sc.Spectrum] = {}          # (uid, gap, width, others) -> ring
        self._maps_window = None          # bonus: whole-image spectral comparison
        # Undo history of ROI additions/deletions: ("add", uid) or ("delete", [(index, record,
        # spectrum, centroid), ...]).
        self._undo: List[tuple] = []
        self._tissue: Dict[str, sc.Spectrum] = {}   # path -> mean spectrum of the tissue pixels

        self._build_ui()
        self._replot_timer = QTimer(self)
        self._replot_timer.setSingleShot(True)
        self._replot_timer.setInterval(40)
        self._replot_timer.timeout.connect(self._replot)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120)
        self._render_timer.timeout.connect(self._render_all)
        self._update_banner()
        self._replot()
        if files:
            QTimer.singleShot(0, lambda: self.open_files(files))

    # ================================================================= UI
    def _build_ui(self) -> None:
        """Build every widget: toolbars, menus, left panel (stacks, display, wavelengths, bonus),
        image panes and right panel (plot, normalisation, background, wavelength range, ROI
        table, export buttons).
        """
        tb = QToolBar("Tools")
        tb.setIconSize(QSize(16, 16))
        tb.setMovable(False)
        self.addToolBar(tb)
        # Show / hide the left panel (hidden automatically in 'Compare two images' mode).
        self.panel_action = QAction("☰", self)
        self.panel_action.setCheckable(True)
        self.panel_action.setChecked(True)
        self.panel_action.setShortcut(QKeySequence("Ctrl+B"))
        self.panel_action.setToolTip("Show / hide the left panel: image list and display settings (Ctrl+B)")
        self.panel_action.toggled.connect(self._set_left_panel)
        # A click by the user overrides the automatic folding of 'Compare two images'.
        self.panel_action.triggered.connect(lambda: setattr(self, "_panel_auto_hidden", False))
        tb.addAction(self.panel_action)
        tb.addSeparator()
        a = QAction("📂 Open stack(s)…", self)
        a.setShortcut(QKeySequence.Open)
        a.triggered.connect(self.open_dialog)
        tb.addAction(a)
        tb.addSeparator()
        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for key, text in TOOL_LABELS:
            act = QAction(text, self)
            act.setCheckable(True)
            act.setData(key)
            act.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            self.tool_group.addAction(act)
            tb.addAction(act)
            self.tool_actions[key] = act
        self.tool_actions["freehand"].setChecked(True)
        tb.addWidget(QLabel(" radius "))
        self.point_radius = QSpinBox()
        self.point_radius.setRange(0, 50)
        self.point_radius.setSuffix(" px")
        self.point_radius.setToolTip("Point ROI radius (0 = single pixel)")
        self.point_radius.valueChanged.connect(self._radius_changed)
        tb.addWidget(self.point_radius)
        # View options on a second toolbar row, so that nothing is pushed into the
        # overflow menu on laptop screens.
        self.addToolBarBreak()
        tb = QToolBar("View")
        tb.setIconSize(QSize(16, 16))
        tb.setMovable(False)
        self.addToolBar(tb)
        self.live = QCheckBox("Live cursor spectrum")
        self.live.setToolTip("Plot the spectrum under the mouse cursor (dashed grey line)")
        self.live.toggled.connect(self._live_toggled)
        tb.addWidget(self.live)
        self.live_size = QComboBox()
        for n in (1, 3, 5, 7, 9):
            self.live_size.addItem(f"{n}×{n} px", n)
        self.live_size.setCurrentIndex(1)
        tb.addWidget(self.live_size)
        tb.addSeparator()
        self.split = QCheckBox("Compare two images")
        self.split.toggled.connect(self._split_toggled)
        tb.addWidget(self.split)

        # ------------------------------------------------------------ menus
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(a)
        act = QAction("Close selected image", self)
        act.triggered.connect(self.close_selected_image)
        m.addAction(act)
        m.addSeparator()
        act = QAction("Quit", self)
        act.setShortcut(QKeySequence.Quit)
        act.triggered.connect(self.close)
        m.addAction(act)
        m = mb.addMenu("&Edit")
        self.undo_action = QAction("Undo", self)
        self.undo_action.setShortcut(QKeySequence.Undo)
        self.undo_action.setEnabled(False)
        self.undo_action.triggered.connect(self.undo)
        m.addAction(self.undo_action)
        m = mb.addMenu("&Export")
        for text, fn, sc_ in (("Spectra to Excel (.xlsx)…", self.export_excel, "Ctrl+E"),
                              ("Spectra to CSV…", self.export_csv, None),
                              ("Spectrum figure (TIFF / PNG / JPEG / PDF / SVG)…", self.export_figure, "Ctrl+F"),
                              ("Image with ROIs (left image)…", lambda: self.export_image(0), None),
                              ("Image with ROIs (right image)…", lambda: self.export_image(1), None)):
            act = QAction(text, self)
            if sc_:
                act.setShortcut(QKeySequence(sc_))
            act.triggered.connect(fn)
            m.addAction(act)
        m = mb.addMenu("&ROIs")
        for text, fn in (("Save ROI set…", self.save_roi_set), ("Load ROI set…", self.load_roi_set),
                         ("Import ImageJ ROIs (.roi / .zip) onto left image…", self.import_imagej),
                         ("Delete selected ROI", self.delete_selected),
                         ("Delete all ROIs", self.delete_all)):
            act = QAction(text, self)
            act.triggered.connect(fn)
            m.addAction(act)
        m = mb.addMenu("&Analysis")
        act = QAction("Spectral difference maps, whole image (bonus)…", self)
        act.triggered.connect(self.open_spectral_maps)
        m.addAction(act)
        m = mb.addMenu("&Settings")
        act = QAction("Wavelength calibration…", self)
        act.triggered.connect(self.edit_calibration)
        m.addAction(act)
        m = mb.addMenu("&Help")
        act = QAction("Quick guide", self)
        act.triggered.connect(self.show_help)
        m.addAction(act)
        act = QAction("About", self)
        act.triggered.connect(self.show_about)
        m.addAction(act)

        # ------------------------------------------------------------ left
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        box = QGroupBox("Open spectral stacks")
        bl = QVBoxLayout(box)
        # List of the open stacks.
        self.image_list = QListWidget()
        self.image_list.setMaximumHeight(160)
        bl.addWidget(self.image_list)
        hl = QHBoxLayout()
        b = QPushButton("Open…")
        b.clicked.connect(self.open_dialog)
        hl.addWidget(b)
        b = QPushButton("Close")
        b.clicked.connect(self.close_selected_image)
        hl.addWidget(b)
        bl.addLayout(hl)
        ll.addWidget(box)

        # Display settings (do not change the spectra, only the image).
        box = QGroupBox("Display")
        form = QFormLayout(box)
        self.mode = QComboBox()
        self.mode.addItems(DISPLAY_MODES)
        self.mode.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mode.setMinimumContentsLength(12)
        self.lut = QComboBox()
        self.lut.addItems(sc.LUTS)
        self.channel = QSlider(Qt.Horizontal)
        self.channel.setRange(0, 31)
        self.channel_label = QLabel("–")
        self.low = QDoubleSpinBox()
        self.low.setRange(0, 50)
        self.low.setValue(0.5)
        self.low.setSuffix(" %")
        self.high = QDoubleSpinBox()
        self.high.setRange(50, 100)
        self.high.setDecimals(2)
        self.high.setValue(99.8)
        self.high.setSuffix(" %")
        self.gamma = QDoubleSpinBox()
        self.gamma.setRange(0.2, 5)
        self.gamma.setSingleStep(0.1)
        self.gamma.setValue(1.0)
        self.equalise = QCheckBox("Equalise channels (display only)")
        self.equalise.setToolTip("Stretch each channel separately so that weak long-wavelength "
                                 "emitters become visible. Spectra are never affected.")
        self.scalebar = QSpinBox()
        self.scalebar.setRange(0, 2000)
        self.scalebar.setSuffix(" µm")
        self.scalebar.setSpecialValueText("off")
        self.scalebar.setValue(50)
        form.addRow("Mode", self.mode)
        form.addRow("Colour table", self.lut)
        chl = QHBoxLayout()
        chl.addWidget(self.channel, 1)
        chl.addWidget(self.channel_label)
        form.addRow("Channel", chl)
        form.addRow("Low saturation", self.low)
        form.addRow("High saturation", self.high)
        form.addRow("Gamma", self.gamma)
        form.addRow(self.equalise)
        form.addRow("Scale bar", self.scalebar)
        for w in (self.mode, self.lut):
            w.currentIndexChanged.connect(self._display_changed)
        for w in (self.low, self.high, self.gamma):
            w.valueChanged.connect(self._display_changed)
        self.channel.valueChanged.connect(self._display_changed)
        self.equalise.toggled.connect(self._display_changed)
        self.scalebar.valueChanged.connect(self._scalebar_changed)
        ll.addWidget(box)

        # Wavelength calibration status.
        box = QGroupBox("Wavelengths")
        bl = QVBoxLayout(box)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        bl.addWidget(self.banner)
        b = QPushButton("Calibration…")
        b.clicked.connect(self.edit_calibration)
        bl.addWidget(b)
        ll.addWidget(box)

        box = QGroupBox("Bonus: whole-image comparison")
        bl = QVBoxLayout(box)
        lab = QLabel("Where do two images differ spectrally? Heat maps over the whole tissue, "
                     "no ROI needed.")
        lab.setWordWrap(True)
        lab.setStyleSheet("color: #9aa7ba;")
        bl.addWidget(lab)
        b = QPushButton("Spectral difference maps…")
        b.clicked.connect(self.open_spectral_maps)
        bl.addWidget(b)
        ll.addWidget(box)
        ll.addStretch(1)

        # ---------------------------------------------------------- centre
        self.panes = [Pane("Image A"), Pane("Image B")]
        for i, p in enumerate(self.panes):
            p.selector.currentIndexChanged.connect(lambda _=0, k=i: self._pane_image_changed(k))
            p.viewer.roiDrawn.connect(lambda g, k=i: self._roi_drawn(k, g))
            p.viewer.hovered.connect(lambda x, y, k=i: self._hovered(k, x, y))
            p.viewer.roiClicked.connect(self._select_roi)
        self.panes[1].setVisible(False)
        centre = QSplitter(Qt.Horizontal)
        centre.addWidget(self.panes[0])
        centre.addWidget(self.panes[1])

        # ----------------------------------------------------------- right
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        # Spectrum plot, with the wavelength/value readout underneath.
        self.fig = Figure(figsize=(5, 3.4), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setMinimumHeight(300)
        self.canvas.mpl_connect("motion_notify_event", self._plot_hover)
        self.canvas.mpl_connect("figure_leave_event", self._plot_leave)
        rl.addWidget(self.canvas, 3)
        self.readout = QLabel("Move the mouse over the plot to read wavelengths and values.")
        self.readout.setStyleSheet("color: #9aa7ba;")
        self.readout.setWordWrap(True)
        # Room for two lines from the start, so that the plot does not jump when the text grows.
        self.readout.setMinimumHeight(2 * self.readout.fontMetrics().lineSpacing() + 6)
        rl.addWidget(self.readout)
        self._hover_line = None
        self._hover_x: Optional[float] = None     # last wavelength under the mouse (None = outside)
        self._plot_series: List[Series] = []
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Normalisation"))
        # Normalisation of the spectra.
        self.norm = QComboBox()
        self.norm.addItems(sc.NORMALISATIONS)
        self.norm.currentIndexChanged.connect(self._spectra_changed)
        opts.addWidget(self.norm)
        opts.addStretch(1)
        rl.addLayout(opts)
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Background"))
        # Background subtraction (none, drawn ROI, dimmest pixels, ring around each ROI).
        self.bg_mode = QComboBox()
        self.bg_mode.addItems(sc.BACKGROUND_MODES)
        self.bg_mode.setToolTip(
            "Spectrum subtracted from each ROI before normalisation:\n"
            "- Background ROI(s): mean spectrum of the ROIs given the role 'Background' (per image).\n"
            "- Dimmest 1 %: mean spectrum of the 1 % dimmest pixels of the image (detector offset,\n"
            "  stray light; on the ENS stacks these pixels are faint tissue).\n"
            "- Ring: for each ROI, the pixels of a ring around it (local background, no drawing;\n"
            "  other ROIs excluded). Shows what the structure emits above its surroundings.")
        self.bg_mode.currentIndexChanged.connect(self._bg_mode_changed)
        opts.addWidget(self.bg_mode, 1)
        self.ring_width = QSpinBox()
        self.ring_width.setRange(2, 100)
        self.ring_width.setValue(10)
        self.ring_width.setSuffix(" px ring")
        self.ring_width.setToolTip("Width of the background ring, starting 2 px outside the ROI "
                                   "edge (10 px = 2 µm at 0.2 µm/px)")
        self.ring_width.valueChanged.connect(self._spectra_changed)
        self.ring_width.setVisible(False)
        opts.addWidget(self.ring_width)
        rl.addLayout(opts)
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Range"))
        # Analysed wavelength range (from ... to ... nm, button Full).
        self.wl_lo = QSpinBox()
        self.wl_hi = QSpinBox()
        for sb in (self.wl_lo, self.wl_hi):
            sb.setRange(200, 1500)
            sb.setSuffix(" nm")
            sb.setToolTip("Analysed wavelength range: only the channels whose centre lies in this "
                          "range are plotted, normalised (e.g. max = 1 within the range), "
                          "described (peak, FWHM, centroid) and exported as processed spectra. "
                          "Raw spectra are always exported in full. If the spectrum decreases "
                          "throughout the range, the reported peak is the range edge.")
        self.wl_lo.setValue(414)
        self.wl_hi.setValue(693)
        self._range_custom = False
        self.wl_lo.valueChanged.connect(self._range_changed)
        self.wl_hi.valueChanged.connect(self._range_changed)
        b = QToolButton()
        b.setText("Full")
        b.setToolTip("Analyse all channels")
        b.clicked.connect(self._range_full)
        opts.addWidget(self.wl_lo)
        opts.addWidget(QLabel("to"))
        opts.addWidget(self.wl_hi)
        opts.addWidget(b)
        opts.addStretch(1)
        rl.addLayout(opts)
        # Check boxes on a 2 x 2 grid (keeps the panel narrow on laptop screens).
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        # Plot options (SD band, peak markers, colour band, image mean spectrum).
        self.show_sd = QCheckBox("± SD")
        self.show_sd.setToolTip("± standard deviation of the pixel intensities inside each ROI "
                                "(pixel heterogeneity)")
        self.show_sd.setChecked(False)
        self.show_peaks = QCheckBox("Peak markers")
        self.show_strip = QCheckBox("Colour band")
        self.show_strip.setChecked(True)
        for w in (self.show_sd, self.show_peaks, self.show_strip):
            w.toggled.connect(self._schedule_replot)
        grid.addWidget(self.show_sd, 0, 0)
        grid.addWidget(self.show_peaks, 0, 1)
        grid.addWidget(self.show_strip, 1, 0)
        self.show_mean = QCheckBox("Image mean spectrum (white)")
        self.show_mean.setToolTip(
            "Mean spectrum of all tissue pixels of the displayed image(s), background excluded "
            "(Otsu threshold), drawn in white (dashed for image B) to compare with the ROIs. "
            "Same normalisation and background subtraction as the ROIs.")
        self.show_mean.toggled.connect(self._schedule_replot)
        grid.addWidget(self.show_mean, 2, 0, 1, 2)
        grid.setColumnStretch(2, 1)
        rl.addLayout(grid)

        # ROI table: one row per ROI with its descriptors.
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL["name"], QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(COL["image"], QHeaderView.Interactive)
        self.table.setColumnWidth(COL["name"], 150)
        self.table.setColumnWidth(COL["image"], 150)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.itemChanged.connect(self._table_item_changed)
        self.table.cellClicked.connect(self._table_clicked)
        self.table.itemSelectionChanged.connect(self._table_selection)
        rl.addWidget(self.table, 2)
        hl = QHBoxLayout()
        b = QPushButton("Delete ROI")
        b.clicked.connect(self.delete_selected)
        hl.addWidget(b)
        b = QPushButton("Delete all")
        b.clicked.connect(self.delete_all)
        hl.addWidget(b)
        hl.addStretch(1)
        rl.addLayout(hl)
        hl = QHBoxLayout()
        hl.addStretch(1)
        # Export buttons.
        b = QPushButton("Export spectra…")
        b.setObjectName("primary")
        b.clicked.connect(self.export_excel)
        hl.addWidget(b)
        b = QPushButton("Export figure…")
        b.clicked.connect(self.export_figure)
        hl.addWidget(b)
        rl.addLayout(hl)

        main = QSplitter(Qt.Horizontal)
        # The left column scrolls instead of imposing its height on the window.
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setFrameShape(QScrollArea.NoFrame)
        left_scroll.setWidget(left)
        left_scroll.setMinimumWidth(left.minimumSizeHint().width() + 14)
        self.left_panel = left_scroll
        self._panel_auto_hidden = False      # True when hidden by 'Compare two images'
        main.addWidget(left_scroll)
        main.addWidget(centre)
        main.addWidget(right)
        main.setStretchFactor(1, 3)
        main.setStretchFactor(2, 2)
        main.setSizes([270, 760, 620])
        self.setCentralWidget(main)
        self.status = self.statusBar()
        credit = QLabel(COPYRIGHT_SHORT)
        credit.setStyleSheet("color: #9aa7ba;")
        credit.setToolTip("Help › About: authors and GitHub accounts")
        self.status.addPermanentWidget(credit)
        QShortcut(QKeySequence.Delete, self, activated=self.delete_selected)
        self.set_tool("freehand")

    # ========================================================= settings
    # Settings key of the calibration.  Values saved by earlier versions under
    # "calibration" (unconfirmed 414.45 nm / 8.9 nm guess) are deliberately
    # ignored: the lab-confirmed default (414 nm, +9 nm) replaces them.
    _CAL_KEY = "calibration_v2"

    def _load_calibration(self) -> Calibration:
        """Wavelength calibration saved in the settings (lab default if none)."""
        raw = self.settings.value(self._CAL_KEY, "")
        try:
            d = json.loads(raw) if raw else {}
            return Calibration(**d)
        except (TypeError, ValueError):
            return Calibration()

    def _save_calibration(self) -> None:
        """Remember the wavelength calibration for the next sessions."""
        self.settings.setValue(self._CAL_KEY, json.dumps(self.cal.__dict__))

    # =========================================================== opening
    def open_dialog(self) -> None:
        """Ask for one or more stack files and open them."""
        start = self.settings.value("last_dir", "")
        files, _ = QFileDialog.getOpenFileNames(self, "Open spectral stack(s)", start,
                                                "TIFF stacks (*.tif *.tiff *.lsm);;All files (*)")
        if files:
            self.settings.setValue("last_dir", os.path.dirname(files[0]))
            self.open_files(files)

    def open_files(self, files: List[str]) -> None:
        """Load the given stack files, list them and show the first one(s); unreadable files are
        reported.
        """
        opened = []
        for f in files:
            f = os.path.abspath(f)
            if f in self.stacks:
                opened.append(f)
                continue
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                st = load_stack(f, self.cal, choose_z=lambda n: ask_z(self, n))
            except (StackFormatError, OSError, ValueError) as exc:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, "Cannot open", f"{os.path.basename(f)}:\n{exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - unknown TIFF flavours
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, "Cannot open", f"{os.path.basename(f)}:\n{exc}")
                continue
            QApplication.restoreOverrideCursor()
            self.stacks[f] = st
            it = QListWidgetItem(st.name)
            it.setData(Qt.UserRole, f)
            it.setToolTip(f"{f}\n{st.n_channels} channels, {st.shape_yx[1]}×{st.shape_yx[0]} px, "
                          f"pixel {st.pixel_size_um or '?'} µm, wavelengths from {st.wl_source}"
                          + ("".join(f"\n{n}" for n in st.notes)))
            self.image_list.addItem(it)
            for p in self.panes:
                p.selector.blockSignals(True)
                p.selector.addItem(st.name, f)
                p.selector.blockSignals(False)
            for w in sanity_report(st):
                self.status.showMessage(f"{st.name}: {w}", 10000)
            opened.append(f)
        if not opened:
            return
        previous = self.panes[0].current_path()
        # The first opened image goes to pane A; pane B gets the second opened image, or the
        # image that was in pane A before, so that two images are ready for comparison.
        self._set_pane(0, opened[0])
        if len(opened) > 1:
            self._set_pane(1, opened[1])
            self.split.setChecked(True)
        elif previous and previous != opened[0]:
            self._set_pane(1, previous)
        elif self.panes[1].current_path() in (None, opened[0]) and len(self.stacks) == 1:
            self._set_pane(1, opened[0])
        self._update_banner()
        self._update_channel_slider()
        self._sync_range()
        self._refresh_maps_window()

    def _set_pane(self, k: int, path: str) -> None:
        """Show the stack 'path' in pane k."""
        sel = self.panes[k].selector
        i = sel.findData(path)
        if i >= 0:
            if sel.currentIndex() == i:
                self._pane_image_changed(k)
            else:
                sel.setCurrentIndex(i)

    def close_selected_image(self) -> None:
        """Close the stack selected in the list and delete its ROIs (after confirmation)."""
        it = self.image_list.currentItem()
        if it is None:
            return
        path = it.data(Qt.UserRole)
        n = sum(1 for r in self.rois if r.image == path)
        if n and QMessageBox.question(self, "Close image",
                                      f"Close {it.text()} and delete its {n} ROI(s)?") != QMessageBox.Yes:
            return
        for r in [r for r in self.rois if r.image == path]:
            self._remove_roi(r.uid)
        del self.stacks[path]
        self._tissue.pop(path, None)
        self._dark_bg.pop(path, None)
        # Undo entries that refer to the closed image can no longer be applied.
        kept = []
        for kind, payload in self._undo:
            if kind == "delete":
                payload = [e for e in payload if e[1].image != path]
                if payload:
                    kept.append((kind, payload))
            elif any(r.uid == payload for r in self.rois):
                kept.append((kind, payload))
        self._undo = kept
        self._update_undo_action()
        self._render_cache = {k: v for k, v in self._render_cache.items() if k[0] != path}
        self.image_list.takeItem(self.image_list.row(it))
        for p in self.panes:
            i = p.selector.findData(path)
            if i >= 0:
                p.selector.removeItem(i)
        for k in range(2):
            self._pane_image_changed(k)
        self._refresh_table()
        self._update_banner()
        self._sync_range()
        self._refresh_maps_window()

    # ============================================== bonus: spectral maps
    def open_spectral_maps(self) -> None:
        """Open (or bring to front) the whole-image spectral comparison window."""
        if not self.stacks:
            QMessageBox.information(self, "Spectral difference maps",
                                    "Open at least one spectral stack first (two to compare).")
            return
        if self._maps_window is None:
            self._maps_window = SpectralMapsWindow(lambda: self.stacks, short_name,
                                                   self._default_name, self)
        self._maps_window.refresh_images((self.panes[0].current_path(),
                                          self.panes[1].current_path()))
        self._maps_window.show()
        self._maps_window.raise_()
        self._maps_window.activateWindow()

    def _refresh_maps_window(self) -> None:
        """Update the image lists of the bonus maps window, if it is open."""
        if self._maps_window is not None:
            self._maps_window.refresh_images()

    def dragEnterEvent(self, e) -> None:
        """Accept files dragged onto the window."""
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        """Open the TIFF/LSM files dropped onto the window."""
        files = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.toLocalFile().lower().endswith((".tif", ".tiff", ".lsm"))]
        if files:
            self.open_files(files)

    # =========================================================== display
    def _display_key(self, path: str) -> tuple:
        """Key of the display settings, used to cache rendered images."""
        return (path, self.mode.currentIndex(), self.lut.currentIndex(), self.channel.value(),
                self.low.value(), self.high.value(), self.gamma.value(), self.equalise.isChecked())

    def _render(self, path: str) -> np.ndarray:
        """Render a stack as an 8-bit RGB image according to the display mode (colour-coded
        projection, true colour, sum, maximum or single channel); results are cached.
        """
        key = self._display_key(path)
        if key in self._render_cache:
            return self._render_cache[key]
        st = self.stacks[path]
        d = st.data
        mode = self.mode.currentIndex()
        lo_p, hi_p, gam = self.low.value(), self.high.value(), self.gamma.value()
        if mode == 0:
            cols = sc.channel_colors(st.wavelengths, self.lut.currentText())
            if self.equalise.isChecked():
                lo, hi = sc.channel_limits(d, lo_p, hi_p)
            else:
                lo, hi = sc.intensity_limits(d, lo_p, hi_p)
            rgb = sc.color_code_projection(d, cols, lo, hi, gam)
        elif mode == 1:
            src = d
            if self.equalise.isChecked():
                lo, hi = sc.channel_limits(d, lo_p, hi_p)
                src = np.clip((d.astype(np.float32) - lo[:, None, None]) /
                              (hi - lo)[:, None, None], 0, 1)
            rgb = sc.true_color(src, st.wavelengths, lo_p, hi_p, gam)
        elif mode == 2:
            rgb = sc.gray(d.sum(axis=0, dtype=np.float64), lo_p, hi_p, gam)
        elif mode == 3:
            rgb = sc.gray(d.max(axis=0).astype(np.float32), lo_p, hi_p, gam)
        else:
            c = min(self.channel.value(), st.n_channels - 1)
            rgb = sc.gray(d[c].astype(np.float32), lo_p, hi_p, gam)
        out = sc.to_uint8(rgb)
        if len(self._render_cache) > 12:
            self._render_cache.pop(next(iter(self._render_cache)))
        self._render_cache[key] = out
        return out

    def _render_all(self) -> None:
        """Re-render both image panes."""
        for k in range(2):
            self._show_pane(k)

    def _show_pane(self, k: int) -> None:
        """Display the current stack of pane k with its ROIs and scale bar."""
        pane = self.panes[k]
        path = pane.current_path()
        if not path or path not in self.stacks:
            pane.viewer.set_image(None)
            pane.viewer.set_rois([], None)
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pane.viewer.set_image(self._render(path))
        finally:
            QApplication.restoreOverrideCursor()
        st = self.stacks[path]
        pane.viewer.set_scalebar(self.scalebar.value() or None, st.pixel_size_um)
        pane.viewer.set_rois([r for r in self.rois if r.image == path], self.selected)

    def _display_changed(self) -> None:
        """Enable the controls that apply to the chosen display mode and re-render."""
        mode = self.mode.currentIndex()
        self.lut.setEnabled(mode == 0)
        self.channel.setEnabled(mode == 4)
        self._update_channel_label()
        self._render_timer.start()

    def _scalebar_changed(self) -> None:
        """Redraw the scale bars with the new length."""
        for p in self.panes:
            path = p.current_path()
            if path in self.stacks:
                p.viewer.set_scalebar(self.scalebar.value() or None, self.stacks[path].pixel_size_um)

    def _update_channel_slider(self) -> None:
        """Set the channel slider range to the number of channels."""
        n = max((s.n_channels for s in self.stacks.values()), default=32)
        self.channel.setRange(0, n - 1)
        self._update_channel_label()

    def _update_channel_label(self) -> None:
        """Show the selected channel number and its wavelength."""
        path = self.panes[0].current_path()
        c = self.channel.value()
        if path in self.stacks and c < self.stacks[path].n_channels:
            self.channel_label.setText(f"{c + 1} · {self.stacks[path].wavelengths[c]:.0f} nm")
        else:
            self.channel_label.setText(str(c + 1))

    def _pane_image_changed(self, k: int) -> None:
        """A pane switched to another stack: redraw it (and the image mean spectrum)."""
        self._show_pane(k)
        self._update_channel_label()
        if getattr(self, "show_mean", None) and self.show_mean.isChecked():   # follows the panes
            self._schedule_replot()

    def _set_left_panel(self, visible: bool) -> None:
        """Show or hide the left panel (button ☰ at the top left, Ctrl+B). The freed width goes
        to the images, and the panel gets its previous width back when it reappears."""
        panel = getattr(self, "left_panel", None)
        if panel is not None and panel.isVisible() != visible:
            main = self.centralWidget()
            sizes = main.sizes()                 # [left panel, images, right panel]
            if visible:
                width = getattr(self, "_left_width", 270)
                panel.setVisible(True)
                main.setSizes([width, max(200, sizes[1] - width), sizes[2]])
            else:
                self._left_width = sizes[0]
                panel.setVisible(False)
                main.setSizes([0, sizes[1] + sizes[0], sizes[2]])
        if self.panel_action.isChecked() != visible:
            self.panel_action.setChecked(visible)

    def _split_toggled(self, on: bool) -> None:
        """Show or hide the second image pane ('Compare two images'). The left panel is folded
        away while two images are compared, to give them room, and comes back afterwards."""
        if on and self.panel_action.isChecked():
            self._panel_auto_hidden = True
            self._set_left_panel(False)
        elif not on and self._panel_auto_hidden:
            self._panel_auto_hidden = False
            self._set_left_panel(True)
        self.panes[1].setVisible(on)
        if on:
            self._show_pane(1)
        if getattr(self, "show_mean", None) and self.show_mean.isChecked():
            self._schedule_replot()

    def set_tool(self, tool: str) -> None:
        """Select the mouse tool in both panes (pan or one ROI shape)."""
        self.tool_actions[tool].setChecked(True)
        for p in self.panes:
            p.viewer.set_tool(tool)
            p.viewer.point_radius = self.point_radius.value()

    def _radius_changed(self, v: int) -> None:
        """Set the radius of point ROIs."""
        for p in self.panes:
            p.viewer.point_radius = v

    # ============================================================== ROIs
    def _next_color(self) -> str:
        """First ROI colour of the palette not used yet."""
        used = [r.color for r in self.rois]
        for c in ROI_COLORS:
            if c not in used:
                return c
        return ROI_COLORS[len(self.rois) % len(ROI_COLORS)]

    def _roi_drawn(self, k: int, geom: sc.RoiGeometry) -> None:
        """An ROI was drawn in pane k: add it to the current stack and record it for undo."""
        path = self.panes[k].current_path()
        if not path or path not in self.stacks:
            return
        rec = self._add_roi(path, geom, None, None)
        if rec is not None:
            self._push_undo(("add", rec.uid))

    def _add_roi(self, path: str, geom: sc.RoiGeometry, name: Optional[str], color: Optional[str],
                 visible: bool = True, select: bool = True) -> Optional[RoiRecord]:
        """Create an ROI on a stack: build its pixel mask, compute its spectrum and add it to the
        list; returns None if the ROI contains no pixel.
        """
        st = self.stacks[path]
        mask = sc.roi_mask(st.shape_yx, geom)
        if mask.sum() == 0:
            self.status.showMessage("The ROI contains no pixel centre: ignored.", 5000)
            return None
        self._uid += 1
        rec = RoiRecord(self._uid, name or f"ROI {self._uid}", path, geom,
                        color or self._next_color(), visible)
        self.rois.append(rec)
        self.spectra[rec.uid] = sc.roi_spectrum(st.data, mask)
        ii, jj = np.nonzero(mask)
        self.masks_centroid[rec.uid] = (float(jj.mean() + 0.5), float(ii.mean() + 0.5))
        if select:
            self.selected = rec.uid
        self._refresh_table()
        self._refresh_overlays()
        self._schedule_replot()
        return rec

    def _remove_roi(self, uid: int) -> None:
        """Delete an ROI and everything computed from it."""
        self.rois = [r for r in self.rois if r.uid != uid]
        self.spectra.pop(uid, None)
        self.masks_centroid.pop(uid, None)
        if self.selected == uid:
            self.selected = None

    # ---------------------------------------------------------------- undo
    def _snapshot(self, uids: List[int]) -> list:
        """Copy of the given ROIs (position in the list, record, spectrum), so that a deletion can
        be undone.
        """
        return [(i, r, self.spectra[r.uid], self.masks_centroid[r.uid])
                for i, r in enumerate(self.rois) if r.uid in uids]

    def _push_undo(self, entry: tuple) -> None:
        """Record an action for Ctrl+Z (the last 50 are kept)."""
        self._undo.append(entry)
        del self._undo[:-50]                     # keep the last 50 actions
        self._update_undo_action()

    def _update_undo_action(self) -> None:
        """Update the text and state of Edit › Undo to describe the last action."""
        if not self._undo:
            self.undo_action.setEnabled(False)
            self.undo_action.setText("Undo")
            return
        kind, payload = self._undo[-1]
        if kind == "add":
            rec = next((r for r in self.rois if r.uid == payload), None)
            text = f"Undo: draw {rec.name}" if rec else "Undo: draw ROI"
        else:
            text = (f"Undo: delete {payload[0][1].name}" if len(payload) == 1
                    else f"Undo: delete {len(payload)} ROIs")
        self.undo_action.setEnabled(True)
        self.undo_action.setText(text)

    def undo(self) -> None:
        """Ctrl+Z: undo the last ROI drawing or deletion."""
        if not self._undo:
            self.status.showMessage("Nothing to undo.", 3000)
            return
        kind, payload = self._undo.pop()
        if kind == "add":
            rec = next((r for r in self.rois if r.uid == payload), None)
            if rec is not None:
                self._remove_roi(rec.uid)
                msg = f"Undone: drawing of {rec.name}."
            else:
                msg = "Nothing to undo."
        else:
            for index, rec, spectrum, centroid in sorted(payload, key=lambda e: e[0]):
                if rec.image not in self.stacks:
                    continue
                self.rois.insert(min(index, len(self.rois)), rec)
                self.spectra[rec.uid] = spectrum
                self.masks_centroid[rec.uid] = centroid
            self.selected = payload[-1][1].uid if len(payload) == 1 else self.selected
            msg = (f"Restored {payload[0][1].name}." if len(payload) == 1
                   else f"Restored {len(payload)} ROIs.")
        self._refresh_table()
        self._refresh_overlays()
        self._schedule_replot()
        self._update_undo_action()
        self.status.showMessage(msg, 4000)

    def delete_selected(self) -> None:
        """Delete the selected ROI (can be undone)."""
        if self.selected is None:
            return
        self._push_undo(("delete", self._snapshot([self.selected])))
        self._remove_roi(self.selected)
        self._refresh_table()
        self._refresh_overlays()
        self._schedule_replot()

    def delete_all(self) -> None:
        """Delete every ROI after confirmation (can be undone)."""
        if not self.rois:
            return
        if QMessageBox.question(self, "Delete all ROIs",
                                f"Delete all {len(self.rois)} ROIs?") != QMessageBox.Yes:
            return
        self._push_undo(("delete", self._snapshot([r.uid for r in self.rois])))
        for r in list(self.rois):
            self._remove_roi(r.uid)
        self._refresh_table()
        self._refresh_overlays()
        self._schedule_replot()
        self.status.showMessage("All ROIs deleted (Ctrl+Z to undo).", 5000)

    def _select_roi(self, uid: int) -> None:
        """Select an ROI: highlight it on the image and in the table."""
        self.selected = uid
        self._refresh_overlays()
        for row in range(self.table.rowCount()):
            it = self.table.item(row, COL["name"])
            if it is not None and it.data(Qt.UserRole) == uid:
                self.table.blockSignals(True)
                self.table.selectRow(row)
                self.table.blockSignals(False)
                break

    def _refresh_overlays(self) -> None:
        """Redraw the ROI outlines of both panes."""
        for p in self.panes:
            path = p.current_path()
            p.viewer.set_rois([r for r in self.rois if r.image == path], self.selected)

    # ------------------------------------------------------------ table
    def _refresh_table(self) -> None:
        """Rebuild the ROI table: name, colour, role, image, pixels, area and spectral descriptors.
        """
        self._filling_table = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rois))
        procs = self._processed()
        for row, r in enumerate(self.rois):
            st = self.stacks[r.image]
            sp = self.spectra[r.uid]
            show = QTableWidgetItem()
            show.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            show.setCheckState(Qt.Checked if r.visible else Qt.Unchecked)
            self.table.setItem(row, COL["show"], show)
            sw = QTableWidgetItem("■")
            sw.setForeground(QColor(r.color))
            sw.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            sw.setToolTip("Click to change the colour")
            self.table.setItem(row, COL["color"], sw)
            name = QTableWidgetItem(r.name)
            name.setData(Qt.UserRole, r.uid)
            self.table.setItem(row, COL["name"], name)
            role = r.role
            cb = QComboBox()
            cb.addItems(["Signal", "Background"])
            cb.setCurrentText(role)
            cb.currentTextChanged.connect(lambda t, uid=r.uid: self._role_changed(uid, t))
            self.table.setCellWidget(row, COL["role"], cb)
            area = (sp.n_pixels * st.pixel_size_um ** 2) if st.pixel_size_um else None
            y = procs.get(r.uid, (None, None))[0]
            d = sc.descriptors(st.wavelengths, y) if y is not None else None
            vals = {
                "image": st.name,              # full path in the tooltip (below)
                "shape": r.geom.shape,
                "n": str(sp.n_pixels),
                "area": "–" if area is None else f"{area:.1f}",
                "peak": "–" if d is None else f"{d.peak_nm:.1f}",
                "peak_i": "–" if d is None else f"{d.peak_interp_nm:.1f}",
                "fwhm": "–" if d is None or not np.isfinite(d.fwhm_nm) else f"{d.fwhm_nm:.1f}",
                "centroid": "–" if d is None or not np.isfinite(d.centroid_nm) else f"{d.centroid_nm:.1f}",
            }
            for k, v in vals.items():
                it = QTableWidgetItem(v)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if k == "image":
                    it.setToolTip(r.image)
                self.table.setItem(row, COL[k], it)
            if r.uid == self.selected:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        # Re-position the 'Role' drop-downs (cell widgets) after rows were rebuilt.
        self.table.doItemsLayout()
        self._filling_table = False

    def _role_changed(self, uid: int, role: str) -> None:
        """Change the role of an ROI (Signal or Background) and recompute the spectra."""
        rec = next((r for r in self.rois if r.uid == uid), None)
        if rec is not None:
            rec.role = role
            self._spectra_changed()

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        """Apply a name edited in the table, or the Show check box."""
        if self._filling_table:
            return
        row = item.row()
        uid = self.table.item(row, COL["name"]).data(Qt.UserRole)
        rec = next((r for r in self.rois if r.uid == uid), None)
        if rec is None:
            return
        if item.column() == COL["name"]:
            rec.name = item.text().strip() or rec.name
            self._refresh_overlays()
            self._schedule_replot()
        elif item.column() == COL["show"]:
            rec.visible = item.checkState() == Qt.Checked
            self._refresh_overlays()
            self._schedule_replot()

    def _table_clicked(self, row: int, col: int) -> None:
        """Click on the colour cell: choose a new ROI colour."""
        uid = self.table.item(row, COL["name"]).data(Qt.UserRole)
        rec = next((r for r in self.rois if r.uid == uid), None)
        if rec is None:
            return
        if col == COL["color"]:
            c = QColorDialog.getColor(QColor(rec.color), self, "ROI colour")
            if c.isValid():
                rec.color = c.name()
                self._refresh_table()
                self._refresh_overlays()
                self._schedule_replot()

    def _table_selection(self) -> None:
        """Select on the image the ROI selected in the table."""
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            return
        it = self.table.item(min(rows), COL["name"])
        if it is not None:
            self.selected = it.data(Qt.UserRole)
            self._refresh_overlays()

    # =========================================================== spectra
    # ---------------------------------------------------------- wavelength range
    def _wl_extent(self) -> Tuple[float, float]:
        """Lowest and highest channel wavelength of the open stacks."""
        if not self.stacks:
            return 414.0, 693.0
        return (min(float(s.wavelengths.min()) for s in self.stacks.values()),
                max(float(s.wavelengths.max()) for s in self.stacks.values()))

    def _set_range(self, lo: float, hi: float) -> None:
        """Set the wavelength range boxes without triggering a recomputation."""
        for sb, v in ((self.wl_lo, lo), (self.wl_hi, hi)):
            sb.blockSignals(True)
            sb.setValue(int(round(v)))
            sb.blockSignals(False)

    def _range_changed(self, *args) -> None:
        """The user changed the wavelength range: recompute the spectra."""
        self._range_custom = not self._range_is_full()
        self._spectra_changed()

    def _range_full(self) -> None:
        """Button 'Full': analyse all channels again."""
        self._set_range(*self._wl_extent())
        self._range_custom = False
        self._spectra_changed()

    def _sync_range(self) -> None:
        """Follow the wavelength extent of the open stacks unless the user set a range."""
        if not self._range_custom:
            self._set_range(*self._wl_extent())

    def _range_is_full(self) -> bool:
        """True if the range covers every channel."""
        lo, hi = sorted((self.wl_lo.value(), self.wl_hi.value()))
        elo, ehi = self._wl_extent()
        return lo <= elo + 0.5 and hi >= ehi - 0.5

    def _keep(self, wl: np.ndarray) -> Optional[np.ndarray]:
        """Channels inside the analysed range, or None when the full range is analysed."""
        if self._range_is_full():
            return None
        return sc.range_mask(wl, self.wl_lo.value(), self.wl_hi.value())

    def _range_text(self) -> str:
        """Range written in the exports ('all channels' or 'lo-hi nm ...')."""
        if self._range_is_full():
            return "all channels"
        lo, hi = sorted((self.wl_lo.value(), self.wl_hi.value()))
        return (f"{lo}-{hi} nm (channel centres); normalisation and descriptors computed "
                "within this range; processed values outside it left empty")

    # ------------------------------------------------------------ background
    RING_GAP = 2      # px between the ROI edge and the background ring

    def _bg_mode_changed(self) -> None:
        """Background mode changed: show the ring width if needed and recompute."""
        self.ring_width.setVisible(self.bg_mode.currentIndex() == 3)
        self._spectra_changed()

    def _dark_background(self, path: str) -> sc.Spectrum:
        """Background spectrum of a stack = mean of its 1 % dimmest pixels (cached)."""
        if path not in self._dark_bg:
            data = self.stacks[path].data
            self._dark_bg[path] = sc.roi_spectrum(data, sc.dark_pixels_mask(data))
        return self._dark_bg[path]

    def _ring_background(self, r: RoiRecord) -> sc.Spectrum:
        """Background spectrum of one ROI = mean of a ring around it, other ROIs excluded (cached).
        """
        others = tuple(sorted(o.uid for o in self.rois if o.image == r.image and o.uid != r.uid))
        key = (r.uid, self.RING_GAP, self.ring_width.value(), others)
        if key not in self._ring_bg:
            st = self.stacks[r.image]
            mask = sc.roi_mask(st.shape_yx, r.geom)
            excl = np.zeros(st.shape_yx, dtype=bool)
            for o in self.rois:
                if o.image == r.image and o.uid != r.uid:
                    excl |= sc.roi_mask(st.shape_yx, o.geom)
            ring = sc.ring_mask(mask, self.RING_GAP, self.ring_width.value(), exclude=excl)
            self._ring_bg[key] = sc.roi_spectrum(st.data, ring)
        return self._ring_bg[key]

    def _image_background(self, path: str) -> Optional[np.ndarray]:
        """Per-image background for the cursor and image-mean curves (None: not applicable)."""
        mode = self.bg_mode.currentIndex()
        if mode == 1:
            return self._bg_by_image.get(path)
        if mode == 2 and path in self.stacks:
            return self._dark_background(path).mean
        return None             # ring: defined per ROI only

    def _processed(self) -> Dict[int, Tuple[np.ndarray, Optional[np.ndarray]]]:
        """uid -> (processed mean, processed SD) following the current settings."""
        method = self.norm.currentText()
        mode = self.bg_mode.currentIndex()
        bg_by_image: Dict[str, np.ndarray] = {}
        if mode == 1:
            for path in {r.image for r in self.rois}:
                bgs = [r for r in self.rois if r.image == path and r.role == "Background"]
                if bgs:
                    # Pixel-weighted mean of all background ROIs of this image.
                    w = np.array([self.spectra[b.uid].n_pixels for b in bgs], dtype=float)
                    m = np.array([self.spectra[b.uid].mean for b in bgs])
                    bg_by_image[path] = (m * w[:, None]).sum(axis=0) / w.sum()
        elif mode == 2:
            for path in {r.image for r in self.rois}:
                bg_by_image[path] = self._dark_background(path).mean
        self._bg_by_image = bg_by_image
        out = {}
        negative = []
        for r in self.rois:
            sp = self.spectra[r.uid]
            mean = sp.mean.copy()
            bg = None
            if r.role != "Background":
                if mode in (1, 2):
                    bg = bg_by_image.get(r.image)
                elif mode == 3:
                    ring = self._ring_background(r)
                    bg = ring.mean if ring.n_pixels > 0 else None
            if bg is not None:
                mean = mean - bg
            sd = sp.sd
            keep = self._keep(self.stacks[r.image].wavelengths)
            if keep is not None:
                mean, sd = sc.restrict(mean, keep), sc.restrict(sd, keep)
            if (bg is not None and r.visible and np.isfinite(mean).any()
                    and np.nanmax(mean) <= 0):
                negative.append(r.name)
            out[r.uid] = sc.normalise(mean, sd, method)
        self._negative_rois = negative
        return out

    # --- Curves to plot ------------------------------------------------------
    def _series(self) -> List[Series]:
        """Curves to plot: the processed spectrum of every visible ROI, plus the image mean spectra
        if requested.
        """
        procs = self._processed()
        multi = len({r.image for r in self.rois if r.visible}) > 1
        out = []
        for r in self.rois:
            if not r.visible:
                continue
            st = self.stacks[r.image]
            y, sd = procs[r.uid]
            label = f"{r.name} ({short_name(st.name)})" if multi else r.name
            if r.role == "Background":
                label += " [bg]"
            d = sc.descriptors(st.wavelengths, y)
            out.append(Series(label, st.wavelengths, y, sd, r.color, d.peak_interp_nm))
        for tag, path, y, sd in self._tissue_series():
            st = self.stacks[path]
            d = sc.descriptors(st.wavelengths, y)
            out.append(Series(f"Image mean, {tag} ({short_name(st.name)})", st.wavelengths, y, sd,
                              "#ffffff", d.peak_interp_nm, ls="-" if tag == "A" else "--"))
        return out

    def _tissue_spectrum(self, path: str) -> sc.Spectrum:
        """Mean spectrum of all tissue pixels of a stack (Otsu threshold), cached."""
        if path not in self._tissue:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._tissue[path] = sc.tissue_spectrum(self.stacks[path].data)
            finally:
                QApplication.restoreOverrideCursor()
        return self._tissue[path]

    def _tissue_series(self) -> List[tuple]:
        """(pane tag, path, processed mean, processed SD) of the displayed images' tissue."""
        if not self.show_mean.isChecked():
            return []
        out, seen = [], set()
        for k, tag in ((0, "A"), (1, "B")):
            if k == 1 and not self.split.isChecked():
                continue
            path = self.panes[k].current_path()
            if not path or path not in self.stacks or path in seen:
                continue
            seen.add(path)
            sp = self._tissue_spectrum(path)
            mean = sp.mean.copy()
            bg = self._image_background(path)
            if bg is not None:
                mean = mean - bg
            sd = sp.sd
            keep = self._keep(self.stacks[path].wavelengths)
            if keep is not None:
                mean, sd = sc.restrict(mean, keep), sc.restrict(sd, keep)
            y, sd = sc.normalise(mean, sd, self.norm.currentText())
            out.append((tag, path, y, sd))
        return out

    def _spectra_changed(self) -> None:
        """Something affecting the spectra changed: refresh the table and the plot."""
        self._refresh_table()
        self._schedule_replot()

    def _schedule_replot(self, *args) -> None:
        """Redraw the plot shortly (several quick changes give one redraw)."""
        self._replot_timer.start()

    def _replot(self) -> None:
        """Redraw the spectrum plot and show a warning for ROIs that became entirely negative."""
        series = self._series()
        warn = "entirely negative after background subtraction"
        if getattr(self, "_negative_rois", None):
            self.status.showMessage(
                f"{', '.join(self._negative_rois)}: {warn} (the background is brighter than this "
                "ROI in every channel). Choose a darker background region, or the ring mode.",
                12000)
        elif warn in self.status.currentMessage():
            self.status.clearMessage()
        live = self._live if self.live.isChecked() else None
        self._plot_series = series + ([live] if live is not None else [])
        self._hover_line = None                 # the figure is redrawn from scratch
        draw_spectra(self.fig, series, sc.y_label(self.norm.currentText()),
                     live=live, dark=True,
                     show_sd=self.show_sd.isChecked(), show_peaks=self.show_peaks.isChecked(),
                     color_strip=self.show_strip.isChecked())
        if self._hover_x is not None:           # mouse still on the plot: refresh the readout
            self._show_hover(self._hover_x)
        self.canvas.draw_idle()

    # --- Plot readout under the mouse ----------------------------------------
    def _plot_hover(self, event) -> None:
        """Read the wavelength under the mouse and each curve's value at the nearest channel."""
        if event.inaxes is None or event.xdata is None:
            self._plot_leave()
            return
        self._show_hover(float(event.xdata))

    def _show_hover(self, x: float) -> None:
        """Write the wavelength x, the nearest channel and every curve's value under the plot, and
        move the dotted line.
        """
        if not self._plot_series or not self.fig.axes:
            self._plot_leave()
            return
        ax = self.fig.axes[0]
        parts, chan = [], None
        for s in self._plot_series:
            wl, y = np.asarray(s.wl, dtype=float), np.asarray(s.y, dtype=float)
            ok = np.isfinite(y)
            if not ok.any():
                continue
            i = int(np.argmin(np.where(ok, np.abs(wl - x), np.inf)))
            chan = chan if chan is not None else (i, wl[i])
            v = y[i]
            txt = f"{v:.0f}" if abs(v) >= 100 else f"{v:.3f}"
            if abs(wl[i] - chan[1]) > 1e-6:         # stack with another calibration
                txt += f" (at {wl[i]:.0f} nm)"
            parts.append(f"<span style='color:{s.color}'>■</span> {s.label}: {txt}")
        if chan is None:
            self._plot_leave()
            return
        self._hover_x = x
        i, w = chan
        self.readout.setText(f"<b>{x:.1f} nm</b> · nearest channel {i + 1} ({w:.0f} nm): "
                             + " · ".join(parts))
        if self._hover_line is None or self._hover_line.axes is not ax:
            self._hover_line = ax.axvline(w, color="#c9d1dc", lw=0.8, ls=":", alpha=0.9)
        else:
            self._hover_line.set_xdata([w, w])
            self._hover_line.set_visible(True)
        self.canvas.draw_idle()

    def _plot_leave(self, *args) -> None:
        """Mouse left the plot: reset the readout and hide the dotted line."""
        self._hover_x = None
        self.readout.setText("Move the mouse over the plot to read wavelengths and values.")
        if self._hover_line is not None:
            self._hover_line.set_visible(False)
            self.canvas.draw_idle()

    # --- Live cursor spectrum ------------------------------------------------
    def _live_toggled(self, on: bool) -> None:
        """Turn the live cursor spectrum on or off."""
        if not on:
            self._live = None
        self._schedule_replot()

    def _hovered(self, k: int, x: float, y: float) -> None:
        """Mouse over an image: show pixel information and, if enabled, plot the live spectrum
        under the cursor.
        """
        path = self.panes[k].current_path()
        if not path or path not in self.stacks or not np.isfinite(x):
            self.panes[k].info.setText(" ")
            if self._live is not None and self.live.isChecked():
                self._live = None
                self._schedule_replot()
            return
        st = self.stacks[path]
        i, j = int(y), int(x)
        px = st.data[:, i, j]
        c = int(np.argmax(px))
        um = f" ({x * st.pixel_size_um:.1f}, {y * st.pixel_size_um:.1f} µm)" if st.pixel_size_um else ""
        self.panes[k].info.setText(
            f"x {j}, y {i}{um} · brightest channel {c + 1} ({st.wavelengths[c]:.0f} nm) · "
            f"max {int(px.max())}")
        if self.live.isChecked():
            spec = sc.cursor_spectrum(st.data, x, y, self.live_size.currentData())
            if spec is not None:
                bg = self._image_background(path)
                if bg is not None:
                    spec = spec - bg
                keep = self._keep(st.wavelengths)
                if keep is not None:
                    spec = sc.restrict(spec, keep)
                yy, _ = sc.normalise(spec, None, self.norm.currentText())
                n = self.live_size.currentData()
                self._live = Series(f"cursor {n}×{n} px", st.wavelengths, yy, None, "#c9d1dc")
                self._schedule_replot()

    # ============================================================ export
    def _export_rows(self) -> List[ExportRow]:
        """One ExportRow per visible ROI (and per image mean spectrum) with processed and raw
        spectra and descriptors.
        """
        procs = self._processed()
        rows = []
        for r in self.rois:
            if not r.visible:
                continue
            st = self.stacks[r.image]
            sp = self.spectra[r.uid]
            y, sd = procs[r.uid]
            d = sc.descriptors(st.wavelengths, y)
            name = r.name + (" [background]" if r.role == "Background" else "")
            rows.append(ExportRow(
                name=name, image=r.image, shape=r.geom.shape, n_pixels=sp.n_pixels,
                area_um2=(sp.n_pixels * st.pixel_size_um ** 2) if st.pixel_size_um else None,
                centroid_xy=self.masks_centroid[r.uid], wl=st.wavelengths, raw_mean=sp.mean,
                raw_sd=sp.sd, proc=y, proc_sd=sd, peak_nm=d.peak_nm,
                peak_interp_nm=d.peak_interp_nm, fwhm_nm=d.fwhm_nm, centroid_nm=d.centroid_nm,
                wl_source=("file metadata" if st.wl_source == "file"
                           else "lab acquisition settings (414 nm + 9 nm per channel)"
                           if self.cal == Calibration()
                           else "user calibration" + ("" if self.cal.verified else " (NOT verified)"))))
        for tag, path, y, sd in self._tissue_series():
            st = self.stacks[path]
            sp = self._tissue[path]
            d = sc.descriptors(st.wavelengths, y)
            rows.append(ExportRow(
                name=f"Image mean {tag}", image=path, shape="whole tissue (Otsu threshold)",
                n_pixels=sp.n_pixels,
                area_um2=(sp.n_pixels * st.pixel_size_um ** 2) if st.pixel_size_um else None,
                centroid_xy=(float("nan"), float("nan")), wl=st.wavelengths, raw_mean=sp.mean,
                raw_sd=sp.sd, proc=y, proc_sd=sd, peak_nm=d.peak_nm,
                peak_interp_nm=d.peak_interp_nm, fwhm_nm=d.fwhm_nm, centroid_nm=d.centroid_nm,
                wl_source=rows[0].wl_source if rows else (
                    "file metadata" if st.wl_source == "file" else self.cal.describe(st.n_channels))))
        return rows

    def _bg_text(self) -> str:
        """Background method written in the exports."""
        mode = self.bg_mode.currentIndex()
        if mode == 1:
            return "per image, pixel-weighted mean spectrum of the ROI(s) with role 'Background'"
        if mode == 2:
            return "per image, mean spectrum of the 1 % dimmest pixels (summed intensity)"
        if mode == 3:
            return (f"per ROI, mean spectrum of a ring {self.RING_GAP} to "
                    f"{self.RING_GAP + self.ring_width.value()} px outside its edge "
                    "(other ROIs excluded)")
        return "none"

    def _calib_text(self) -> str:
        """Wavelength calibration written in the exports."""
        n = max((s.n_channels for s in self.stacks.values()), default=32)
        return self.cal.describe(n) + ("" if self.cal.verified else " (not verified by the user)")

    def export_excel(self) -> None:
        """Export spectra, ROI table and settings to an Excel file."""
        rows = self._export_rows()
        if not rows:
            QMessageBox.information(self, "Export", "No visible ROI to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export spectra", self._default_name("spectra.xlsx"),
                                              "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_excel(path, rows, self.norm.currentText(), self._bg_text(), self._calib_text(),
                         extra=[("Analysed wavelength range", self._range_text())])
        except PermissionError:
            QMessageBox.warning(self, "Export", "The file is open in another program.")
            return
        self.status.showMessage(f"{len(rows)} spectra exported to {path}", 8000)

    def export_csv(self) -> None:
        """Export the processed spectra to a CSV file."""
        rows = self._export_rows()
        if not rows:
            QMessageBox.information(self, "Export", "No visible ROI to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export spectra (CSV)", self._default_name("spectra.csv"),
                                              "CSV (*.csv)")
        if path:
            export_csv(path, rows, self.norm.currentText(), self._bg_text())
            self.status.showMessage(f"Spectra exported to {path}", 8000)

    def export_figure(self) -> None:
        """Export the spectrum plot as a publication figure (size, resolution and content chosen in
        a dialog).
        """
        series = self._series()
        if not series:
            QMessageBox.information(self, "Export", "No visible ROI to plot.")
            return
        dlg = FigureDialog(self, self._fig_defaults)
        if dlg.exec() != FigureDialog.Accepted:
            return
        v = dlg.values()
        self._fig_defaults = v
        ext = FIGURE_FORMATS[v["format"]]
        path, _ = QFileDialog.getSaveFileName(self, "Save figure", self._default_name("spectrum" + ext),
                                              f"{v['format']} (*{ext})")
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        fig = Figure(figsize=(v["width"] / 25.4, v["height"] / 25.4))
        draw_spectra(fig, series, sc.y_label(self.norm.currentText()), show_sd=v["sd"],
                     show_peaks=v["peaks"], color_strip=v["strip"], legend=v["legend"],
                     font_size=v["font"], line_width=1.2, title=v["title"])
        save_figure(fig, path, v["dpi"])
        self.status.showMessage(f"Figure saved: {path}", 8000)

    def export_image(self, k: int) -> None:
        """Save the image of pane k with its ROI outlines."""
        pane = self.panes[k]
        if not pane.current_path():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save image with ROIs",
            self._default_name(f"{self.stacks[pane.current_path()].name}_ROIs.png"),
            "PNG (*.png);;TIFF (*.tif)")
        if path:
            pane.viewer.render_image().save(path)
            self.status.showMessage(f"Image saved: {path}", 8000)

    def _default_name(self, name: str) -> str:
        """Default path of an exported file, in the last export folder."""
        return os.path.join(self.settings.value("export_dir", os.path.expanduser("~")), name)

    # ======================================================= ROI files
    def save_roi_set(self) -> None:
        """Save every ROI and the analysis settings to a JSON file."""
        if not self.rois:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save ROI set", self._default_name("rois.json"),
                                              "ROI set (*.json)")
        if path:
            save_rois(path, self.rois, {"software": f"Spectral Analyzer {__version__}",
                                        "calibration": self._calib_text()})

    def load_roi_set(self) -> None:
        """Load ROIs from a JSON file; their stacks are opened (or matched by file name) and ROIs whose
        image is unavailable are skipped.
        """
        path, _ = QFileDialog.getOpenFileName(self, "Load ROI set", "", "ROI set (*.json)")
        if not path:
            return
        try:
            items = load_rois(path)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Load ROI set", f"Unreadable file:\n{exc}")
            return
        missing = sorted({it["image"] for it in items if it["image"] not in self.stacks
                          and os.path.isfile(it["image"])})
        if missing:
            self.open_files(missing)
        skipped = 0
        for it in items:
            target = it["image"] if it["image"] in self.stacks else next(
                (p for p in self.stacks if os.path.basename(p) == it["image_name"]), None)
            if target is None:
                skipped += 1
                continue
            rec = self._add_roi(target, it["geom"], it["name"], it["color"], it["visible"], select=False)
            if rec is not None:
                rec.role = it["role"]
        self._spectra_changed()
        if skipped:
            QMessageBox.information(self, "Load ROI set",
                                    f"{skipped} ROI(s) skipped: their image is not open.")

    def import_imagej(self) -> None:
        """Import ImageJ ROIs (.roi or RoiSet.zip) onto the image of the left pane."""
        target = self.panes[0].current_path()
        if not target:
            QMessageBox.information(self, "Import", "Open the image first (left pane).")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import ImageJ ROIs", "",
                                              "ImageJ ROI (*.roi *.zip)")
        if not path:
            return
        try:
            rois, skipped = import_imagej(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Import", f"Could not read the ROI file:\n{exc}")
            return
        for name, geom in rois:
            self._add_roi(target, geom, name, None, select=False)
        msg = f"{len(rois)} ROI(s) imported."
        if skipped:
            msg += "\nSkipped:\n" + "\n".join(skipped)
        QMessageBox.information(self, "Import", msg)

    # ======================================================= calibration
    def edit_calibration(self) -> None:
        """Edit the wavelength calibration and recompute everything with the new wavelengths."""
        n = max((s.n_channels for s in self.stacks.values()), default=32)
        dlg = CalibrationDialog(self.cal, n, self)
        if dlg.exec() != CalibrationDialog.Accepted:
            return
        self.cal = dlg.calibration()
        self._save_calibration()
        for st in self.stacks.values():
            if st.wl_source == "calibration":
                st.wavelengths = self.cal.wavelengths(st.n_channels)
        self._render_cache.clear()
        self._render_all()
        self._update_banner()
        self._sync_range()
        self._spectra_changed()
        self._refresh_maps_window()

    def _update_banner(self) -> None:
        """Text of the Wavelengths box: where the wavelengths come from and whether they were
        checked.
        """
        uses_cal = any(s.wl_source == "calibration" for s in self.stacks.values()) or not self.stacks
        n = max((s.n_channels for s in self.stacks.values()), default=32)
        if uses_cal and not self.cal.verified:
            self.banner.setText(
                "<span style='color:#f0b45a'><b>Wavelengths are not stored in these files.</b> "
                f"Calibration applied: {self.cal.describe(n)}. Not verified: check it against the "
                "acquisition settings (ZEN).</span>")
        elif uses_cal and self.cal == Calibration():
            self.banner.setText(f"Calibration: {self.cal.describe(n)}. Lab acquisition settings "
                                "(excitation 405 nm, emission from 414 nm, 9 nm per channel).")
        elif uses_cal:
            self.banner.setText(f"Calibration: {self.cal.describe(n)} (verified).")
        else:
            self.banner.setText("Wavelengths read from the file metadata.")

    def show_about(self) -> None:
        """Show the About dialog (version, authors, acknowledgements)."""
        AboutDialog(
            f"<h3>Spectral Analyzer {__version__}</h3>"
            "<p>Region-of-interest autofluorescence spectra from spectral (lambda) stacks, "
            "normalised and exported; whole-image spectral comparison maps.</p>"
            + credits_html(), self).exec()

    def show_help(self) -> None:
        """Show the quick guide."""
        QMessageBox.information(self, "Quick guide", (
            "1. Open one or more spectral stacks (TIFF, channels = wavelengths), or drop them on the "
            "window.\n"
            "2. Choose a drawing tool and outline a structure: freehand (drag), polygon (click "
            "vertices, double-click or right-click to close), rectangle, ellipse, point.\n"
            "3. The mean spectrum of the ROI is plotted immediately (normalised to max = 1 by "
            "default). Rename ROIs in the table; untick 'Show' to hide one.\n"
            "4. To compare a pathological and a control sample, tick 'Compare two images' and draw "
            "ROIs on both. The left panel folds away; ☰ at the top left (Ctrl+B) brings it back.\n"
            "5. Optional background subtraction (menu 'Background'): ROIs given the role "
            "'Background', the 1 % dimmest pixels of the image, or an automatic ring around each "
            "ROI (local background, no drawing needed).\n"
            "6. Export the spectra (Excel), the figure (TIFF 600 dpi for publication) or the image "
            "with its ROIs.\n"
            "7. Bonus: Analysis › Spectral difference maps compares the spectra of two whole "
            "images (heat maps, no ROI needed).\n\n"
            "Mouse: wheel = zoom, middle button = pan, Esc = cancel the current outline, "
            "Delete = remove the selected ROI, Ctrl+Z = undo the last ROI drawing or deletion."))
