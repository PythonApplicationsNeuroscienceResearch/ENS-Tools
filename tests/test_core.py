"""Automated tests of the non-graphical code (run with: python -m pytest tests)."""
import os
import sys
import zipfile

import numpy as np
import openpyxl
import pytest
import tifffile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ens_database import xlsx_patch                                      # noqa: E402
from ens_database.database import Database                               # noqa: E402
from ens_database.naming import image_key, slide_id, spectral_key        # noqa: E402
from ens_database.query import Condition, apply_query                    # noqa: E402
from spectral_analyzer import spectral_core as sc                        # noqa: E402
from spectral_analyzer.spectral_io import Calibration, load_stack        # noqa: E402


# --------------------------------------------------------------------------- naming
@pytest.mark.parametrize("name,key", [
    ("images/Lame_BK1245_EA5_01_99.jpg", "lame_bk1245_ea5_01_99"),
    ("Lame BK1245 EA5 01_99.jpg", "lame_bk1245_ea5_01_99"),
    (r"C:\data\lame 429C1.JPG", "lame_429c1"),
    ("images/BK1210_EA5_02_96.jpg", "bk1210_ea5_02_96"),
])
def test_image_key(name, key):
    """Image names written in different ways give the same key."""
    assert image_key(name) == key


@pytest.mark.parametrize("key,slide", [
    ("lame_bk1245_ea5_01_99", "BK1245 EA5"), ("bk1210_ea5_02_96", "BK1210 EA5"),
    ("lame_bk_1192_ea5_02_32", "BK1192 EA5"), ("lame_bk1173_ea521", "BK1173 EA5"),
    ("lame_424c10", "424C"), ("lame_4346", "434"), ("lame_456_1", "456"), ("lame_472g1", "472G"),
])
def test_slide_id(key, slide):
    """The slide is correctly extracted from an image key."""
    assert slide_id(key) == slide


def test_spectral_key():
    """A spectral stack file name is linked to its database image."""
    assert spectral_key("Spectre Lame BK1238 EA5 03_6-slice14.tif") == "lame_bk1238_ea5_03_6"
    assert spectral_key("Spectre Lame BK1173 EA521-slice12.tif") == "lame_bk1173_ea521"


# --------------------------------------------------------------------------- workbook fixture
def make_workbook(folder):
    """Small workbook mimicking the lab file (morphology + 2 pathologists + aligned sheet)."""
    from openpyxl.drawing.image import Image as XLImage
    from PIL import Image

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "All results DH (2)"
    head = ["Images", "Login", "Nom de l'image", "RÃ©ponse Image", "empty_plexus"]
    for size in ("small", "large"):
        for gran in ("without", "with"):
            for nucl in ("with", "without"):
                head.append(f"{size}_nerve_cell_{gran}_granules_{nucl}_nucleoli_nb")
                if gran == "with":
                    head.append(f"{size}_nerve_cell_with_granules_{nucl}_nucleoli_granules")
    head += ["infiltrating_cell_nb", "infiltrating_cell_in_env_tissue", "comments", "nice_picture",
             "total_nb_large_nerve_cell"]
    ws.append(head)
    rows = [
        ("images/Lame_BK1_EA5_01_1.jpg", "Auerbach", 0, [0, 2, 0, "none", 5, "+", 1, 0, 2, "++", 3, "+++"], "No"),
        ("images/Lame_BK1_EA5_01_2.jpg", "Meissner", 1, [0, 0, 0, "none", 0, "none", 0, 0, 0, "none", 0, "none"], "Sure"),
        ("images/lame_424C3.jpg", "NoENS", 0, [1, 0, 0, "none", 0, "none", 0, 1, 0, "none", 0, "none"], "Maybe"),
    ]
    for i, (name, plexus, empty, counts, nice) in enumerate(rows, start=2):
        ws.append([None, "doriane", name, plexus, empty] + counts + ["none", "none", None, nice,
                                                                      None])
        col = head.index("total_nb_large_nerve_cell") + 1
        ws.cell(i, col).value = f"=SUM({openpyxl.utils.get_column_letter(head.index('large_nerve_cell_without_granules_with_nucleoli_nb') + 1)}{i})"
    img_path = os.path.join(folder, "thumb.png")
    Image.new("RGB", (20, 20), "red").save(img_path)
    ws.add_image(XLImage(img_path), "A2")
    for code, login, diags in (("PN", "peter", ["Healthy", "Parkinson", "Healthy"]),
                               ("FC", "francois", ["Parkinson", "Parkinson", None])):
        r = wb.create_sheet(f"All results {code}")
        r.append(["Login", "Nom de l'image", "RÃ©ponse Image", "Diagnostic", "Certitude",
                  "CaractÃ©ristiques morphologiques", "Commentaires"])
        for (name, *_), d in zip(rows, diags):
            if d is None:
                continue
            r.append([login, name, "Auerbach", d, "Maybe", "a", "-"])
    al = wb.create_sheet("Fichier aligné")
    al.append(["Images", "Login", "Nom de l'image", "Login", "Nom de l'image"])
    path = os.path.join(folder, "db.xlsx")
    wb.save(path)
    imgs = os.path.join(folder, "images", "sub")
    os.makedirs(imgs)
    for name in ("Lame BK1 EA5 01_1.jpg", "Lame BK1 EA5 01_2.jpg", "lame 424C3.jpg", "extra 9.jpg"):
        Image.new("RGB", (64, 64), "gray").save(os.path.join(imgs, name))
    return path, os.path.join(folder, "images")


