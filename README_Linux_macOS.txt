ENS TOOLS - ENS Image Database + Spectral Analyzer (Linux and macOS)
====================================================================

Authors: (c) 2026 Anis Kebbiche (github.com/anis-kebbiche), Petia Georgieva (github.com/petia-georgieva), Manal Achami (github.com/manal-achami)
Released under the MIT License.

Nothing to install (no Python, no other software).

macOS (Apple Silicon or Intel: the version must have been built on the same type of Mac)
1) Double-click ENS_Tools_macOS.zip to unzip it.
2) Drag ENS_Image_Database.app and Spectral_Analyzer.app into Applications
   (or keep them side by side in the same folder).
3) First start: right-click the app > Open > Open
   (the program is not signed by Apple; this is expected).
   If macOS says that the app is "damaged": open the Terminal and type
      xattr -cr /Applications/ENS_Image_Database.app /Applications/Spectral_Analyzer.app

Linux (64-bit; Ubuntu 24.04, Debian 13, Fedora 39 or newer)
   Older system: run build_linux.sh on that PC (compatible version).
1) tar -xzf ENS_Tools_Linux.tar.gz
2) ./ENS_Image_Database/ENS_Image_Database
   ./Spectral_Analyzer/Spectral_Analyzer
3) If the window does not open ("could not load the Qt platform plugin xcb"):
      sudo apt install libxcb-cursor0 libxkbcommon-x11-0
4) Keep the two folders ENS_Image_Database and Spectral_Analyzer side by side:
   the "Open in Spectral Analyzer" button of the image database needs it.

On the first start of the image database, select:
  - the most recent Excel workbook (Results Patho_DH definitif 06-10-2025 CLEMENT.xlsx)
  - the image folder (the per-slide .zip archives work directly)
  - (optional) the folder of spectral stacks
The data are not in this package: copy them separately.
