"""生成正方形透明背景的应用图标。"""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
OUTPUT = Path(__file__).resolve().parent.parent / "icon.ico"
BG = (56, 103, 214, 255)
BG_DARK = (74, 66, 186, 255)
NOTE = (255, 255, 255, 250)


def create_icon() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    inset = 24
    box = [inset, inset, SIZE - inset, SIZE - inset]
    draw.rounded_rectangle(box, radius=48, fill=BG)
    draw.rounded_rectangle(
        [box[0] + 8, box[1] + 8, box[2] - 8, box[3] - 8],
        radius=40,
        fill=BG_DARK,
    )

    cx, cy = 108, 128
    draw.ellipse([cx - 20, cy + 10, cx + 20, cy + 50], fill=NOTE)
    draw.rectangle([cx + 8, cy - 42, cx + 16, cy + 28], fill=NOTE)
    draw.polygon(
        [(cx + 16, cy - 42), (cx + 58, cy - 24), (cx + 58, cy - 6), (cx + 16, cy - 24)],
        fill=NOTE,
    )

    bar_x = 154
    for i, h in enumerate((36, 52, 28, 44)):
        x = bar_x + i * 16
        draw.rounded_rectangle(
            [x, 170 - h, x + 10, 170],
            radius=4,
            fill=(186, 220, 255, 240),
        )

    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(OUTPUT, format="ICO", sizes=sizes)
    print(f"icon saved: {OUTPUT}")


if __name__ == "__main__":
    create_icon()
