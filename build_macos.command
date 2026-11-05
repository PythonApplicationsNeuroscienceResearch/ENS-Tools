#!/usr/bin/env bash
# Builds the two macOS apps and packs them into ENS_Tools_macOS.zip.
# Requirements: Python 3.10 to 3.15 from python.org (the macOS system Python is too old).
# Run: double-click this file in Finder (first time: right-click > Open), or in Terminal:
#      bash build_macos.command
# The apps are built for the processor of this Mac (Apple Silicon or Intel).
set -u
cd "$(dirname "$0")"
bash ./build_unix_common.sh || { read -r -p "Press Enter to close." _; exit 1; }
cp README_Linux_macOS.txt dist/
echo "Packing ENS_Tools_macOS.zip (about one minute)..."
rm -f ENS_Tools_macOS.zip
# ditto keeps the internal links and permissions of the .app bundles (a plain zip breaks them).
mkdir -p dist/ENS_Tools_macOS
cp -R dist/ENS_Image_Database.app dist/Spectral_Analyzer.app dist/README_Linux_macOS.txt dist/ENS_Tools_macOS/
ditto -c -k --keepParent dist/ENS_Tools_macOS ENS_Tools_macOS.zip || { echo "Packing failed"; exit 1; }
rm -rf dist/ENS_Tools_macOS
echo
echo "Done: ENS_Tools_macOS.zip  - send this single file."
echo "Apps: dist/ENS_Image_Database.app and dist/Spectral_Analyzer.app"
open -R ENS_Tools_macOS.zip 2>/dev/null
read -r -p "Press Enter to close." _
