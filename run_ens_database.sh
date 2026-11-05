#!/usr/bin/env bash
# Run the ENS Image Database from source (Linux / macOS), after a first build.
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then exec .venv/bin/python run_ens_database.py "$@"; else exec python3 run_ens_database.py "$@"; fi
