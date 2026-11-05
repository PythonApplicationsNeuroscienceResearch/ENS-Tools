@echo off
REM Run the Spectral Analyzer from source (no build needed).
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (.venv\Scripts\pythonw.exe run_spectral_analyzer.py %*) else (pythonw run_spectral_analyzer.py %*)
