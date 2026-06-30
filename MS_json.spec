# -*- mode: python ; coding: utf-8 -*-
import importlib.util
import os
import shutil

from PyInstaller.building.splash import Splash
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

_splash_builder_path = os.path.join(SPECPATH, "scripts", "build_splash_image.py")
_builder_spec = importlib.util.spec_from_file_location("build_splash_image", _splash_builder_path)
_builder = importlib.util.module_from_spec(_builder_spec)
_builder_spec.loader.exec_module(_builder)
_splash_image_path = os.path.join(SPECPATH, "_splash_build.png")
_builder.write_boot_splash_image(_splash_image_path)

splash = Splash(
    _splash_image_path,
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(12, 142),
    text_size=-9,
    text_color="#909090",
    text_default="",
    always_on_top=True,
    max_img_size=(360, 148),
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name="MS_json",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",
)
