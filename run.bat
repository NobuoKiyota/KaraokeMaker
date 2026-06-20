@echo off
echo Starting KaraokeMaker...
python main_gui.py
if errorlevel 1 (
    echo.
    echo Error: Failed to start the application.
    echo Please ensure that required libraries are installed:
    echo PySide6, Pillow, openpyxl, pandas, sounddevice, soundfile, imageio-ffmpeg
    echo.
    pause
)