def test_database_join_and_derived(tmp_path):
    """The sheets are joined per image and the derived totals and consensus are correct."""
    xlsx, imgs = make_workbook(str(tmp_path))
    db = Database(xlsx, imgs, "", groups_path=str(tmp_path / "g.csv"))
    db.load()
    assert len(db.df) == 3
    assert [r.code for r in db.raters] == ["PN", "FC"]
    k = "lame_bk1_ea5_01_1"
    assert db.value(k, "derived:total_small") == 7
    assert db.value(k, "derived:total_large") == 6
    assert db.value(k, "derived:large_with_granules") == 5
    assert db.value(k, "derived:large_with_nucleoli") == 3
    assert db.value(k, "derived:max_granule_grade_large") == "+++"
    assert db.value(k, "consensus:n_parkinson") == 1
    assert db.value(k, "consensus:majority") == "Tie"
    assert db.value("lame_bk1_ea5_01_2", "consensus:majority") == "Parkinson"
    assert db.value("lame_424c3", "consensus:n_raters") == 1
    assert db.value("lame_424c3", "info:slide") == "424C"
    cats = {c for c, _ in db.issues}
    assert "Image without Excel row (excluded)" in cats and "Not rated by FC" in cats and "Sheet ignored" in cats
    assert "DH:total_nb_large_nerve_cell" not in db.df.columns          # formula column dropped
    assert db.value(k, "derived:large_with_granules_and_nucleoli") == 2


def test_clinical_group_rule(tmp_path):
    """Slide-code rule of the lab: BK11 = Alzheimer, BK12 = Parkinson, others = control."""
    from ens_database.database import rule_group
    assert rule_group("BK1173 EA5") == "Alzheimer"
    assert rule_group("BK1192 EA5") == "Alzheimer"
    assert rule_group("BK1210 EA5") == "Parkinson"
    assert rule_group("BK1245 EA5") == "Parkinson"
    assert rule_group("424C") == "Control" and rule_group("470") == "Control"
    assert rule_group("Unknown") == "Unassigned"
    xlsx, imgs = make_workbook(str(tmp_path))
    g = tmp_path / "g.csv"
    g.write_text("slide,clinical_group\n424C,Unassigned\n", encoding="utf-8")   # old-version file
    db = Database(xlsx, imgs, "", groups_path=str(g))
    db.load()
    assert db.value("lame_424c3", "info:clinical_group") == "Control"     # rule wins over 'Unassigned'
    db.set_slide_groups({"424C": "Other"})                                # manual override
    assert db.value("lame_424c3", "info:clinical_group") == "Other"
    db2 = Database(xlsx, imgs, "", groups_path=str(g))
    db2.load()
    assert db2.value("lame_424c3", "info:clinical_group") == "Other"      # override persisted
    db2.set_slide_groups({"424C": "Control"})                             # back to the rule
    db3 = Database(xlsx, imgs, "", groups_path=str(g))
    db3.load()
    assert db3.slide_groups == {} and db3.value("lame_424c3", "info:clinical_group") == "Control"


