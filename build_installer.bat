@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 用法: build_installer.bat [版本号]   （版本号可选，默认 1.0.0，如 build_installer.bat 1.2.0）
set VERSION=1.0.0
if not "%~1"=="" set VERSION=%~1

echo [1/4] Installing build dependencies...
python -m pip install -r requirements.txt pyinstaller -q
if errorlevel 1 exit /b 1

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [ERROR] ffmpeg not found in PATH. Please install ffmpeg before packaging.
    exit /b 1
)

echo [2/4] Building MS_json onedir (dist\MS_json) ...
if exist "dist\MS_json" rmdir /s /q "dist\MS_json"
python -m PyInstaller MS_json_installer.spec --noconfirm --clean
if errorlevel 1 exit /b 1

echo [3/4] Locating Inno Setup compiler...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC for /f "delims=" %%P in ('where ISCC 2^>nul') do if not defined ISCC set "ISCC=%%P"
if not defined ISCC (
    echo [ERROR] Inno Setup compiler ISCC.exe not found.
    echo Install it with: winget install JRSoftware.InnoSetup
    exit /b 1
)

echo [4/4] Compiling installer (version %VERSION%) ...
"%ISCC%" installer\MS_json_installer.iss /DMyAppVersion=%VERSION%
if errorlevel 1 exit /b 1

echo.
echo Done: dist\MS_json_Setup.exe  (dist\MS_json\ is the portable folder)
pause
