#!/usr/bin/env python3
"""Generate the Windows executable icon from the checked-in source image."""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "assets" / "app-icon-source.jpg"
OUTPUT = ROOT / "packaging" / "app.ico"


def main() -> None:
    with Image.open(SOURCE) as source:
        icon = source.convert("RGBA")
        icon.thumbnail((256, 256), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        canvas.alpha_composite(icon, ((256 - icon.width) // 2, (256 - icon.height) // 2))
        canvas.save(OUTPUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"已生成 Windows 图标：{OUTPUT}")


if __name__ == "__main__":
    main()
