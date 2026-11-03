@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo === Songbook: build and upload to Cloudflare ===
py -3 songbook\deploy.py
if errorlevel 1 (
    echo.
    echo Error: upload failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo Done. The public page is updated.
pause
