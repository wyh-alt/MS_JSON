@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Installing build dependencies...
python -m pip install -r requirements.txt pyinstaller -q
if errorlevel 1 exit /b 1

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [ERROR] ffmpeg not found in PATH. Please install ffmpeg before packaging.
    exit /b 1
)

echo Building MS_json.exe (onefile) ...
python -m PyInstaller MS_json.spec --noconfirm --clean
if errorlevel 1 exit /b 1

echo.
echo Done: dist\MS_json.exe
pause
