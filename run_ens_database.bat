@echo off
REM Run the ENS Image Database from source (no build needed).
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (.venv\Scripts\pythonw.exe run_ens_database.py) else (pythonw run_ens_database.py)
