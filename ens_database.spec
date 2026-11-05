# PyInstaller recipe for the ENS Image Database  (build:  pyinstaller ens_database.spec)
# Works on Windows (folder with ENS_Image_Database.exe), Linux (folder with ENS_Image_Database) and
# macOS (ENS_Image_Database.app bundle).  Each system builds its own version.
import sys

sys.path.insert(0, ".")
from ens_database import COPYRIGHT, __version__  # noqa: E402  (authors and version, single source)

block_cipher = None

# Windows wants an .ico; on macOS PyInstaller converts the .png into an .icns (needs Pillow).
ICON = "assets/ens_database.ico" if sys.platform == "win32" else "assets/ens_database.png"

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
                StringStruct("FileDescription", "ENS Image Database"),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", "ENS_Image_Database"),
                StringStruct("LegalCopyright", COPYRIGHT),
                StringStruct("OriginalFilename", "ENS_Image_Database.exe"),
                StringStruct("ProductName", "ENS Image Database"),
                StringStruct("ProductVersion", __version__),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

# 1) Analysis: start from the launcher script and collect every module it imports.
#    hiddenimports = modules loaded dynamically that PyInstaller cannot detect;
#    excludes = large libraries this program does not need (smaller download).
a = Analysis(
    ["run_ens_database.py"],
    pathex=["."],
    datas=[("assets/ens_database.png", "assets")],
    hiddenimports=["lxml.etree", "lxml._elementpath", "openpyxl.cell._writer"],
    excludes=["tkinter", "PyQt5", "PyQt6", "IPython", "matplotlib", "scipy", "pytest"],
)
# 2) PYZ: archive of the pure-Python modules.
pyz = PYZ(a.pure)
# 3) EXE: the executable itself (console=False: no black terminal window).
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ENS_Image_Database",
    icon=ICON,
    version=VERSION,
    console=False,
)
# 4) COLLECT: one folder with the executable, Python, Qt and the other libraries.
coll = COLLECT(exe, a.binaries, a.datas, name="ENS_Image_Database")

# 5) macOS only: wrap the folder into a double-clickable .app bundle.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ENS_Image_Database.app",
        icon=ICON,
        bundle_identifier="fr.sppin.ens-image-database",
        info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": __version__,
                     "NSHumanReadableCopyright": COPYRIGHT},
    )
