@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo === Songbook: build the folder for publishing ===
py -3 -c "import cryptography" >nul 2>&1
if errorlevel 1 (
    echo Installing required library: cryptography
    py -3 -m pip install cryptography
)

echo.
echo Password: if pass.txt exists in this folder, its first line is used as the password.
echo Otherwise type one to encrypt the song data, or just press Enter for no password
echo (with no password, anyone who knows the URL can view the pages).
echo.
py -3 songbook\publish.py
if errorlevel 1 (
    echo.
    echo Error: failed to build songbook\dist
    pause
    exit /b 1
)

echo.
echo Done. Upload the contents of songbook\dist to your hosting.
explorer songbook\dist
pause
