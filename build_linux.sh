#!/usr/bin/env bash
# Builds the two Linux programs and packs them into ENS_Tools_Linux.tar.gz.
# Requirements: Python 3.10 to 3.15 with venv (Ubuntu/Debian: sudo apt install python3 python3-venv).
# Run:  bash build_linux.sh     (or make it executable once: chmod +x build_linux.sh)
set -u
cd "$(dirname "$0")"
bash ./build_unix_common.sh || exit 1
cp README_Linux_macOS.txt dist/
echo "Packing ENS_Tools_Linux.tar.gz (about one minute)..."
rm -f ENS_Tools_Linux.tar.gz
tar -czf ENS_Tools_Linux.tar.gz -C dist ENS_Image_Database Spectral_Analyzer README_Linux_macOS.txt || { echo "Packing failed"; exit 1; }
echo
echo "Done: ENS_Tools_Linux.tar.gz  - send this single file."
echo "Programs: dist/ENS_Image_Database/ENS_Image_Database and dist/Spectral_Analyzer/Spectral_Analyzer"
