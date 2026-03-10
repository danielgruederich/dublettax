#!/usr/bin/env python3
"""Generate DublettaX.icns app icon."""
import os
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
BG   = (31, 36, 33)       # #1f2421
GRN  = (73, 160, 120)     # #49a078

def make_icon(size):
    img  = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)

    # Rounded rectangle background using ellipse corners
    r = size // 8
    draw.rounded_rectangle([0, 0, size-1, size-1], radius=r, fill=BG)

    # Two overlapping circles — simple vinyl / dedup visual
    cx, cy = size // 2, size // 2
    r1 = int(size * 0.30)
    r2 = int(size * 0.30)
    offset = int(size * 0.12)
    teal = (33, 104, 105)   # #216869

    draw.ellipse([cx - offset - r1, cy - r1, cx - offset + r1, cy + r1],
                 fill=teal, outline=None)
    draw.ellipse([cx + offset - r2, cy - r2, cx + offset + r2, cy + r2],
                 fill=GRN, outline=None)

    # Overlap area (intersection) — slightly lighter
    draw.ellipse([cx - offset // 2, cy - r1 // 2,
                  cx + offset // 2, cy + r1 // 2],
                 fill=(86, 178, 140))

    # "D" letter mark
    try:
        font = ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc", int(size * 0.22))
        bbox = draw.textbbox((0, 0), "Dx", font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) // 2, (size - th) // 2 + int(size * 0.28)),
                  "Dx", fill=(220, 225, 222), font=font)
    except Exception:
        pass

    return img

# ── Build iconset ────────────────────────────────────────────────────────────
out_dir = Path("/Users/danielgruederich/DublettaX/DublettaX.iconset")
out_dir.mkdir(exist_ok=True)

sizes = [16, 32, 64, 128, 256, 512, 1024]
for s in sizes:
    img = make_icon(s)
    img.save(out_dir / f"icon_{s}x{s}.png")
    if s <= 512:
        img2 = make_icon(s * 2)
        img2.save(out_dir / f"icon_{s}x{s}@2x.png")

# ── Convert to .icns ────────────────────────────────────────────────────────
icns_path = Path("/Users/danielgruederich/DublettaX/DublettaX.icns")
subprocess.run(["iconutil", "-c", "icns", str(out_dir), "-o", str(icns_path)], check=True)
print(f"Icon created: {icns_path}")
