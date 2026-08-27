@echo off
rem Rebuild ChamberBot.exe and copy the data files next to it.
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --windowed --name ChamberBot ^
  --collect-all customtkinter --collect-all pystray ^
  --hidden-import cv2 --hidden-import numpy --hidden-import PIL --hidden-import winotify gui.py
if errorlevel 1 (
  echo BUILD FAILED
  pause
  exit /b 1
)
copy /y config.json dist\ >nul
copy /y digit_templates.json dist\ >nul
copy /y app_icon.ico dist\ >nul
if not exist settings.json python -c "import settings; settings.save(settings.load())"
copy /y settings.json dist\ >nul
echo.
echo Done: dist\ChamberBot.exe
pause
