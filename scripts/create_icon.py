"""从 icon.png 生成多尺寸 icon.ico（图标源文件缺失时按旧风格绘制兜底）。"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "icon.png"
OUTPUT = ROOT / "icon.ico"
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def _draw_fallback(size: int) -> Image.Image:
    """绘制旧风格的蓝色键盘图标作为兜底。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    inset = 24
    box = [inset, inset, size - inset, size - inset]
    draw.rounded_rectangle(box, radius=48, fill=(56, 103, 214, 255))
    draw.rounded_rectangle(
        [box[0] + 8, box[1] + 8, box[2] - 8, box[3] - 8],
        radius=40,
        fill=(74, 66, 186, 255),
    )

    cx, cy = 108, 128
    draw.ellipse([cx - 20, cy + 10, cx + 20, cy + 50], fill=(255, 255, 255, 250))
    draw.rectangle([cx + 8, cy - 42, cx + 16, cy + 28], fill=(255, 255, 255, 250))
    draw.polygon(
        [(cx + 16, cy - 42), (cx + 58, cy - 24), (cx + 58, cy - 6), (cx + 16, cy - 24)],
        fill=(255, 255, 255, 250),
    )

    bar_x = 154
    for i, h in enumerate((36, 52, 28, 44)):
        x = bar_x + i * 16
        draw.rounded_rectangle(
            [x, 170 - h, x + 10, 170],
            radius=4,
            fill=(186, 220, 255, 240),
        )
    return img


def create_icon() -> None:
    if SRC.exists():
        source = Image.open(SRC).convert("RGBA")
        frames = [source.resize(s, Image.LANCZOS) for s in SIZES]
        print(f"从 {SRC} 生成图标")
    else:
        source = _draw_fallback(256)
        frames = [source.resize(s, Image.LANCZOS) for s in SIZES]
        print(f"{SRC} 不存在，使用兜底绘制")

    frames[0].save(OUTPUT, format="ICO", sizes=SIZES, append_images=frames[1:])
    print(f"icon saved: {OUTPUT}")


if __name__ == "__main__":
    create_icon()
