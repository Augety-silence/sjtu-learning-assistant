#!/usr/bin/env python3
"""Generate reproducible macOS and dashboard icons from the checked-in sources."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
APP_ICON_SOURCE = ROOT / "packaging" / "assets" / "app-icon-source.jpg"
APP_ICON_PNG = ROOT / "packaging" / "assets" / "app-icon.png"
LOGO_SOURCE = ROOT / "dashboard-web" / "src" / "assets" / "app-logo-source.jpg"
LOGO_OUTPUT = ROOT / "dashboard-web" / "src" / "assets" / "app-logo.png"
ICONSET = ROOT / "build" / "AppIcon.iconset"
OUTPUT = ROOT / "packaging" / "app.icns"
APP_ICON_CANVAS_SIZE = 1024
APP_ICON_SUBJECT_SIZE = 824
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


def render_app_icon() -> Image.Image:
    """Render the icon inside the macOS safe area with transparent breathing room."""
    with Image.open(APP_ICON_SOURCE) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
    side = min(source.size)
    square = ImageOps.fit(
        source,
        (side, side),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    ).convert("RGBA")
    mask = Image.new("L", square.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, side - 1, side - 1),
        radius=round(side * 0.22),
        fill=255,
    )
    square.putalpha(mask)
    subject = square.resize(
        (APP_ICON_SUBJECT_SIZE, APP_ICON_SUBJECT_SIZE), Image.Resampling.LANCZOS
    )
    canvas = Image.new(
        "RGBA", (APP_ICON_CANVAS_SIZE, APP_ICON_CANVAS_SIZE), (0, 0, 0, 0)
    )
    inset = (APP_ICON_CANVAS_SIZE - APP_ICON_SUBJECT_SIZE) // 2
    canvas.alpha_composite(subject, (inset, inset))
    return canvas


def _white_matte_alpha(red: int, green: int, blue: int) -> int:
    deficit = 255 - min(red, green, blue)
    if deficit <= 18:
        return 0
    if deficit >= 72:
        return 255
    position = (deficit - 18) / 54
    smooth = position * position * (3 - 2 * position)
    return round(255 * smooth)


def render_dashboard_logo() -> Image.Image:
    """Remove the near-white JPEG matte, unmatte edges, and add transparent safety space."""
    with Image.open(LOGO_SOURCE) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
    pixels: list[tuple[int, int, int, int]] = []
    for red, green, blue in source.getdata():
        alpha = _white_matte_alpha(red, green, blue)
        if alpha == 0:
            pixels.append((0, 0, 0, 0))
            continue
        # Recover foreground colours from a white JPEG matte to avoid pale halos.
        channels = tuple(
            max(0, min(255, 255 - round((255 - value) * 255 / alpha)))
            for value in (red, green, blue)
        )
        pixels.append((*channels, alpha))
    transparent = Image.new("RGBA", source.size)
    transparent.putdata(pixels)
    alpha_box = transparent.getchannel("A").getbbox()
    if alpha_box is None:
        raise RuntimeError("UI logo source contains no non-white subject.")
    subject = transparent.crop(alpha_box)
    max_subject = 820
    subject.thumbnail((max_subject, max_subject), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    offset = ((1024 - subject.width) // 2, (1024 - subject.height) // 2)
    canvas.alpha_composite(subject, offset)
    return canvas


def main() -> int:
    APP_ICON_PNG.parent.mkdir(parents=True, exist_ok=True)
    app_icon = render_app_icon()
    app_icon.save(APP_ICON_PNG, optimize=True)

    LOGO_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    render_dashboard_logo().save(LOGO_OUTPUT, optimize=True)

    if shutil.which("iconutil") is None:
        raise SystemExit("错误：生成 .icns 需要 macOS iconutil。")
    shutil.rmtree(ICONSET, ignore_errors=True)
    ICONSET.mkdir(parents=True)
    for name, size in SIZES.items():
        app_icon.resize((size, size), Image.Resampling.LANCZOS).save(ICONSET / name)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(OUTPUT)], check=True)
    print(
        "已从保留的 JPG 源图生成透明 1024 PNG、AppIcon.iconset、app.icns 和 UI logo。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
