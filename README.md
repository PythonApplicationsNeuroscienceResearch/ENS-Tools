# ENS Tools: Image Database and Spectral Analyzer

Two standalone programs (Windows, Linux, macOS) developed for the M2 course *Python Applications for Neuroscience Research*, Université Paris Cité, on projects proposed by SPPIN (Dr Doriane Hazart, Dr Clément Ricard).

**Authors:** © 2026 Anis Kebbiche ([@anis-kebbiche](https://github.com/anis-kebbiche)), Petia Georgieva ([@petia-georgieva](https://github.com/petia-georgieva)), Manal Achami ([@manal-achami](https://github.com/manal-achami)).


| Program | Project | Purpose |
|---|---|---|
| **ENS_Image_Database** | #5 EarlyDiagnosisParkinson | Identity card of each autofluorescence image of the enteric nervous system (ENS), multi-criteria search and extraction, live two-way link with the Excel database. |
| **Spectral_Analyzer** | #6 SpectralAnalysisGUI | Display of 32-channel spectral (lambda) stacks, free-hand ROI drawing, automatic normalised emission spectra, comparison between images, export to Excel and publication figures. |

The two programs are separate on purpose. They use different data, and the Spectral Analyzer is also meant for other tissues (e.g. testis). They are still linked: when an image of the database has a matching spectral stack, its identity card opens it in the Spectral Analyzer with one click.

---

## 1. Installation

### Option A: build the Windows executables (recommended)
(Step-by-step guide for beginners: `BUILD_INSTRUCTIONS.txt`. End-user notes shipped with the programs: `README_Windows.txt`, `README_Linux_macOS.txt`.)
1. Install Python 3.10 to 3.15, 64-bit, from python.org (tick *Add python.exe to PATH*).
2. Double-click `build_windows.bat`. It creates a virtual environment, installs the dependencies, runs the automated tests and builds:
   - `dist\ENS_Image_Database\ENS_Image_Database.exe`
   - `dist\Spectral_Analyzer\Spectral_Analyzer.exe`
3. To share the programs, copy the whole `dist` folder. Each `.exe` must stay next to its `_internal` folder, and both program folders must stay side by side so that the database can launch the analyzer.

### Option A bis: Linux and macOS
Each system builds its own programs (PyInstaller cannot cross-compile; a Mac build only runs on the same processor type, Apple Silicon or Intel).
- **Linux**: Python 3.10 to 3.15 with `venv` (Ubuntu/Debian: `sudo apt install python3 python3-venv`), then `bash build_linux.sh` → `ENS_Tools_Linux.tar.gz` (programs in `dist/ENS_Image_Database/` and `dist/Spectral_Analyzer/`). A build runs on Linux systems at least as recent as the one that built it (built on Ubuntu 24.04: glibc ≥ 2.38, i.e. Ubuntu 24.04, Debian 13, Fedora 39 or newer). If Qt reports *could not load the Qt platform plugin "xcb"*: `sudo apt install libxcb-cursor0 libxkbcommon-x11-0`.
- **macOS**: Python 3.10 to 3.15 from python.org (the system Python is too old), then double-click `build_macos.command` (first time: right-click › Open) → `ENS_Tools_macOS.zip` with `ENS_Image_Database.app` and `Spectral_Analyzer.app`. The apps are not signed by Apple: first launch with right-click › Open; if macOS reports the app as damaged, run `xattr -cr` on the two apps.
- The same `.spec` files serve all three systems; `build_unix_common.sh` holds the steps shared by Linux and macOS (fresh virtual environment, installation, tests, PyInstaller).

### Option B: run from source
```
python -m pip install -r requirements.txt
python run_ens_database.py
python run_spectral_analyzer.py  [stack.tif ...]
```
(or double-click `run_ens_database.bat` / `run_spectral_analyzer.bat` on Windows, `run_ens_database.sh` / `run_spectral_analyzer.sh` on Linux and macOS, after a first build has created `.venv`).

---

## 2. ENS Image Database (project #5)

### First start
*File › Data locations*: choose the Excel workbook, the image folder (sub-folders are searched, so the per-slide folders can be used as delivered) and, optionally, the folder of spectral stacks. These paths are remembered.

### Views
- **Gallery**: thumbnails of all images matching the current search, coloured by clinical group, majority pathologist diagnosis or plexus type. Thumbnails are generated in the background and cached, so the second start is immediate.
- **Table**: sortable table; the columns are chosen with *Table columns*.
- **Identity card** (double-click an image): zoomable image; DH semi-quantitative morphometry laid out as a size × granules × nucleoli grid with granule grades; tissue information; the four blinded pathologist assessments with consensus; linked spectral stacks; and *every* stored field. Use ← / → to browse the current selection and Esc to return.

### Search and extraction
- Quick filters: free text (image name, comments, morphological descriptions), clinical group, majority diagnosis, plexus (DH), minimum number of pathologists calling Parkinson, slides.
- **Advanced conditions**: any field combined with AND / OR, with operators adapted to the type (numbers: ≥ ≤ = …; ordinal grades none < + < ++ < +++; categories; text). Example: *Pathologists calling Parkinson ≥ 2* AND *Large neurons with nucleoli ≥ 1* AND *Granule load, large neurons ≥ ++*. Queries can be saved and reloaded (JSON). Example queries are provided.
- *File › Export current selection as Excel table* writes every field of the selected images plus a sheet that records the query. *File › Copy image files…* copies the matching images into a folder.

### Live link with the Excel file
- The workbook is **watched**: when it is saved from Excel, the application reloads it automatically and keeps the current view.
- **Edit values…** on an identity card writes the corrections into the workbook. The edit only patches the XML of the modified cells, so the embedded thumbnails, conditional formatting and formulas are preserved (saving with standard Python libraries such as openpyxl would delete them). A timestamped backup is written first to `_backups_ENS_database` (the last 10 are kept). Formula cells are never overwritten. If the file is open in Excel, the application asks you to close it. If the file changed on disk since it was loaded, the edit is refused and the file is reloaded.

### Data model (important)
- The morphology sheet (`All results DH (2)`, one row per image) is joined with each pathologist sheet (`All results FC/MD/PD/PN`) **by image name**. Image names are normalised (case, spaces and underscores, `images/` prefix) so that `images/Lame_BK1245_EA5_01_99.jpg` matches the file `Lame BK1245 EA5 01_99.jpg`.
- The pre-aligned sheet `Fichier aligné DHFCMDPDPN` is **not used**: the joined table is rebuilt from the individual sheets, which the lab confirmed are the reference.
- Totals (small, large and all neurons; with or without granules; with nucleoli; maximal granule grade) are recomputed from the counts. On the current file they match the Excel formulas for all 787 images.
- Consensus: number of pathologists who rated the image, number calling Parkinson / Healthy / Other pathology, and majority diagnosis (*Tie* when two diagnoses have the same count).
- **Clinical group**: assigned **per slide** with the rule given by the lab: slides `BK11..` = Alzheimer group, `BK12..` = Parkinson group, every other slide = control. On the current workbook: 3 Alzheimer slides (BK1173, BK1176, BK1192), 3 Parkinson slides (BK1210, BK1238, BK1245) and 10 control slides. A slide can be reassigned in *Data › Clinical group of each slide*; only such overrides are saved, next to the workbook, as `<workbook>_clinical_groups.csv`.
- **Detailed diagnosis and patient**: when the workbook contains the columns *True Diagnosis* and *Patient* (summary sheet of the version `06-10-2025 CLEMENT`), they are read **per slide** (one slide = one patient) and shown as *Diagnosis (detailed)* (Parkinson, Alzheimer, Control (aged) for 'Healthy', Control (young) for 'Young') and *Patient*. Reading per slide makes the result immune to the row shifts of that sheet; a slide with conflicting values would be reported, as would a disagreement with the clinical group. On the current workbook the two sources agree for all 16 patients (3 Parkinson, 3 Alzheimer, 6 aged controls, 4 young controls).
- **Pathologists matching the clinical group (n/4)**: number of pathologists whose call matches the patient's group, with the lab's convention (column 'Nombre de réponses exactes sur 4', i.e. number of correct answers out of 4): a *Parkinson* call is correct for Parkinson and Alzheimer patients, a *Healthy* call for controls.
- Images that a pathologist's interface did not display carry no rating from that pathologist ("No analyse"); they are counted as not rated, never as a diagnosis.
- *Data › Data integrity report* lists every inconsistency (missing files, images not rated by a pathologist, duplicates, ignored sheets).

---

## 3. Spectral Analyzer (project #6)

### Input
TIFF spectral stacks (ImageJ hyperstacks, OME-TIFF, Zeiss LSM); channels = emission wavelength bands. If the file also contains a Z axis, the program asks for a plane or a projection. Several stacks can be open at once.

### Display
- *Colour-coded projection*: the ImageJ *Temporal-Color Code* applied to the spectral axis with a physical (wavelength) colour table, as in the lab's ImageJ workflow. Other colour tables are available.
- *True colour* (wavelength-weighted sum), sum / maximum projections, single channel.
- *Equalise channels* stretches each channel separately so that weak long-wavelength emitters (red-emitting granules) become visible. This option affects the display only, never the spectra.
- Scale bar from the pixel size stored in the TIFF (0.2076 µm/pixel for all 15 current stacks).

### ROIs and spectra
- Tools: **free-hand** (outline granules, cell bodies or whole ganglia), polygon, rectangle, ellipse, point (single pixel or disk). ImageJ ROI files (`.roi`, `RoiSet.zip`) can be imported.
- The spectrum of an ROI is the mean intensity of its pixels in each channel (pixel centres inside the outline). It is plotted immediately against wavelength (nm), normalised to **maximum = 1** by default (alternatives: none, sum = 1, min–max).
- *Live cursor spectrum* plots the spectrum under the mouse (1×1 to 9×9 pixels).
- *Compare two images* shows two stacks side by side (e.g. pathological vs control). Spectra from all images are overlaid in the same plot. The left panel folds away in this mode to give the images more room; the **☰** button at the top left (or **Ctrl+B**) shows or hides it at any time.
- Optional background subtraction (menu *Background*), applied before normalisation; the spectrum is a mean per pixel, so the size of the background region does not matter:
  - *Background ROI(s) (drawn)*: the pixel-weighted mean spectrum of the ROIs given the role *Background* is subtracted from the other ROIs of the same image.
  - *Auto: dimmest 1 % of the image*: mean spectrum of the 1 % dimmest pixels (summed intensity), a low-percentile estimate of the detector offset and stray light. The ENS stacks have no empty area: the dark regions are faint tissue with the tissue's spectrum (correlation 0.999), at 15 to 40 % of the tissue level, and even the dimmest 1 % reach 160 to 390 at the peak; the minimum pixel value is 0 (no detector offset).
  - *Auto: ring around each ROI*: for each ROI, the mean spectrum of a ring from 2 px to 2 + *w* px outside its edge (default *w* = 10 px, i.e. 2 µm at 0.2 µm/px), other ROIs excluded. This is the local-background annulus of fluorescence photometry: it measures what a structure (e.g. a granule) emits above its immediate surroundings, with no operator choice of the background region.
  - When the background is brighter than an ROI in every channel, the subtracted spectrum is entirely negative; normalisation then keeps the sign (scaling by the largest absolute value) and the status bar names the ROIs concerned.
- Descriptors per ROI: peak wavelength (brightest channel and 3-point parabolic interpolation), FWHM (linear interpolation), spectral centroid, number of pixels, area (µm²).
- *Image mean spectrum (white)*: mean spectrum of all tissue pixels of the displayed image(s) (background excluded by Otsu's threshold on the summed intensity), drawn in white (dashed for image B) with the same normalisation and background subtraction as the ROIs, and exported with them. All stacks were acquired with identical settings (confirmed by the lab), so with normalisation *None* intensities can also be compared between images.
- **Range** (*from … to … nm*, button *Full*): restricts the analysis to the channels whose centre lies in the range (e.g. 480 to 693 nm starts at the 486 nm channel, since channels are centred at 414 + 9 nm × (k − 1)). Normalisation, background subtraction and descriptors are computed within the range only, so that, for instance, *maximum = 1* scales the spectra by their value inside the range and the shape of the descending part can be compared without the dominant 468 nm peak. When the spectrum decreases throughout the range, the reported peak is the first channel of the range (a range edge, not an emission maximum). The processed spectra are exported empty outside the range; the raw spectra are always exported in full, and the range is written in the *Settings* sheet.
- **Readout under the plot**: moving the mouse over the spectrum plot shows the wavelength under the cursor, the nearest channel and the value of every curve at that channel (dotted vertical line).
- **Ctrl+Z** (*Edit › Undo*) undoes the last ROI drawing or deletion, including *Delete all* (last 50 actions).

### Export
- **Excel (.xlsx)**: processed spectra (as plotted), raw mean and SD, ROI table with descriptors, and a sheet with all settings (normalisation, background, wavelength calibration). The project description mentions `.xls`; `.xlsx` is used because the legacy `.xls` format is deprecated, while Excel and every analysis package read `.xlsx`.
- **Figure**: TIFF (LZW, 300 / 600 / 1200 dpi), PNG, JPEG, PDF or SVG, with width in mm (85 mm = single column).
- Image with ROI overlays (PNG/TIFF); ROI sets (JSON) for reproducibility.

### Wavelength calibration
The stacks exported from ImageJ contain **no wavelength information**. The default calibration is the acquisition setting confirmed by the lab: excitation at 405 nm, emission collected from 414 nm with a 9 nm increment between channels, i.e. channel *k* = 414 + 9 × (k − 1) nm (32 channels: 414 to 693 nm). Stacks acquired with other settings (e.g. other tissues) can be given another calibration in *Settings › Wavelength calibration*; the calibration used is written into every export. In all 15 stacks the first channel holds only 1 to 5 % of the peak intensity, consistent with collection starting just above the 405 nm excitation line, and the mean spectrum peaks in channel 7.

### Bonus: spectral difference maps (whole image, no ROI)
*Analysis › Spectral difference maps* (or the *Bonus* box) compares the spectra of two whole images, A and a reference B. ROI drawing remains the main analysis; this view is exploratory.
- The images are not aligned, so A is compared with the **distribution** of the spectra of B, not pixel to pixel.
- The stacks are binned (default 4 × 4 px) to reduce noise; background is excluded by Otsu's threshold on the log summed intensity (or a manual percentile).
- Each tissue pixel is reduced to one number describing the **shape** of its spectrum: the **spectral centroid** (mean emission wavelength, nm; maps centred on the median of B, red = red-shifted) or the **spectral angle** to the mean spectrum of B (°).
- **Atypical pixels**: outside the range of 95 % of the pixels of B *of similar brightness* (limits interpolated between up to 20 intensity strata of B). Brightness matters because dim pixels have noisier spectra: on the lab stacks the angle of B's own pixels falls from about 7° (dimmest decile) to about 5° (brightest), whereas the centroid is stable. Without this matching a dimmer image would look 'different' merely because it is noisier. Pixels of A dimmer than the dimmest stratum of B have no matched reference and are left unclassified (grey), never extrapolated.
- Below the maps: distributions of A and B (with their overlap coefficient) and the mean spectra of A, B and the atypical pixels of A, which shows in which wavelengths the difference lies. Clicking a map plots the spectrum of that pixel.
- Validation on the lab stacks (4 × 4 binning): an image against itself gives 5.0 % atypical pixels (the baseline); a copy of an image dimmed to 60 % and re-noised 1.0 to 3.4 %; two images of the same Parkinson patient 3.8 to 7.7 %; a Parkinson image against an Alzheimer image 48.7 to 66.5 %, with a median centroid shift of +5.3 to +5.5 nm at all binnings (2 to 8 px).
- **Limits**: pixels are spatially correlated and belong to one sample, so no p-value is given; a group effect requires several images per patient and several patients per group. The 15 current stacks come from one Alzheimer and one Parkinson patient and include no control.
- Export: figure (TIFF / PNG / JPEG / PDF / SVG, 300 dpi) and data (.xlsx summary, mean spectra and distribution percentiles; maps and atypical masks as TIFF).

---

## 4. Data

The data (autofluorescence images, spectral stacks and the annotation workbook) belong to SPPIN and are **not** included in this repository. To use the programs, point *File › Data locations* (ENS Image Database) or *Open stack(s)* (Spectral Analyzer) to your own copy. *Data › Data integrity report* lists any inconsistency found in a workbook (missing files, unrated images, duplicates, ignored sheets).

---

## 5. Code organisation
```
ens_database/            Image database (project #5)
  naming.py              image-name normalisation, slide identification
  excel_io.py            sheet detection and reading (header-based, robust to new rows/columns)
  database.py            joined table, derived morphometry, consensus, integrity checks, edits
  xlsx_patch.py          loss-free cell writing into .xlsx (backup, lock detection, atomic swap)
  query.py               multi-criteria conditions
  gui/                   main window, gallery/table, identity card, filters, dialogs, thumbnails
spectral_analyzer/       Spectral Analyzer (project #6)
  spectral_io.py         TIFF/OME/LSM loading, wavelength metadata, calibration, pixel size
  spectral_core.py       colour coding, ROI masks, spectra, normalisation, descriptors
  plotting.py            spectrum figure (screen and publication)
  export.py              Excel / CSV / figure export
  roi_io.py              ROI sets (JSON), ImageJ ROI import
  spectral_maps.py       bonus: whole-image spectral comparison (binning, tissue mask, metrics)
  gui/                   main window, ROI viewer, dialogs, compare_maps.py (bonus window)
tests/test_core.py       automated tests (python -m pytest tests)
```

---

## License

This software was developed by Anis Kebbiche, Petia Georgieva and Manal Achami during the *Python Applications for Neuroscience Research* course, Université Paris Cité, 2026. © 2026 The contributors. Released under the MIT License (see `LICENSE`).
