# PyInstaller recipe for the Spectral Analyzer  (build:  pyinstaller spectral_analyzer.spec)
# Works on Windows (folder with Spectral_Analyzer.exe), Linux (folder with Spectral_Analyzer) and
# macOS (Spectral_Analyzer.app bundle).  Each system builds its own version.
import sys

sys.path.insert(0, ".")
from spectral_analyzer import COPYRIGHT, __version__  # noqa: E402  (authors and version, single source)

block_cipher = None

# Windows wants an .ico; on macOS PyInstaller converts the .png into an .icns (needs Pillow).
ICON = "assets/spectral_analyzer.ico" if sys.platform == "win32" else "assets/spectral_analyzer.png"

# Windows file properties (right-click > Properties > Details): description, version, copyright.
VERSION = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                     StringTable, VarFileInfo, VarStruct,
                                                     VSVersionInfo)
    _v = tuple(int(x) for x in __version__.split(".")) + (0,)
    VERSION = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_v, prodvers=_v, mask=0x3F, flags=0x0, OS=0x40004,
                          fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Anis Kebbiche, Petia Georgieva, Manal Achami"),
                StringStruct("FileDescription", "Spectral Analyzer"),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", "Spectral_Analyzer"),
                StringStruct("LegalCopyright", COPYRIGHT),
                StringStruct("OriginalFilename", "Spectral_Analyzer.exe"),
                StringStruct("ProductName", "Spectral Analyzer"),
                StringStruct("ProductVersion", __version__),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

# 1) Analysis: start from the launcher script and collect every module it imports.
#    hiddenimports = modules loaded dynamically that PyInstaller cannot detect;
#    excludes = large libraries this program does not need (smaller download).
a = Analysis(
    ["run_spectral_analyzer.py"],
    pathex=["."],
    datas=[("assets/spectral_analyzer.png", "assets")],
    hiddenimports=["matplotlib.backends.backend_qtagg", "openpyxl.cell._writer"],
    excludes=["tkinter", "PyQt5", "PyQt6", "IPython", "scipy", "pytest"],
)
# 2) PYZ: archive of the pure-Python modules.
pyz = PYZ(a.pure)
# 3) EXE: the executable itself (console=False: no black terminal window).
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Spectral_Analyzer",
    icon=ICON,
    version=VERSION,
    console=False,
)
# 4) COLLECT: one folder with the executable, Python, Qt and the other libraries.
coll = COLLECT(exe, a.binaries, a.datas, name="Spectral_Analyzer")

# 5) macOS only: wrap the folder into a double-clickable .app bundle.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Spectral_Analyzer.app",
        icon=ICON,
        bundle_identifier="fr.sppin.spectral-analyzer",
        info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": __version__,
                     "NSHumanReadableCopyright": COPYRIGHT},
    )