def test_no_analyse_is_not_a_rating():
    """'No analyse' is treated as a missing rating, not as a diagnosis."""
    from ens_database.database import _clean
    assert _clean("No analyse") is None and _clean(" no analysis ") is None
    assert _clean("Healthy") == "Healthy"


def test_default_calibration_is_lab_setting():
    """Default wavelengths = lab settings (414 nm + 9 nm per channel)."""
    wl = Calibration().wavelengths(32)
    assert wl[0] == 414 and wl[1] == 423 and wl[-1] == 693 and Calibration().verified


def test_query(tmp_path):
    """Advanced conditions (AND / OR) select the expected images."""
    xlsx, imgs = make_workbook(str(tmp_path))
    db = Database(xlsx, imgs, "", groups_path=str(tmp_path / "g.csv"))
    db.load()
    m = apply_query(db.df, db.fields, [Condition("derived:total_large", "≥", 1),
                                       Condition("derived:max_granule_grade_large", "≥", "++")])
    assert list(db.df.index[m]) == ["lame_bk1_ea5_01_1"]
    m = apply_query(db.df, db.fields, [Condition("DH:plexus", "is", "NoENS"),
                                       Condition("consensus:majority", "is", "Parkinson")], "any")
    assert set(db.df.index[m]) == {"lame_424c3", "lame_bk1_ea5_01_2"}
    m = apply_query(db.df, db.fields, [Condition("FC:diagnosis", "is empty")])
    assert list(db.df.index[m]) == ["lame_424c3"]


def test_write_back_preserves_package(tmp_path):
    """Writing a value back keeps every other part of the .xlsx file intact."""
    xlsx, imgs = make_workbook(str(tmp_path))
    db = Database(xlsx, imgs, "", groups_path=str(tmp_path / "g.csv"))
    db.load()
    with zipfile.ZipFile(xlsx) as z:
        before = {i.filename: z.read(i.filename) for i in z.infolist()}
    from ens_database.database import Edit
    backup = db.write_edits([
        Edit("DH:large_nerve_cell_with_granules_with_nucleoli_nb", "lame_bk1_ea5_01_1", 4),
        Edit("DH:comments", "lame_bk1_ea5_01_1", "re-counted"),
        Edit("PN:diagnosis", "lame_424c3", "Parkinson"),
    ])
    assert backup and os.path.isfile(backup)
    assert db.value("lame_bk1_ea5_01_1", "derived:large_with_granules") == 7   # 4 + 3
    assert db.value("lame_bk1_ea5_01_1", "DH:comments") == "re-counted"
    assert db.value("lame_424c3", "consensus:n_parkinson") == 1
    with zipfile.ZipFile(xlsx) as z:
        after = {i.filename: z.read(i.filename) for i in z.infolist()}
    changed = {k for k in before if before[k] != after.get(k)}
    assert set(after) == set(before)
    assert all(k.startswith("xl/worksheets/") or k == "xl/workbook.xml" for k in changed)
    assert any(k.startswith("xl/media/") for k in after)                 # embedded picture kept
    wb = openpyxl.load_workbook(xlsx)
    assert len(wb["All results DH (2)"]._images) == 1


def test_formula_cells_are_protected(tmp_path):
    """Cells containing a formula are never overwritten."""
    xlsx, _ = make_workbook(str(tmp_path))
    wb = openpyxl.load_workbook(xlsx)
    ws = wb["All results DH (2)"]
    col = [c.value for c in ws[1]].index("total_nb_large_nerve_cell") + 1
    with pytest.raises(xlsx_patch.XlsxWriteError):
        xlsx_patch.write_cells(xlsx, {"All results DH (2)": [(2, col, 5)]}, backup=False)


