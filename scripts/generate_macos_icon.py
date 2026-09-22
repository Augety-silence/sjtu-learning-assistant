#!/usr/bin/env python3
"""Generate a neutral macOS .icns icon from original vector-like primitives."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICONSET = ROOT / "build" / "AppIcon.iconset"
OUTPUT = ROOT / "packaging" / "app.icns"
SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def render() -> Image.Image:
    scale = 4
    image = Image.new("RGBA", (1024 * scale, 1024 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = tuple(value * scale for value in (64, 64, 960, 960))
    draw.rounded_rectangle(box, radius=208 * scale, fill="#2458D3")
    left_book = tuple(value * scale for value in (210, 230, 512, 760))
    right_book = tuple(value * scale for value in (512, 230, 814, 760))
    draw.rounded_rectangle(left_book, radius=42 * scale, fill="#FFFFFF")
    draw.rounded_rectangle(right_book, radius=42 * scale, fill="#E9F0FF")
    draw.line(tuple(value * scale for value in (512, 270, 512, 758)), fill="#B9CAFF", width=22 * scale)
    width = 28 * scale
    draw.line(tuple(value * scale for value in (596, 610, 672, 390, 748, 610)), fill="#214EB5", width=width, joint="curve")
    draw.line(tuple(value * scale for value in (622, 530, 722, 530)), fill="#214EB5", width=width)
    return image.resize((1024, 1024), Image.Resampling.LANCZOS)


def main() -> int:
    if shutil.which("iconutil") is None:
        raise SystemExit("错误：生成 .icns 需要 macOS iconutil。")
    ICONSET.mkdir(parents=True, exist_ok=True)
    source = render()
    for name, size in SIZES.items():
        source.resize((size, size), Image.Resampling.LANCZOS).save(ICONSET / name)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(OUTPUT)], check=True)
    print(f"已生成 {OUTPUT.relative_to(ROOT)}（原创中性书本/字母图形）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
