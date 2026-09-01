"""Generate every VRKA production icon from the canonical two-colour wolf PNG.

Canonical artwork: assets/branding/vrka-build010-canonical-source.png - the
approved two-colour wolf (vibrant purple geometry with internal black geometry:
inner ears, eye cutouts, nose, cheek markings) rendered on a solid black
background. This is the single canonical artwork for the build010 branding
pipeline; the artwork is never redrawn, recolored, simplified, or reinterpreted.

This tool:
  1. removes ONLY the external black background (flood fill from the image
     border); internal black regions enclosed by the purple geometry are kept
     as opaque black geometry,
  2. converts the antialiased silhouette edge to partial alpha so the wolf
     keeps its correct edges on any background,
  3. normalizes the wolf into the production 1024 px canvas using uniform
     scaling and centering only (preserving proportions and silhouette),
  4. renders every production PNG, ICO, and ICNS size from that transparent
     master.

Run with:
    python tools/generate_brand_assets.py
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_DIR / "assets" / "branding"
CANONICAL_NAME = "vrka-build010-canonical-source.png"
CANONICAL_PATH = OUTPUT_DIR / CANONICAL_NAME

_source = (PROJECT_DIR / "vrka_downloader.py").read_text(encoding="utf-8")
APP_VERSION = re.search(r'^APP_VERSION = "([^"]+)"', _source, re.M).group(1)
APP_BUILD = re.search(r'^APP_BUILD = "([^"]+)"', _source, re.M).group(1)

ACCENT_HEX = "#8140DC"  # application UI accent colour (unchanged by this pipeline)
BG_THRESHOLD = 32  # pixels with every channel <= this value count as background
PURPLE_STRONG_DELTA = 40  # blue - red delta used to identify solid purple pixels
CANVAS = 1024
SAFE_MARGIN = 72  # production framing margin, matching the build008 baseline
PNG_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ICNS_SIZES = (32, 64, 128, 256, 512, 1024)
MIN_ALPHA = 8  # drop sub-visible LANCZOS ringing below this alpha value


def _channel_max(image_rgb):
    r, g, b = image_rgb.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def modal_purple_max_channel(source_rgb):
    """Modal max-channel of solid purple pixels; used for edge alpha recovery."""
    r, g, b = source_rgb.split()
    maxch = _channel_max(source_rgb)
    strong = Image.eval(ImageChops.difference(b, r), lambda v: 255 if v > PURPLE_STRONG_DELTA else 0)
    counter = Counter()
    width, height = source_rgb.size
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            if strong.getpixel((x, y)):
                counter[maxch.getpixel((x, y))] += 1
    return counter.most_common(1)[0][0]


def extract_foreground(source_rgb):
    """Remove only the external black background; preserve internal black geometry."""
    width, height = source_rgb.size
    maxch = _channel_max(source_rgb)
    near_black = maxch.point(lambda v: 255 if v <= BG_THRESHOLD else 0)

    padded = Image.new("L", (width + 2, height + 2), 255)
    padded.paste(near_black, (1, 1))
    ImageDraw.floodfill(padded, (0, 0), 0, thresh=0)
    interior = padded.crop((1, 1, width + 1, height + 1))  # 255 = enclosed internal black
    external_bg = ImageChops.multiply(near_black, ImageChops.invert(interior))  # 255 = external background

    pmax = modal_purple_max_channel(source_rgb)
    ring = ImageChops.subtract(external_bg.filter(ImageFilter.MaxFilter(3)), external_bg)

    out = source_rgb.copy()
    alpha = Image.new("L", source_rgb.size, 255)
    bg_px = external_bg.load()
    ring_px = ring.load()
    max_px = maxch.load()
    out_px = out.load()
    a_px = alpha.load()
    for y in range(height):
        for x in range(width):
            if bg_px[x, y]:
                a_px[x, y] = 0
            elif ring_px[x, y]:
                value = max_px[x, y]
                if value < pmax:
                    scale = pmax / value
                    rr, gg, bb = out_px[x, y]
                    a_px[x, y] = max(1, round(value * 255 / pmax))
                    out_px[x, y] = (
                        min(255, round(rr * scale)),
                        min(255, round(gg * scale)),
                        min(255, round(bb * scale)),
                    )
    master = Image.merge("RGBA", (*out.split(), alpha))
    stats = {
        "background_threshold": BG_THRESHOLD,
        "external_background_pixels": external_bg.histogram()[255],
        "internal_black_pixels": interior.histogram()[255],
        "purple_max_channel": pmax,
    }
    return master, stats


def unpremultiply(image):
    """Recover straight (non-premultiplied) RGB after resizing on black transparency."""
    r, g, b, a = image.split()
    a = a.point(lambda value: 0 if value < MIN_ALPHA else value)
    out = Image.merge("RGBA", (r, g, b, a))
    px = out.load()
    width, height = out.size
    for y in range(height):
        for x in range(width):
            rr, gg, bb, aa = px[x, y]
            if 0 < aa < 255:
                scale = 255 / aa
                px[x, y] = (
                    min(255, round(rr * scale)),
                    min(255, round(gg * scale)),
                    min(255, round(bb * scale)),
                    aa,
                )
    return out


def normalize_master(master):
    """Uniformly scale and centre the wolf into the production canvas."""
    bbox = master.getchannel("A").getbbox()
    left, top, right, bottom = bbox
    content = master.crop(bbox)
    content_w = right - left
    content_h = bottom - top
    target = CANVAS - 2 * SAFE_MARGIN
    scale = min(target / content_w, target / content_h)
    new_w = max(1, round(content_w * scale))
    new_h = max(1, round(content_h * scale))
    resized = unpremultiply(content.resize((new_w, new_h), Image.Resampling.LANCZOS))
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.paste(resized, ((CANVAS - new_w) // 2, (CANVAS - new_h) // 2), resized)
    normalization = {
        "source_bbox": [left, top, right, bottom],
        "scale": round(scale, 6),
        "offset": [(CANVAS - new_w) // 2, (CANVAS - new_h) // 2],
    }
    return canvas, normalization


def render_size(master, size):
    return unpremultiply(master.resize((size, size), Image.Resampling.LANCZOS))


def count_dark_and_purple(image_rgba):
    """Count opaque black-ish and purple-ish pixels for two-colour verification."""
    data = image_rgba.convert("RGBA")
    px = data.load()
    width, height = data.size
    opaque = dark = purple = 0
    for y in range(height):
        for x in range(width):
            rr, gg, bb, aa = px[x, y]
            if not aa:
                continue
            opaque += 1
            value = max(rr, gg, bb)
            if value < 60:
                dark += 1
            elif bb > rr + 20 and bb >= 60:
                purple += 1
    return opaque, dark, purple


def alpha_symmetry_fraction(image_rgba):
    """Fraction of alpha pixels that differ from the mirror image beyond noise."""
    alpha = image_rgba.getchannel("A")
    difference = ImageChops.difference(alpha, alpha.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    width, height = alpha.size
    differing = sum(1 for value in difference.get_flattened_data() if value > 4)
    return round(differing / (width * height), 6)


def validate_png(path, expected_size):
    with Image.open(path) as image:
        image.load()
        assert image.mode == "RGBA", f"{path.name}: expected RGBA"
        assert image.size == (expected_size, expected_size), f"{path.name}: wrong size"
        alpha = image.getchannel("A")
        assert alpha.getextrema() == (0, 255), f"{path.name}: incomplete alpha range"
        assert all(image.getpixel(point)[3] == 0 for point in (
            (0, 0), (expected_size - 1, 0), (0, expected_size - 1), (expected_size - 1, expected_size - 1),
        )), f"{path.name}: corner must be transparent"
        bbox = alpha.getbbox()
        assert bbox is not None, f"{path.name}: no visible content"
        left, top, right, bottom = bbox
        assert min(left, top, expected_size - right, expected_size - bottom) >= 1, (
            f"{path.name}: mark has insufficient transparent padding: {bbox}"
        )
        opaque, dark, purple = count_dark_and_purple(image)
        assert dark > 0, f"{path.name}: internal black geometry lost"
        assert purple > 0, f"{path.name}: purple geometry lost"
        ratio = dark / opaque
        assert 0.02 <= ratio <= 0.60, f"{path.name}: unexpected dark/opaque ratio {ratio:.3f}"
        assert purple / opaque >= 0.40, f"{path.name}: unexpected purple/opaque ratio"
        return bbox


def checkerboard(size, tile=12):
    image = Image.new("RGB", (size, size), (238, 238, 238))
    draw = ImageDraw.Draw(image)
    for y in range(0, size, tile):
        for x in range(0, size, tile):
            if (x // tile + y // tile) % 2:
                draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill=(210, 210, 210))
    return image


def make_contact_sheet(png_paths):
    sizes = (16, 20, 24, 32, 48, 64, 128, 256)
    backgrounds = (
        ("NEAR-BLACK", (9, 10, 13)),
        ("CHARCOAL", (24, 26, 33)),
        ("WHITE", (255, 255, 255)),
        ("TRANSPARENCY", None),
    )
    width, height = 1760, 1080
    sheet = Image.new("RGB", (width, height), (9, 10, 13))
    draw = ImageDraw.Draw(sheet)
    try:
        title_font = ImageFont.truetype("segoeuib.ttf", 34)
        label_font = ImageFont.truetype("segoeui.ttf", 18)
        small_font = ImageFont.truetype("consola.ttf", 14)
    except OSError:
        title_font = label_font = small_font = ImageFont.load_default()
    draw.text((36, 24), "VRKA / CANONICAL WOLF PRODUCTION CONTACT SHEET", fill=(243, 241, 247), font=title_font)
    draw.text((38, 70), "TWO-COLOUR CANONICAL  /  CANONICAL PNG MASTER  /  RGBA  /  BUILD 010", fill=(170, 166, 179), font=small_font)

    left = 170
    cell_width = 190
    row_top = 110
    row_height = 205
    for column, size in enumerate(sizes):
        draw.text((left + column * cell_width + 70, 88), f"{size}px", fill=(185, 138, 242), font=small_font)
    for row, (name, background) in enumerate(backgrounds):
        y = row_top + row * row_height
        draw.text((28, y + 78), name, fill=(170, 166, 179), font=small_font)
        for column, size in enumerate(sizes):
            x = left + column * cell_width
            panel = checkerboard(174, 12) if background is None else Image.new("RGB", (174, 174), background)
            with Image.open(png_paths[size]) as source:
                icon = source.convert("RGBA")
            native_size = min(size, 56)
            native = icon if size <= 56 else icon.resize(
                (native_size, native_size), Image.Resampling.LANCZOS
            )
            panel.paste(native, ((174 - native_size) // 2, 10), native)
            preview_size = 92 if size <= 64 else 112
            preview_filter = Image.Resampling.NEAREST if size <= 64 else Image.Resampling.LANCZOS
            preview = icon.resize((preview_size, preview_size), preview_filter)
            panel.paste(preview, ((174 - preview_size) // 2, 76), preview)
            sheet.paste(panel, (x, y))
            draw.rectangle((x, y, x + 173, y + 173), outline=(56, 60, 73), width=1)

    mapping_y = 950
    draw.line((36, mapping_y - 18, width - 36, mapping_y - 18), fill=(39, 42, 52), width=1)
    draw.text((36, mapping_y), "PRODUCTION PLACEMENTS", fill=(243, 241, 247), font=label_font)
    placement_text = (
        "Sidebar 40  /  Queue empty 32  /  History empty 32  /  Window & Alt+Tab 256  /  "
        "EXE + installer + shortcuts ICO  /  macOS app ICNS"
    )
    draw.text((36, mapping_y + 34), placement_text, fill=(170, 166, 179), font=small_font)
    destination = OUTPUT_DIR / "vrka-icon-contact-sheet.png"
    sheet.save(destination, "PNG", optimize=True)
    return destination


def generate():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    assert CANONICAL_PATH.is_file(), f"missing canonical artwork: {CANONICAL_PATH}"

    canonical_sha256 = hashlib.sha256(CANONICAL_PATH.read_bytes()).hexdigest().upper()

    with Image.open(CANONICAL_PATH) as source:
        assert source.mode == "RGB", "canonical source must be an opaque RGB render"
        source_rgb = source.convert("RGB")
        source_size = list(source_rgb.size)

    master, extraction = extract_foreground(source_rgb)
    master, normalization = normalize_master(master)
    assert master.size == (CANVAS, CANVAS)

    png_paths = {}
    for size in PNG_SIZES:
        image = render_size(master, size) if size != CANVAS else master
        path = OUTPUT_DIR / f"vrka-wolf-{size}.png"
        image.save(path, "PNG", optimize=True)
        png_paths[size] = path

    ico_source = render_size(master, 256)
    ico_source.save(
        OUTPUT_DIR / "vrka.ico",
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=[render_size(master, size) for size in ICO_SIZES],
    )

    icns_images = [render_size(master, size) for size in ICNS_SIZES]
    icns_images[-1].save(
        OUTPUT_DIR / "vrka.icns",
        format="ICNS",
        append_images=icns_images[:-1],
    )

    bounding_boxes = {str(size): validate_png(path, size) for size, path in png_paths.items()}

    with Image.open(OUTPUT_DIR / "vrka.ico") as icon:
        ico_sizes = sorted(size[0] for size in icon.ico.sizes())
    assert set(ICO_SIZES).issubset(ico_sizes), f"ICO missing sizes: {set(ICO_SIZES) - set(ico_sizes)}"

    with Image.open(OUTPUT_DIR / "vrka.icns") as icon:
        icns_entries = list(icon.info.get("sizes", []))
        icns_sizes = sorted({width * scale for width, _height, scale in icns_entries})
    assert set((32, 64, 128, 256, 512, 1024)).issubset(icns_sizes)

    contact_sheet = make_contact_sheet(png_paths)

    manifest = {
        "brand": "VRKA",
        "version": APP_VERSION,
        "build": APP_BUILD,
        "accent": ACCENT_HEX,
        "canonical_master": CANONICAL_NAME,
        "canonical_source_sha256": canonical_sha256,
        "canonical_source_size": source_size,
        "derivation": (
            "external black background removed via 8-connected border flood fill; "
            "internal black geometry preserved opaque; antialiased silhouette edge "
            "converted to partial alpha; uniform scale and centering into the production "
            "1024 canvas only"
        ),
        "two_colour": True,
        "flat_color": False,
        "alpha_symmetry_fraction": alpha_symmetry_fraction(master),
        "extraction": extraction,
        "normalization": normalization,
        "tiny_variant": None,
        "png_sizes": list(PNG_SIZES),
        "ico_sizes": ico_sizes,
        "icns_sizes": icns_sizes,
        "icns_logical_entries": icns_entries,
        "contact_sheet": contact_sheet.name,
        "placements": {
            "sidebar": 40,
            "queue_empty_state": 32,
            "history_empty_state": 32,
            "window_taskbar_alt_tab": 256,
            "windows_executable_installer_shortcuts": "vrka.ico",
            "macos_application": "vrka.icns"
        },
        "png_alpha_bounding_boxes": bounding_boxes,
    }
    (OUTPUT_DIR / "icon-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    generate()