# --------------------------------------------------------------------------- spectral
def test_masks_and_spectrum():
    """ROI masks have the right pixels and the ROI spectrum equals the direct mean."""
    d = np.random.RandomState(1).randint(0, 4000, (8, 40, 50)).astype(np.uint16)
    g = sc.RoiGeometry("rectangle", {"x0": 2, "y0": 3, "x1": 7, "y1": 5})
    m = sc.roi_mask((40, 50), g)
    assert m.sum() == 10 and m[3:5, 2:7].all()
    sp = sc.roi_spectrum(d, m)
    ref = d[:, 3:5, 2:7].reshape(8, -1).astype(float)
    assert np.allclose(sp.mean, ref.mean(1)) and np.allclose(sp.sd, ref.std(1, ddof=1))
    poly = sc.RoiGeometry("polygon", {"points": [[2, 3], [7, 3], [7, 5], [2, 5]]})
    assert (sc.roi_mask((40, 50), poly) == m).all()
    pt = sc.roi_mask((40, 50), sc.RoiGeometry("point", {"x": 9.9, "y": 0.1, "radius": 0}))
    assert pt.sum() == 1 and pt[0, 9]


def test_descriptors_gaussian():
    """Peak, FWHM and centroid of a Gaussian spectrum are recovered."""
    wl = 414 + 8.9 * np.arange(32)
    y = np.exp(-0.5 * ((wl - 500.0) / 25.0) ** 2)
    d = sc.descriptors(wl, y)
    assert abs(d.peak_interp_nm - 500.0) < 1.0
    assert abs(d.fwhm_nm - 2.3548 * 25) < 2.0
    assert abs(d.centroid_nm - 500.0) < 1.0
    n, s = sc.normalise(y * 7, y, "Maximum = 1")
    assert np.isclose(n.max(), 1) and np.isclose(s.max(), 1 / 7)


def test_load_imagej_and_zstack(tmp_path):
    """ImageJ hyperstacks load with the right axes; a Z axis is reduced as requested."""
    data = np.random.RandomState(0).randint(0, 1000, (32, 16, 20)).astype(np.uint16)
    p = str(tmp_path / "s.tif")
    tifffile.imwrite(p, data, imagej=True, metadata={"axes": "CYX", "unit": "micron"},
                     resolution=(4.8177, 4.8177))
    st = load_stack(p, Calibration(414, 8.9))
    assert st.data.shape == (32, 16, 20) and np.array_equal(st.data, data)
    assert st.wl_source == "calibration" and np.isclose(st.wavelengths[-1], 414 + 8.9 * 31)
    assert abs(st.pixel_size_um - 1 / 4.8177) < 1e-3
    z = np.random.RandomState(0).randint(0, 1000, (5, 32, 16, 20)).astype(np.uint16)
    p2 = str(tmp_path / "z.tif")
    tifffile.imwrite(p2, z, imagej=True, metadata={"axes": "ZCYX"})
    st2 = load_stack(p2, Calibration(), choose_z=lambda n: 3)
    assert np.array_equal(st2.data, z[3])
    st3 = load_stack(p2, Calibration(), choose_z=lambda n: "max")
    assert np.array_equal(st3.data, z.max(axis=0))


def test_load_ome_wavelengths(tmp_path):
    """Wavelengths stored in OME-XML metadata are read."""
    data = np.zeros((4, 8, 8), np.uint16)
    p = str(tmp_path / "o.ome.tif")
    tifffile.imwrite(p, data, metadata={"axes": "CYX", "Channel": {
        "Name": ["a", "b", "c", "d"], "EmissionWavelength": [450.0, 460.0, 470.0, 480.0]}})
    st = load_stack(p, Calibration())
    assert st.wl_source == "file" and list(st.wavelengths) == [450, 460, 470, 480]


def test_imagej_roi_import(tmp_path):
    """An ImageJ .roi file is imported with the right geometry (a 20 × 10 px rectangle)."""
    from roifile import ImagejRoi
    from spectral_analyzer.roi_io import import_imagej
    roi = ImagejRoi.frompoints([[10, 10], [30, 10], [30, 20], [10, 20]])
    p = str(tmp_path / "r.roi")
    roi.tofile(p)
    rois, skipped = import_imagej(p)
    assert len(rois) == 1 and not skipped
    m = sc.roi_mask((50, 50), rois[0][1])
    assert m.sum() == 200


