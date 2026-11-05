#!/usr/bin/env bash
# Run the Spectral Analyzer from source (Linux / macOS), after a first build.
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then exec .venv/bin/python run_spectral_analyzer.py "$@"; else exec python3 run_spectral_analyzer.py "$@"; fi
