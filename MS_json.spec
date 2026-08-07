# -*- mode: python ; coding: utf-8 -*-
import importlib.util
import os

from PyInstaller.building.splash import Splash
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = [("icon.png", "."), ("icon.ico", ".")]
binaries = []
hiddenimports = ["mido.backends.backend_mido", "mido.backends.amidi", "openpyxl"]

for package in ("qfluentwidgets", "PyQt6", "librosa", "numpy", "scipy", "soundfile", "audioread"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

def _resolve_chocolatey_real_ffmpeg() -> str | None:
    """沿 chocolatey\\lib 目录解析 shim 指向的真实 ffmpeg.exe。

    shim 用相对路径 ..\\lib\\<包名>\\tools\\... 指向真实文件，真实文件为静态编译的
    自包含 exe，可独立运行；递归搜索取体积最大的候选（shim 本体仅数百 KB）。
    """
    import glob as _glob

    shim_path = None
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        directory = directory.strip('"')
        if not directory:
            continue
        candidate = os.path.join(directory, "ffmpeg.exe")
        if os.path.isfile(candidate) and "chocolatey" in candidate.lower():
            shim_path = candidate
            break
    if shim_path is None:
        return None

    lib_dir = os.path.join(os.path.dirname(os.path.dirname(shim_path)), "lib")
    candidates = [
        p
        for p in _glob.glob(os.path.join(lib_dir, "ffmpeg", "**", "ffmpeg.exe"), recursive=True)
        if os.path.isfile(p)
    ]
    if not candidates:
        return None
    # 真实 ffmpeg 体积远大于 shim，取最大的候选
    return max(candidates, key=lambda p: os.path.getsize(p))


def _find_real_ffmpeg() -> str:
    """在 PATH 中查找可独立运行的 ffmpeg.exe。

    跳过 Chocolatey 的代理 shim（如 C:\\ProgramData\\chocolatey\\bin\\ffmpeg.exe）：
    该 shim 体积很小，内部通过相对路径指向 chocolatey\\lib 下的真实文件，一旦被
    打包进单文件 exe 并在运行时解压到临时目录，就找不到目标程序，导致 ffmpeg
    调用直接失败（退出码 4294967295，无任何错误输出）。
    """
    candidates = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        directory = directory.strip('"')
        if not directory:
            continue
        candidate = os.path.join(directory, "ffmpeg.exe")
        if os.path.isfile(candidate):
            candidates.append(candidate)

    for candidate in candidates:
        if "chocolatey" not in candidate.lower():
            return candidate

    # PATH 中只有 Chocolatey 代理时，解析出包内真实的自包含 ffmpeg 打包
    choco_real = _resolve_chocolatey_real_ffmpeg()
    if choco_real:
        print(f"使用 Chocolatey 包内的真实 ffmpeg: {choco_real}")
        return choco_real
    if candidates:
        print(f"警告：仅找到 Chocolatey 的 ffmpeg 代理程序 {candidates[0]}，"
              "该文件打包后可能无法运行，建议安装独立版 ffmpeg 并调整 PATH 顺序。")
        return candidates[0]
    raise SystemExit("构建失败：未找到 ffmpeg，请先安装 ffmpeg 并确保命令行可用。")


ffmpeg_path = _find_real_ffmpeg()
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
    # torch/torchaudio 是 scipy._lib.array_api_compat 的可选适配子模块被
    # collect_all("scipy") 拖入的；pandas 为 librosa 等可选功能依赖。
    # 项目代码均未使用，排除可显著减小体积（torch 全家桶可达数百 MB）。
    excludes=["torch", "torchaudio", "pandas"],
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