def test_images_inside_zip_archives(tmp_path):
    """Images can be used directly from the per-slide .zip archives delivered by the lab."""
    xlsx, imgs = make_workbook(str(tmp_path))
    from PIL import Image
    from ens_database import imagefiles
    zdir = tmp_path / "zips"
    zdir.mkdir()
    with zipfile.ZipFile(zdir / "Lame BK1 EA5_Images.zip", "w") as z:
        for name in ("Lame BK1 EA5 01_1.jpg", "Lame BK1 EA5 01_2.jpg"):
            p = tmp_path / name
            Image.new("RGB", (32, 32), "white").save(p)
            z.write(p, f"Images jpeg traitées auto contrast/{name}")
    db = Database(xlsx, str(zdir), "", groups_path=str(tmp_path / "g.csv"))
    db.load()
    path = db.value("lame_bk1_ea5_01_1", "info:file")
    assert "::" in path and imagefiles.exists(path)
    assert imagefiles.open_image(path).size == (32, 32)
    assert db.value("lame_bk1_ea5_01_1", "info:image") == "Lame BK1 EA5 01_1.jpg"
    out = tmp_path / "out"
    out.mkdir()
    assert os.path.isfile(imagefiles.copy_to(path, str(out)))
    assert not db.value("lame_424c3", "info:file")            # not in the archive


def test_thumbnail_of_16bit_image():
    """Raw TIFF exports are 16-bit (mode I;16B): thumbnails must still be produced."""
    from PIL import Image
    from ens_database.gui.thumbnails import to_8bit
    a = (np.arange(64 * 64, dtype=np.uint16).reshape(64, 64) * 13).astype(">u2")
    im = Image.fromarray(a.astype(np.uint16)).convert("I;16B")
    out = to_8bit(im)
    out.thumbnail((16, 16), Image.LANCZOS)
    assert out.mode == "RGB" and out.size == (16, 16)


def test_true_diagnosis_and_patient_from_summary_sheet(tmp_path):
    """'True Diagnosis' / 'Patient' read per slide, name column given as '=C<row>' formulas."""
    xlsx, imgs = make_workbook(str(tmp_path))
    wb = openpyxl.load_workbook(xlsx)
    al = wb["Fichier aligné"]
    for j, h in enumerate(["Vrai Diagnostic", "Nombre", "True Diagnosis", "Patient"], start=6):
        al.cell(1, j).value = h
    data = [("images/Lame_BK1_EA5_01_1.jpg", "Parkinson", "P1"),
            ("images/Lame_BK1_EA5_01_2.jpg", "Parkinson", "P2"),     # same slide, other patient
            ("images/lame_424C3.jpg", "Young", "Healthy1P")]
    for i, (name, dx, pt) in enumerate(data, start=2):
        al.cell(i, 3).value = name
        al.cell(i, 6).value = f"=C{i}"
        al.cell(i, 8).value = dx
        al.cell(i, 9).value = pt
    wb.save(xlsx)
    db = Database(xlsx, imgs, "", groups_path=str(tmp_path / "g.csv"))
    db.load()
    assert db.value("lame_424c3", "info:diagnosis") == "Control (young)"
    assert db.value("lame_424c3", "info:patient") == "Healthy1P"
    assert db.value("lame_424c3", "info:clinical_group") == "Control"
    assert db.value("lame_bk1_ea5_01_1", "info:diagnosis") is None       # conflicting slide
    cats = [c for c, _ in db.issues]
    assert "Diagnosis / patient conflict" in cats and "Diagnosis / group mismatch" not in cats
    # 424C3: PN 'Healthy', FC not rated -> 1 call matching the control group
    assert db.value("lame_424c3", "consensus:n_correct") == 1
    assert db.value("lame_bk1_ea5_01_1", "consensus:n_correct") is None   # group unassigned
    db.set_slide_groups({"424C": "Parkinson"})
    assert db.value("lame_424c3", "consensus:n_correct") == 0            # recomputed


# --------------------------------------------------------------------------- spectral maps
def _synthetic_stack(peak_nm, shape=(128, 128), scale=1000.0, seed=0, tissue_frac=0.75):
    """Gaussian emission spectrum + Poisson noise; the top rows are background (zero)."""
    rng = np.random.default_rng(seed)
    wl = 414 + 9 * np.arange(32)
    spec = np.exp(-0.5 * ((wl - peak_nm) / 30.0) ** 2)
    y, x = shape
    inten = np.zeros(shape)
    inten[int(y * (1 - tissue_frac)):, :] = scale * (0.5 + rng.random((y - int(y * (1 - tissue_frac)), x)))
    lam = spec[:, None, None] * inten[None]
    return rng.poisson(lam).astype(np.uint16), wl


