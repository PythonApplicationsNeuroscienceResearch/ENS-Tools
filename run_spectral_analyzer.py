"""Launcher used by PyInstaller and for double-click execution."""
import sys

from spectral_analyzer.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
