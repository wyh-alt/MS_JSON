# -*- mode: python ; coding: utf-8 -*-
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = [("icon.ico", ".")]
binaries = []
hiddenimports = ["mido.backends.backend_mido", "mido.backends.amidi", "openpyxl"]

for package in ("qfluentwidgets", "PyQt6", "librosa", "numpy", "scipy", "soundfile", "audioread"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

ffmpeg_path = shutil.which("ffmpeg")
if not ffmpeg_path:
    raise SystemExit("构建失败：未找到 ffmpeg，请先安装 ffmpeg 并确保命令行可用。")
binaries.append((ffmpeg_path, "."))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MS_json",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MS_json",
)
