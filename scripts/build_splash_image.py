"""打包时生成 PyInstaller bootloader 用的纯文字启动图。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SPLASH_WIDTH = 360
SPLASH_HEIGHT = 148
APP_TITLE = "MS JSON 导出工具"
BOOT_STATUS = "正在启动…"


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def write_boot_splash_image(output_path: str | Path) -> Path:
    output = Path(output_path)
    image = Image.new("RGB", (SPLASH_WIDTH, SPLASH_HEIGHT), (32, 32, 32))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [0, 0, SPLASH_WIDTH - 1, SPLASH_HEIGHT - 1],
        radius=12,
        outline=(72, 72, 72),
        width=1,
    )

    title_font = _load_font(18, bold=True)
    status_font = _load_font(12)
    draw.text(
        (SPLASH_WIDTH // 2, 40),
        APP_TITLE,
        fill=(245, 245, 245),
        font=title_font,
        anchor="mm",
    )
    draw.text(
        (SPLASH_WIDTH // 2, 76),
        BOOT_STATUS,
        fill=(160, 160, 160),
        font=status_font,
        anchor="mm",
    )
    image.save(output, format="PNG")
    return output
