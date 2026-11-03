@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo === Songbook ===

py -3 -c "import docx, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo Installing required libraries: python-docx openpyxl
    py -3 -m pip install python-docx openpyxl
)

rem First run only: pair Excel files with lyrics docs by title.
rem To re-pair after adding songs, delete songbook\songs_manifest.json or run: py -3 songbook\make_manifest.py
if not exist "songbook\songs_manifest.json" py -3 songbook\make_manifest.py

echo Building song data...
py -3 songbook\build_songs.py
if errorlevel 1 (
    echo.
    echo Error: failed to build song data.
    pause
    exit /b 1
)
echo See songbook\align_report.txt for the lyrics matching report.

echo.
echo Viewer: http://127.0.0.1:8765/
echo Editor: http://127.0.0.1:8765/editor.html  (this PC only)
echo Close this window to stop the server.
start "" /b cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8765/editor.html"
py -3 songbook\server.py 8765
