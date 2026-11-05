@echo off
REM Packs the built programs into one zip ready to send: ENS_Tools_Windows.zip
cd /d "%~dp0"
if not exist "dist\ENS_Image_Database\ENS_Image_Database.exe" (echo Run build_windows.bat first. & pause & exit /b 1)
if not exist "dist\Spectral_Analyzer\Spectral_Analyzer.exe" (echo Run build_windows.bat first. & pause & exit /b 1)
copy /y README_Windows.txt dist\README_Windows.txt >nul
if exist ENS_Tools_Windows.zip del ENS_Tools_Windows.zip
echo Creating ENS_Tools_Windows.zip (about one minute)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path \"dist\*\" -DestinationPath \"ENS_Tools_Windows.zip\" -CompressionLevel Optimal" || (echo ZIP FAILED & pause & exit /b 1)
echo.
echo Done: ENS_Tools_Windows.zip  - send this single file.
explorer /select,"%~dp0ENS_Tools_Windows.zip"
pause