def test_spectral_maps_basic():
    """Binning, tissue mask and a self-comparison of the whole-image maps give the expected values."""
    from spectral_analyzer import spectral_maps as sm
    d, wl = _synthetic_stack(500, shape=(64, 64))
    b = sm.bin_stack(d, 4)
    assert b.shape == (32, 16, 16)
    assert np.isclose(b[:, -1, -1].sum(), d[:, -4:, -4:].astype(float).mean(axis=(1, 2)).sum())
    p = sm.prepare("x", d, 2)
    assert p.fg[: 8].sum() == 0 and p.fg[-8:].all()              # background rows excluded
    assert np.allclose(p.shapes.sum(axis=1), 1)
    c = sm.compare(p, p, wl, "angle")                            # self-comparison: ~5 % atypical
    assert abs(c.summary["Atypical pixels in A (%)"] - 5) < 3
    assert c.summary["Overlap of the A and B distributions (0–1)"] == 1.0
    assert np.isnan(c.map_a[0, 0]) and not np.isnan(c.map_a[-1, -1])
    assert np.isclose(np.median(sm.spectral_centroid(p.shapes, wl)), 500, atol=3)


def test_spectral_maps_detects_shift_not_brightness():
    """A spectral shift is detected while a mere change of brightness is not."""
    from spectral_analyzer import spectral_maps as sm
    ref, wl = _synthetic_stack(500, seed=1)
    shifted, _ = _synthetic_stack(512, seed=2)                    # red-shifted emission
    dim, _ = _synthetic_stack(500, seed=3, scale=700.0)           # same spectrum, 30 % dimmer
    dimmer, _ = _synthetic_stack(500, seed=4, scale=300.0)        # dimmer than all of ref
    pr, ps, pd_, pdd = (sm.prepare(n, x, 2) for n, x in (("ref", ref), ("shift", shifted),
                                                          ("dim", dim), ("dimmer", dimmer)))
    for metric in ("centroid", "angle"):
        assert sm.compare(ps, pr, wl, metric).summary["Atypical pixels in A (%)"] > 80
    c = sm.compare(ps, pr, wl, "centroid")
    assert 8 < c.summary["Median difference (A − B)"] < 16
    # A dimmer (noisier) image with the same spectrum must not look 'different'.
    for metric in ("centroid", "angle"):
        s = sm.compare(pd_, pr, wl, metric).summary
        assert 20 < s["A pixels dimmer than all of B, unclassified (%)"] < 80
        assert s["Atypical pixels in A (%)"] < 12
        s = sm.compare(pdd, pr, wl, metric).summary
        assert s["A pixels dimmer than all of B, unclassified (%)"] == 100
        assert np.isnan(s["Atypical pixels in A (%)"])


def test_pixel_shape_lookup():
    """The spectrum of a clicked binned pixel is the right one."""
    from spectral_analyzer import spectral_maps as sm
    d, wl = _synthetic_stack(480)
    p = sm.prepare("x", d, 4)
    r, c = p.shape[0] - 1, 3
    idx = int(np.flatnonzero(p.fg.ravel()).tolist().index(r * p.shape[1] + c))
    s = sm.pixel_shape(p, r, c)
    assert np.allclose(s, p.shapes[idx] / p.shapes[idx].max())
    assert sm.pixel_shape(p, 0, 0) is None


def test_tissue_spectrum_matches_direct_mean():
    """The chunked image mean spectrum equals the direct calculation."""
    d, wl = _synthetic_stack(500, shape=(300, 260))                  # 300 rows: several blocks
    m = sc.tissue_mask(d)
    assert m[:60].sum() == 0 and m[-50:].all()                        # background rows excluded
    sp = sc.tissue_spectrum(d)
    v = d[:, m].astype(float)
    assert sp.n_pixels == int(m.sum())
    assert np.allclose(sp.mean, v.mean(axis=1)) and np.allclose(sp.sd, v.std(axis=1, ddof=1))


