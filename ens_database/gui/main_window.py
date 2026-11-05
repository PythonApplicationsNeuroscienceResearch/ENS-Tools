"""Main window of the ENS Image Database."""
from __future__ import annotations

import os
import subprocess
import sys
import datetime as dt
from typing import List, Optional

import pandas as pd
from PySide6.QtCore import QFileSystemWatcher, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QProgressDialog, QSlider,
                               QSplitter, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

from .. import COPYRIGHT_SHORT, __version__, credits_html, imagefiles
from ..database import Database
from ..xlsx_patch import FileLockedError, XlsxWriteError
from .common import CATEGORY_COLORS
from .dialogs import (AboutDialog, ClinicalGroupsDialog, EditDialog, IntegrityDialog,
                      LocationsDialog, confirm_edits)
from .filter_panel import FilterPanel
from .gallery import GalleryModel, GalleryView, TableModel, TableView
from .identity_card import IdentityCard
from .theme import fit_to_screen
from .thumbnails import ThumbnailProvider

COLOR_BY = [
    ("Clinical group", "info:clinical_group"),
    ("Diagnosis (detailed)", "info:diagnosis"),
    ("Majority diagnosis", "consensus:majority"),
    ("Plexus (DH)", "DH:plexus"),
    ("No colour", ""),
]


def _is_app_bundle(path: str) -> bool:
    """True for a macOS .app bundle (launched with 'open' rather than executed)."""
    return sys.platform == "darwin" and path.endswith(".app") and os.path.isdir(path)


def _launch_command(program: str, stack: str) -> List[str]:
    """Command that starts the Spectral Analyzer on a stack (a macOS .app goes through 'open')."""
    if _is_app_bundle(program):
        return ["open", "-n", "-a", program, "--args", stack]
    return [program, stack]


