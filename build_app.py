#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Wavethings
"""
Wavethings Kit Creator - macOS app builder (build_app.py).
Creates "Wavethings Kit Creator.app" so you can open the interface with a double click.

Run it ONCE, with the three files in the same folder:
    python3 build_app.py
Then drag "Wavethings Kit Creator.app" to Applications (or the Dock).
To update the app after changing the scripts, run this file again.
"""
import os, shutil, struct, subprocess, sys, zlib
from pathlib import Path

_v = (os.environ.get("KITCREATOR_LANG") or os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
LANG = "es" if _v.startswith("es") else "en"
T = {
    "en": {"missing": "Files missing next to build_app.py: {files}",
           "icon": "Creating icon…",
           "noicon": "  (the icon could not be created; the app will work with the generic icon)",
           "done": "\nDone: {app}\nDrag it to Applications or the Dock and open it with a double click."},
    "es": {"missing": "Faltan archivos junto a build_app.py: {files}",
           "icon": "Creando icono…",
           "noicon": "  (no se pudo crear el icono; la app funcionará igualmente con el icono genérico)",
           "done": "\nListo: {app}\nArrástrala a Aplicaciones o al Dock y ábrela con doble clic."},
}[LANG]

HERE = Path(__file__).resolve().parent
APP_NAME = "Wavethings Kit Creator"
APP = HERE / f"{APP_NAME}.app"
FILES = ["mpc_kit_creator.py", "kit_creator_gui.py"]

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Wavethings Kit Creator</string>
<key>CFBundleDisplayName</key><string>Wavethings Kit Creator</string>
<key>CFBundleIdentifier</key><string>com.wavethings.kitcreator</string>
<key>CFBundleExecutable</key><string>WavethingsKitCreator</string>
<key>NSHumanReadableCopyright</key><string>Copyright (c) 2026 Wavethings - MIT License</string>
<key>CFBundleIconFile</key><string>icon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1.0</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>LSMinimumSystemVersion</key><string>10.13</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
"""

LAUNCHER = r"""#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
export KITCREATOR_DATA="$HOME/Library/Application Support/Wavethings Kit Creator"
OLD="$HOME/Library/Application Support/Kit Creator"   # settings from the pre-rename version
if [ ! -d "$KITCREATOR_DATA" ] && [ -d "$OLD" ]; then cp -R "$OLD" "$KITCREATOR_DATA"; fi
export KITCREATOR_AUTOQUIT=1
mkdir -p "$KITCREATOR_DATA"
for PY in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if [ -x "$PY" ]; then
    exec "$PY" "$DIR/kit_creator_gui.py" >> "$KITCREATOR_DATA/log.txt" 2>&1
  fi
done
osascript -e 'display alert "Wavethings Kit Creator needs Python 3 / necesita Python 3" message "Open Terminal and run / Abre la Terminal y ejecuta: xcode-select --install"'
"""

# ---------- icon: 4x4 grid of pads, PNG written by hand ----------
PADS = [  # top to bottom
    (0x33, 0x99, 0xFF), (0x33, 0x99, 0xFF), (0xFF, 0xD2, 0x1F), (0xFF, 0xD2, 0x1F),
    (0xFF, 0x7A, 0x1A), (0x3C, 0x3F, 0x46), (0xFF, 0x7A, 0x1A), (0x3C, 0x3F, 0x46),
    (0x3C, 0x3F, 0x46), (0x2E, 0xCC, 0x71), (0x3C, 0x3F, 0x46), (0x2E, 0xCC, 0x71),
    (0xFF, 0x3B, 0x30), (0xFF, 0x3B, 0x30), (0x3C, 0x3F, 0x46), (0xFF, 0x3B, 0x30),
]


def _cov(d):  # antialiased coverage from a signed distance (px)
    return 1.0 if d <= -0.5 else 0.0 if d >= 0.5 else 0.5 - d


def _sdf(px, py, x0, y0, x1, y1, r):
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    qx, qy = abs(px - cx) - ((x1 - x0) / 2 - r), abs(py - cy) - ((y1 - y0) / 2 - r)
    ox, oy = max(qx, 0), max(qy, 0)
    return (ox * ox + oy * oy) ** 0.5 + min(max(qx, qy), 0) - r


def png(size):
    m = size * 0.09
    x0, x1 = m, size - m
    gm = size * 0.19                      # grid margin
    gx0, gx1 = gm, size - gm
    pitch = (gx1 - gx0) / 4
    pad_m = pitch * 0.09
    rows = []
    for y in range(size):
        row = bytearray([0])
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            a = _cov(_sdf(px, py, x0, x0, x1, x1, size * 0.2))
            if a <= 0:
                row += b"\0\0\0\0"
                continue
            col = (0x1B, 0x1C, 0x1F)
            if gx0 <= px < gx1 and gx0 <= py < gx1:
                i, j = int((px - gx0) // pitch), int((py - gx0) // pitch)
                cx0, cy0 = gx0 + i * pitch + pad_m, gx0 + j * pitch + pad_m
                c = _cov(_sdf(px, py, cx0, cy0, cx0 + pitch - 2 * pad_m, cy0 + pitch - 2 * pad_m, pitch * 0.16))
                if c > 0:
                    pc = PADS[j * 4 + i]
                    col = tuple(round(pc[k] * c + col[k] * (1 - c)) for k in range(3))
            row += bytes(col) + bytes([round(a * 255)])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_icon(dest):
    iconset = dest.parent / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    cache = {}
    for name, size in [("16x16", 16), ("16x16@2x", 32), ("32x32", 32), ("32x32@2x", 64),
                       ("128x128", 128), ("128x128@2x", 256), ("256x256", 256), ("256x256@2x", 512),
                       ("512x512", 512), ("512x512@2x", 1024)]:
        cache.setdefault(size, png(size))
        (iconset / f"icon_{name}.png").write_bytes(cache[size])
    try:
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], check=True,
                       capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False
    finally:
        shutil.rmtree(iconset, ignore_errors=True)


def main():
    missing = [f for f in FILES if not (HERE / f).is_file()]
    if missing:
        sys.exit(T["missing"].format(files=", ".join(missing)))
    if APP.exists():
        shutil.rmtree(APP)
    macos, res = APP / "Contents" / "MacOS", APP / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)
    for f in FILES:
        shutil.copy2(HERE / f, res / f)
    (APP / "Contents" / "Info.plist").write_text(PLIST)
    launcher = macos / "WavethingsKitCreator"
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)
    print(T["icon"])
    if not make_icon(res / "icon.icns"):
        print(T["noicon"])
    print(T["done"].format(app=APP))


if __name__ == "__main__":
    main()