def test_credits_in_both_programs():
    """Both programs carry the same authors and copyright."""
    import ens_database
    import spectral_analyzer
    for pkg in (ens_database, spectral_analyzer):
        assert [gh for _, gh in pkg.AUTHORS] == ["anis-kebbiche", "petia-georgieva", "manal-achami"]
        assert pkg.COPYRIGHT.startswith("© 2026 ") and "github.com/manal-achami" in pkg.credits_html()


def test_normalise_never_flips_a_negative_spectrum():
    """Background brighter than the ROI -> spectrum <= 0: Max/Sum scaling must keep the sign."""
    shape = np.exp(-0.5 * ((np.arange(32) - 6) / 4.0) ** 2)
    diff = 600 * shape - 3700 * shape - 14                 # ROI minus a brighter background
    for method in ("Maximum = 1", "Sum = 1"):
        y, _ = sc.normalise(diff, None, method)
        assert np.all(y <= 0)                                # sign kept (old code: flipped, x150)
        assert np.isclose(np.corrcoef(y, diff)[0, 1], 1)     # same shape
    y, _ = sc.normalise(diff, None, "Maximum = 1")
    assert np.isclose(y.min(), -1)
    y, _ = sc.normalise(600 * shape, None, "Maximum = 1")     # ordinary spectrum: unchanged rule
    assert np.isclose(y.max(), 1)


def test_background_ring_and_dimmest_pixels():
    """The background ring and the dimmest-pixel mask select the right pixels."""
    m = np.zeros((60, 60), bool)
    m[28:32, 28:32] = True
    other = np.zeros_like(m)
    other[20:40, 38:40] = True                                   # another ROI crossing the ring
    r = sc.ring_mask(m, gap=2, width=6, exclude=other)
    assert r.any() and not (r & m).any() and not (r & other).any()
    assert not (r & sc._dilate(m, 2)).any()                      # 2 px gap kept
    assert not (r & ~sc._dilate(m, 8)).any()                     # nothing beyond gap + width
    assert r[30, 34:38].all() and not r[30, 38:40].any()          # ring from 2 px out, other ROI cut
    d = np.ones((32, 50, 50), np.uint16) * 100
    d[:, :5, :5] = 7                                             # 25 dimmest pixels = 1 %
    dk = sc.dark_pixels_mask(d, 0.01)
    assert dk.sum() == 25 and np.allclose(sc.roi_spectrum(d, dk).mean, 7)
    # ring background = local surroundings: granule 900 on cytoplasm 300 -> 600 above background
    img = np.full((32, 60, 60), 300.0)
    img[:, 28:32, 28:32] = 900.0
    ring = sc.ring_mask(m, 2, 6)
    assert np.allclose(sc.roi_spectrum(img, m).mean - sc.roi_spectrum(img, ring).mean, 600)


def test_wavelength_range_restricts_normalisation_and_descriptors():
    """A wavelength range restricts normalisation and descriptors to the channels inside it."""
    wl = 414 + 9 * np.arange(32)
    keep = sc.range_mask(wl, 480, 693)
    assert wl[keep][0] == 486 and wl[keep][-1] == 693 and keep.sum() == 24   # 477 nm excluded
    assert (sc.range_mask(wl, 693, 480) == keep).all()                       # bounds in any order
    spec = np.exp(-0.5 * ((wl - 468) / 30.0) ** 2) * 1000 + 10
    y, sd = sc.normalise(sc.restrict(spec, keep), sc.restrict(np.ones(32), keep), "Maximum = 1")
    assert np.isnan(y[~keep]).all() and np.isclose(np.nanmax(y), 1) and np.argmax(np.nan_to_num(y)) == np.flatnonzero(keep)[0]
    d = sc.descriptors(wl, y)
    assert d.peak_nm == 486 and np.isfinite(d.centroid_nm) and d.centroid_nm > 486
    empty = sc.restrict(spec, np.zeros(32, bool))                            # nothing in range
    y2, _ = sc.normalise(empty, None, "Maximum = 1")
    assert np.isnan(y2).all() and np.isnan(sc.descriptors(wl, y2).peak_nm)