class MainWindow(QMainWindow):
    """Main window of the ENS Image Database: filters, gallery/table, identity card and menus."""
    def __init__(self):
        """Build the window (filter panel, gallery, table, identity card), the menus and the Excel
        file watcher, then load the data.
        """
        super().__init__()
        self.setWindowTitle("ENS Image Database")
        fit_to_screen(self, 1500, 920)
        self.settings = QSettings("SPPIN", "ENS Image Database")
        self.db: Optional[Database] = None
        self.filtered: List[str] = []
        self._current_key: Optional[str] = None
        self._editing = False
        self._loaded_sig = None

        self.thumbs = ThumbnailProvider(self)
        self.gallery_model = GalleryModel(self.thumbs, self)
        self.table_model = TableModel(self)

        # ---------------------------------------------------------- layout
        self.filters = FilterPanel()
        self.filters.changed.connect(self.apply_filters)

        self.gallery = GalleryView(self.gallery_model)
        self.gallery.openRequested.connect(self.open_card)
        self.table = TableView(self.table_model)
        self.table.openRequested.connect(self.open_card)

        overview = QWidget()
        ol = QVBoxLayout(overview)
        ol.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.count_label = QLabel()
        self.count_label.setObjectName("title")
        bar.addWidget(self.count_label)
        bar.addStretch(1)
        bar.addWidget(QLabel("Colour by"))
        self.color_by = QComboBox()
        for text, col in COLOR_BY:
            self.color_by.addItem(text, col)
        self.color_by.currentIndexChanged.connect(self._color_changed)
        bar.addWidget(self.color_by)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Size"))
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(90, 260)
        self.size_slider.setValue(int(self.settings.value("thumb_size", 150)))
        self.size_slider.setFixedWidth(120)
        self.size_slider.valueChanged.connect(self._thumb_size)
        bar.addWidget(self.size_slider)
        self.btn_columns = QToolButton()
        self.btn_columns.setText("Table columns ▾")
        self.btn_columns.setPopupMode(QToolButton.InstantPopup)
        bar.addWidget(self.btn_columns)
        ol.addLayout(bar)
        # Colour legend on its own (wrapping) line so that it never widens the window.
        self.legend = QLabel()
        self.legend.setWordWrap(True)
        ol.addWidget(self.legend)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.gallery, "Gallery")
        self.tabs.addTab(self.table, "Table")
        ol.addWidget(self.tabs, 1)

        self.card = IdentityCard()
        self.card.backRequested.connect(self.show_overview)
        self.card.navigate.connect(self._navigate)
        self.card.editRequested.connect(self.edit_image)
        self.card.spectralRequested.connect(self.open_spectral)

        self.stack = QStackedWidget()
        self.stack.addWidget(overview)
        self.stack.addWidget(self.card)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.filters)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([395, 1105])
        self.setCentralWidget(split)

        self._build_menus()
        self.status = self.statusBar()
        self.status_label = QLabel()
        self.status.addPermanentWidget(self.status_label)
        credit = QLabel(COPYRIGHT_SHORT)
        credit.setObjectName("muted")
        credit.setToolTip("Help › About: authors and GitHub accounts")
        self.status.addPermanentWidget(credit)

        # ---------------------------------------------- live link to Excel
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._file_changed)
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(1500)
        self._reload_timer.timeout.connect(self._reload_if_changed)
        self._poll = QTimer(self)               # safety net for network drives
        self._poll.setInterval(5000)
        self._poll.timeout.connect(self._reload_if_changed)

        self._thumb_size(self.size_slider.value())
        QTimer.singleShot(0, self._startup)

    # ================================================================ menus
    def _build_menus(self) -> None:
        """Create the File, Data and Help menus."""
        mb = self.menuBar()
        m = mb.addMenu("&File")
        a = QAction("Data locations…", self)
        a.triggered.connect(self.choose_locations)
        m.addAction(a)
        a = QAction("Reload from Excel", self)
        a.setShortcut(QKeySequence("F5"))
        a.triggered.connect(lambda: self.load(keep_view=True))
        m.addAction(a)
        m.addSeparator()
        a = QAction("Export current selection as Excel table…", self)
        a.setShortcut(QKeySequence("Ctrl+E"))
        a.triggered.connect(self.export_table)
        m.addAction(a)
        a = QAction("Copy image files of current selection to a folder…", self)
        a.triggered.connect(self.export_images)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Quit", self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)

        m = mb.addMenu("&Data")
        a = QAction("Clinical group of each slide…", self)
        a.triggered.connect(self.edit_groups)
        m.addAction(a)
        a = QAction("Data integrity report…", self)
        a.triggered.connect(self.show_integrity)
        m.addAction(a)
        a = QAction("Open backups folder", self)
        a.triggered.connect(self._open_backups)
        m.addAction(a)

        m = mb.addMenu("&Help")
        a = QAction("About", self)
        a.triggered.connect(self._about)
        m.addAction(a)

    # ============================================================= loading
    def _startup(self) -> None:
        """At start: load the data if the saved paths exist, otherwise ask for them."""
        excel = self.settings.value("excel", "")
        images = self.settings.value("images", "")
        if not (excel and os.path.isfile(excel) and images and os.path.isdir(images)):
            if not self.choose_locations():
                self.status.showMessage("No database loaded. Use File › Data locations.")
                return
        else:
            self.load()

    def choose_locations(self) -> bool:
        """Ask for the workbook and folders; returns True if the user confirmed."""
        dlg = LocationsDialog(self.settings.value("excel", ""), self.settings.value("images", ""),
                              self.settings.value("spectral", ""), self)
        if dlg.exec() != LocationsDialog.Accepted:
            return False
        excel, images, spectral = dlg.values()
        self.settings.setValue("excel", excel)
        self.settings.setValue("images", images)
        self.settings.setValue("spectral", spectral)
        self.thumbs.clear()
        self.load()
        return True

    @staticmethod
    def _signature(path: str):
        """(size, modification time) of a file, used to detect that Excel saved it."""
        try:
            st = os.stat(path)
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    def load(self, keep_view: bool = False) -> None:
        """Load the database from the saved paths and refresh every view (keep_view keeps the
        current selection).
        """
        excel = self.settings.value("excel", "")
        images = self.settings.value("images", "")
        spectral = self.settings.value("spectral", "")
        if not excel:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            db = Database(excel, images, spectral)
            db.load()
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Loading failed",
                                 f"The database could not be loaded:\n\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        self.db = db
        self._loaded_sig = self._signature(excel)
        if self.watcher.files():
            self.watcher.removePaths(self.watcher.files())
        self.watcher.addPath(excel)
        self._poll.start()
        self.filters.set_database(db)
        self._build_column_menu()
        self.apply_filters()
        if keep_view and self.stack.currentWidget() is self.card and self._current_key in db.df.index:
            self.open_card(self._current_key)
        n_issue = len(db.issues)
        self.status_label.setText(
            f"{os.path.basename(excel)} · {len(db.df)} images · {len(db.raters)} pathologists · "
            f"loaded {dt.datetime.now():%H:%M:%S} · live link on")
        if n_issue:
            self.status.showMessage(
                f"{n_issue} data integrity note(s): see Data › Data integrity report.", 10000)

    def _file_changed(self, path: str) -> None:
        """Called by the file watcher: check for a reload after a short delay."""
        self._reload_timer.start()

    def _reload_if_changed(self) -> None:
        """If the workbook changed on disk, wait until it is fully written before reloading."""
        if self.db is None or self._editing:
            return
        excel = self.db.excel_path
        if excel not in self.watcher.files() and os.path.exists(excel):
            self.watcher.addPath(excel)        # Excel replaces the file when saving
        sig = self._signature(excel)
        if sig is not None and sig != self._loaded_sig:
            # Wait until the file is stable (Excel writes in several steps).
            QTimer.singleShot(700, lambda s=sig: self._reload_if_stable(s))

    def _reload_if_stable(self, sig) -> None:
        """Reload once the workbook has not changed for a moment (Excel saves in several steps)."""
        if self.db is None or self._editing:
            return
        if self._signature(self.db.excel_path) == sig and sig != self._loaded_sig:
            self.load(keep_view=True)
            self.status.showMessage("Excel file changed on disk: database reloaded.", 6000)

    # ============================================================ filtering
    def apply_filters(self) -> None:
        """Apply the filter panel to the table and update the gallery, table and image count."""
        if self.db is None:
            return
        try:
            mask = self.filters.mask()
        except Exception as exc:  # noqa: BLE001
            self.status.showMessage(f"Filter error: {exc}", 8000)
            return
        self.filtered = list(self.db.df.index[mask.values])
        self.gallery_model.set_data(self.db, self.filtered)
        self.table_model.set_data(self.db, self.filtered)
        self.count_label.setText(f"{len(self.filtered)} of {len(self.db.df)} images")
        self._update_legend()

    def _color_changed(self) -> None:
        """Recolour the gallery tiles by the chosen category ('Colour by')."""
        self.gallery_model.color_by = self.color_by.currentData()
        self.gallery.viewport().update()
        self._update_legend()

    def _update_legend(self) -> None:
        """Show the colour legend of the chosen category."""
        col = self.color_by.currentData()
        if not col or self.db is None or col not in self.db.df:
            self.legend.setText("")
            return
        vals = [v for v in self.db.categories(col)]
        html = " ".join(f"<span style='color:{CATEGORY_COLORS.get(v, '#6c8ebf')}'>■</span> {v}"
                        for v in vals)
        self.legend.setText(html)

    def _thumb_size(self, px: int) -> None:
        """Change and remember the thumbnail size."""
        self.gallery.set_thumb_size(px)
        self.settings.setValue("thumb_size", px)

    def _build_column_menu(self) -> None:
        """Menu of check boxes to choose the columns of the Table tab."""
        menu = QMenu(self)
        current = set(self.table_model.columns)
        for cid, spec in self.db.fields.items():
            a = QAction(f"{spec.group} · {spec.label}", menu)
            a.setCheckable(True)
            a.setChecked(cid in current)
            a.setData(cid)
            a.toggled.connect(self._columns_changed)
            menu.addAction(a)
        self._column_menu = menu
        self.btn_columns.setMenu(menu)

    def _columns_changed(self) -> None:
        """Apply the columns ticked in the Table columns menu."""
        cols = [a.data() for a in self._column_menu.actions() if a.isChecked()]
        self.table_model.set_columns(cols)

    # ========================================================== identity card
    def open_card(self, key: str) -> None:
        """Show the identity card of one image."""
        if self.db is None or key not in self.db.df.index:
            return
        self._current_key = key
        pos = ""
        if key in self.filtered:
            pos = f"{self.filtered.index(key) + 1} / {len(self.filtered)}"
        self.card.show_image(self.db, key, pos)
        self.stack.setCurrentWidget(self.card)
        self.filters.setVisible(False)          # give the identity card the full width

    def show_overview(self) -> None:
        """Go back from the identity card to the gallery/table."""
        self.stack.setCurrentIndex(0)
        self.filters.setVisible(True)
        if self._current_key in self.filtered:
            row = self.gallery_model._row.get(self._current_key)
            if row is not None:
                idx = self.gallery_model.index(row)
                self.gallery.setCurrentIndex(idx)
                self.gallery.scrollTo(idx)

    def _navigate(self, step: int) -> None:
        """Previous / next image on the identity card, in the displayed order."""
        order = self._display_order()
        if not order or self._current_key is None:
            return
        i = order.index(self._current_key) if self._current_key in order else -1
        j = min(max(i + step, 0), len(order) - 1)
        if j != i:
            self.open_card(order[j])

    def _display_order(self) -> List[str]:
        # Navigate in the order of the table when the table tab is active.
        """Order of the images as currently displayed (table order if the table tab is active)."""
        return list(self.table_model.keys) if self.tabs.currentWidget() is self.table else self.filtered

    # ================================================================ editing
    def edit_image(self, key: str) -> None:
        """Edit the values of one image and write them into the workbook (with backup and checks).
        """
        if self.db is None:
            return
        self._editing = True
        try:
            dlg = EditDialog(self.db, key, self)
            if dlg.exec() != EditDialog.Accepted:
                return
            edits = dlg.edits()
            if not edits:
                self.status.showMessage("No change.", 4000)
                return
            if not confirm_edits(self, self.db, edits):
                return
            if self._signature(self.db.excel_path) != self._loaded_sig:
                QMessageBox.warning(self, "Excel file changed",
                                    "The Excel file was modified by another program since it was "
                                    "loaded. It will be reloaded; please redo your edit.")
                self._editing = False
                self.load(keep_view=True)
                return
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                backup = self.db.write_edits(edits)
            finally:
                QApplication.restoreOverrideCursor()
        except FileLockedError as exc:
            QMessageBox.warning(self, "Excel file is open", str(exc))
            return
        except (XlsxWriteError, KeyError, ValueError) as exc:
            QMessageBox.critical(self, "Write failed", str(exc))
            return
        finally:
            self._editing = False
        self._loaded_sig = self._signature(self.db.excel_path)
        self.filters.set_database(self.db)
        self.apply_filters()
        self.open_card(key)
        self.status.showMessage(f"{len(edits)} change(s) written. Backup: {backup}", 10000)

    def edit_groups(self) -> None:
        """Change the clinical group of slides and save the overrides."""
        if self.db is None:
            return
        dlg = ClinicalGroupsDialog(self.db, self)
        if dlg.exec() == ClinicalGroupsDialog.Accepted:
            try:
                self.db.set_slide_groups(dlg.mapping())
            except OSError as exc:
                QMessageBox.critical(self, "Clinical groups", f"Could not save:\n{exc}")
                return
            self.filters.set_database(self.db)
            self.apply_filters()

    def show_integrity(self) -> None:
        """Open the data integrity report."""
        if self.db is not None:
            IntegrityDialog(self.db, self).exec()

    # =============================================================== export
    def _export_keys(self) -> List[str]:
        """Selected images if any, otherwise every image of the current selection."""
        sel = self.table.selected_keys() if self.tabs.currentWidget() is self.table \
            else self.gallery.selected_keys()
        return sel if len(sel) > 1 else self._display_order()

    def export_table(self) -> None:
        """Export the selected (or all filtered) images to an Excel table with every field and the
        query used.
        """
        if self.db is None:
            return
        keys = self._export_keys()
        path, _ = QFileDialog.getSaveFileName(self, "Export table", "ENS_selection.xlsx",
                                              "Excel (*.xlsx)")
        if not path:
            return
        cols = [c for c in self.db.fields if c in self.db.df.columns]
        out = self.db.df.loc[keys, cols].copy()
        out.columns = [f"{self.db.fields[c].group} | {self.db.fields[c].label}" for c in cols]
        meta = pd.DataFrame({
            "Item": ["Exported", "Source workbook", "Image folder", "Selection", "Images (n)",
                     "Application"],
            "Value": [dt.datetime.now().isoformat(timespec="seconds"), self.db.excel_path,
                      self.db.image_root, self.filters.summary(), len(keys),
                      f"ENS Image Database {__version__}"]})
        try:
            with pd.ExcelWriter(path, engine="openpyxl") as xw:
                out.to_excel(xw, sheet_name="Images", index=False)
                meta.to_excel(xw, sheet_name="Query", index=False)
        except PermissionError:
            QMessageBox.warning(self, "Export", "The file is open in another program.")
            return
        self.status.showMessage(f"{len(keys)} images exported to {path}", 8000)

    def export_images(self) -> None:
        """Copy the image files of the selected (or all filtered) images into a folder."""
        if self.db is None:
            return
        keys = self._export_keys()
        folder = QFileDialog.getExistingDirectory(self, "Copy images into folder")
        if not folder:
            return
        prog = QProgressDialog("Copying images…", "Cancel", 0, len(keys), self)
        prog.setWindowModality(Qt.WindowModal)
        n = 0
        for i, k in enumerate(keys):
            if prog.wasCanceled():
                break
            p = self.db.value(k, "info:file")
            if p and imagefiles.exists(p):
                if not os.path.exists(os.path.join(folder, imagefiles.display_name(p))):
                    imagefiles.copy_to(p, folder)
                    n += 1
            prog.setValue(i + 1)
        prog.close()
        self.status.showMessage(f"{n} image file(s) copied to {folder}", 8000)

    # ============================================================ spectral
    def open_spectral(self, path: str) -> None:
        """Launch the Spectral Analyzer (separate program) on a stack."""
        cmd = None
        configured = self.settings.value("spectral_exe", "")
        if configured and (os.path.isfile(configured) or _is_app_bundle(configured)):
            cmd = _launch_command(configured, path)
        elif getattr(sys, "frozen", False):
            here = os.path.dirname(sys.executable)
            # Windows / Linux: the two program folders side by side.  macOS: the two .app
            # bundles side by side (sys.executable is <app>/Contents/MacOS/<name>).
            for cand in (os.path.join(here, "Spectral_Analyzer.exe"),
                         os.path.join(here, "..", "Spectral_Analyzer", "Spectral_Analyzer.exe"),
                         os.path.join(here, "..", "Spectral_Analyzer", "Spectral_Analyzer"),
                         os.path.join(here, "..", "..", "..", "Spectral_Analyzer.app")):
                if (os.path.isfile(cand) and os.access(cand, os.X_OK)) or _is_app_bundle(cand):
                    cmd = _launch_command(os.path.abspath(cand), path)
                    break
        else:
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if os.path.isdir(os.path.join(root, "spectral_analyzer")):
                cmd = [sys.executable, "-m", "spectral_analyzer", path]
                subprocess.Popen(cmd, cwd=root)
                return
        if cmd is None:
            flt = "Program (*.exe);;All files (*)" if sys.platform == "win32" else "All files (*)"
            exe, _ = QFileDialog.getOpenFileName(self, "Locate the Spectral Analyzer program", "",
                                                 flt)
            if not exe:
                return
            self.settings.setValue("spectral_exe", exe)
            cmd = _launch_command(exe, path)
        subprocess.Popen(cmd)

    # ============================================================== misc
    def _open_backups(self) -> None:
        """Open the folder of the automatic workbook backups."""
        if self.db is None:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        folder = os.path.join(os.path.dirname(os.path.abspath(self.db.excel_path)),
                              "_backups_ENS_database")
        if not os.path.isdir(folder):
            QMessageBox.information(self, "Backups", "No backup yet (no edit has been saved).")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _about(self) -> None:
        """Show the About dialog (version, authors, acknowledgements)."""
        AboutDialog(
            f"<h3>ENS Image Database {__version__}</h3>"
            "<p>Identity card and multi-criteria search for autofluorescence images of the enteric "
            "nervous system (colon samples).</p>"
            + credits_html() +
            "<p><b>Data model.</b> The morphology sheet (one row per image) is joined with each "
            "pathologist sheet on the image name. The pre-aligned sheet is not used. Totals are "
            "recomputed from the counts.</p>"
            "<p><b>Live link.</b> The Excel file is watched; any change saved in Excel is reloaded "
            "automatically. Edits made here are written into the file cell by cell (backup first); "
            "embedded pictures and formatting are preserved.</p>", self).exec()

    def closeEvent(self, event) -> None:
        """Stop the thumbnail threads and save the settings before closing."""
        self.thumbs.shutdown()
        self.settings.sync()
        super().closeEvent(event)
