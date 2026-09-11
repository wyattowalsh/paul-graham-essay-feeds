from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import mimetypes
import shutil
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cairosvg import svg2png
from PIL import Image, ImageDraw, features

PROJECT = "wyattowalsh/paul-graham-essay-feeds"
NIGHT = "#061827"
NIGHT_BORDER = "#173A49"
GREEN = "#39FF70"
GREEN_MICRO = "#52FF7F"
CREAM = "#F4EFE4"
ALT_TEXT = (
    "A green dot-matrix pedestrian signal broadcasts a dotted RSS symbol and "
    "three floating essay pages toward a seated classical statue at night."
)

# Source-derived emitter centroids, normalized into the canonical 512×512 mark.
# These preserve the idiosyncratic topology of the final pedestrian glyph while
# avoiding a photographic favicon that collapses at small sizes.
FIGURE_POINTS: tuple[tuple[float, float], ...] = (
    (138.59, 50.00),
    (122.17, 64.92),
    (167.07, 69.36),
    (147.12, 74.63),
    (132.82, 89.78),
    (168.23, 92.67),
    (153.16, 104.73),
    (139.31, 119.97),
    (159.38, 128.50),
    (124.59, 134.48),
    (181.33, 139.90),
    (146.29, 143.75),
    (167.92, 152.04),
    (117.49, 156.20),
    (204.17, 154.30),
    (136.28, 165.21),
    (189.83, 165.52),
    (158.38, 173.83),
    (114.24, 179.00),
    (217.38, 180.71),
    (179.37, 181.78),
    (135.92, 195.07),
    (106.08, 196.48),
    (158.36, 202.64),
    (226.86, 202.72),
    (180.77, 209.59),
    (90.47, 210.65),
    (73.14, 219.84),
    (233.53, 222.45),
    (50.88, 225.74),
    (135.66, 227.60),
    (158.95, 231.47),
    (181.08, 235.78),
    (244.12, 250.95),
    (135.56, 254.33),
    (158.05, 257.97),
    (181.48, 263.71),
    (258.39, 277.77),
    (133.52, 280.83),
    (158.20, 284.53),
    (182.92, 291.34),
    (122.44, 304.29),
    (193.76, 317.10),
    (109.28, 327.00),
    (204.60, 341.05),
    (92.35, 355.14),
    (214.28, 364.70),
    (75.09, 385.75),
    (225.11, 389.35),
    (46.00, 403.05),
    (66.64, 411.94),
    (235.84, 413.64),
    (246.26, 438.42),
    (232.51, 456.73),
    (261.00, 462.00),
)


@dataclass(frozen=True)
class AssetRecord:
    path: str
    role: str
    format: str
    mime_type: str
    width: int | None
    height: int | None
    bytes: int
    sha256: str
    icc_profile_embedded: bool | None


# Fixed standard sRGB profile bytes keep generated binaries stable across runs.
SRGB = base64.b64decode(
    "AAACTGxjbXMEQAAAbW50clJHQiBYWVogB+oACQALAA0AAwARYWNzcEFQUEwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1sY21zAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAALZGVzYwAAAQgAAAA2Y3BydAAAAUAAAABMd3RwdAAAAYwAAAAUY2hhZAAAAaAAAAAsclhZWgAAAcwAAAAUYlhZWgAAAeAAAAAUZ1hZWgAAAfQAAAAUclRSQwAAAggAAAAgZ1RSQwAAAggAAAAgYlRSQwAAAggAAAAgY2hybQAAAigAAAAkbWx1YwAAAAAAAAABAAAADGVuVVMAAAAaAAAAHABzAFIARwBCACAAYgB1AGkAbAB0AC0AaQBuAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAADAAAAAcAE4AbwAgAGMAbwBwAHkAcgBpAGcAaAB0ACwAIAB1AHMAZQAgAGYAcgBlAGUAbAB5WFlaIAAAAAAAAPbWAAEAAAAA0y1zZjMyAAAAAAABDEIAAAXe///zJQAAB5MAAP2Q///7of///aIAAAPcAADAblhZWiAAAAAAAABvoAAAOPUAAAOQWFlaIAAAAAAAACSfAAAPhAAAtsNYWVogAAAAAAAAYpcAALeHAAAY2XBhcmEAAAAAAAMAAAACZmYAAPKnAAANWQAAE9AAAApbY2hybQAAAAAAAwAAAACj1wAAVHsAAEzNAACZmgAAJmYAAA9c"
)


def ensure_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image.copy()
    if image.mode == "RGBA":
        bg = Image.new("RGB", image.size, NIGHT)
        bg.paste(image, mask=image.getchannel("A"))
        return bg
    return image.convert("RGB")


def save_png(image: Image.Image, path: Path, *, icc: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {"format": "PNG", "optimize": True}
    if icc:
        kwargs["icc_profile"] = SRGB
    image.save(path, **kwargs)


def save_jpeg(
    image: Image.Image,
    path: Path,
    *,
    quality: int = 92,
    progressive: bool = True,
    subsampling: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_rgb(image).save(
        path,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=progressive,
        subsampling=subsampling,
        icc_profile=SRGB,
    )


def save_webp(image: Image.Image, path: Path, *, quality: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_rgb(image).save(
        path,
        format="WEBP",
        quality=quality,
        method=6,
        icc_profile=SRGB,
    )


def save_avif(image: Image.Image, path: Path, *, quality: int = 72) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_rgb(image).save(
        path,
        format="AVIF",
        quality=quality,
        speed=6,
        icc_profile=SRGB,
    )


def resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.resize(size, Image.Resampling.LANCZOS)


def center_crop_aspect(image: Image.Image, aspect: float) -> Image.Image:
    width, height = image.size
    current = width / height
    if math.isclose(current, aspect, rel_tol=0.0, abs_tol=1e-9):
        return image.copy()
    if current > aspect:
        new_width = round(height * aspect)
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = round(width / aspect)
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def circle(cx: float, cy: float, radius: float) -> str:
    return f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}"/>'


def rss_points() -> tuple[tuple[float, float, float], ...]:
    points: list[tuple[float, float, float]] = []
    origin_x, origin_y = 316.0, 350.0
    points.append((origin_x, origin_y, 8.3))
    for index in range(6):
        angle = (2 * math.pi * index) / 6
        points.append(
            (
                origin_x + 17 * math.cos(angle),
                origin_y + 17 * math.sin(angle),
                6.2,
            )
        )
    for radius, count in ((83.0, 7), (145.0, 11)):
        for index in range(count):
            degrees = -86 + (82 * index / (count - 1))
            angle = math.radians(degrees)
            points.append(
                (
                    origin_x + radius * math.cos(angle),
                    origin_y + radius * math.sin(angle),
                    7.2,
                )
            )
    return tuple(points)


def geometry_svg(*, fill: str, glow: bool, transform: str | None = None) -> str:
    dots = "".join(circle(x, y, 7.2) for x, y in FIGURE_POINTS)
    dots += "".join(circle(x, y, radius) for x, y, radius in rss_points())
    filter_attr = ' filter="url(#green-glow)"' if glow else ""
    transform_attr = f' transform="{transform}"' if transform else ""
    return f'<g fill="{fill}"{filter_attr}{transform_attr}>{dots}</g>'


def canonical_svg(*, transparent: bool = False) -> str:
    background = "" if transparent else (
        f'<rect width="512" height="512" rx="92" fill="{NIGHT}"/>'
        f'<rect x="11" y="11" width="490" height="490" rx="82" '
        f'fill="none" stroke="{NIGHT_BORDER}" stroke-width="6" opacity=".72"/>'
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-labelledby="title">
<title id="title">Dot-matrix pedestrian and RSS mark</title>
<defs>
  <filter id="green-glow" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="5" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
</defs>
{background}
{geometry_svg(fill=GREEN, glow=True)}
</svg>
'''


def micro_svg() -> str:
    # Dedicated sub-24-pixel geometry. It intentionally omits the RSS arcs,
    # prioritizing a stable source glyph at browser-tab size.
    points = (
        (6.0, 2.6),
        (5.35, 4.0), (6.65, 4.0),
        (5.4, 5.4), (6.75, 5.5),
        (4.1, 5.8), (2.8, 6.8), (1.55, 7.25),
        (7.9, 5.9), (9.0, 6.8), (9.8, 7.95),
        (5.45, 7.0), (6.7, 7.1),
        (5.25, 8.7), (4.5, 10.3), (3.7, 11.9), (2.9, 13.6),
        (6.8, 8.7), (7.55, 10.2), (8.3, 11.8), (9.1, 13.6),
    )
    dots = "".join(circle(x, y, 0.68) for x, y in points)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" role="img" aria-labelledby="title">
<title id="title">Dot-matrix pedestrian mark</title>
<rect width="16" height="16" rx="2.5" fill="{NIGHT}"/>
<g fill="{GREEN_MICRO}">{dots}</g>
</svg>
'''


def monochrome_svg() -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-labelledby="title">
<title id="title">Monochrome dot-matrix pedestrian and RSS mark</title>
{geometry_svg(fill="currentColor", glow=False)}
</svg>
'''


def app_svg(*, maskable: bool = False) -> str:
    scale = 0.76 if maskable else 0.88
    offset = 256 * (1 - scale)
    transform = f"translate({offset:.2f} {offset:.2f}) scale({scale:.4f})"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-labelledby="title">
<title id="title">Dot-matrix pedestrian and RSS app icon</title>
<defs>
  <filter id="green-glow" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="5" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
</defs>
<rect width="512" height="512" fill="{NIGHT}"/>
{geometry_svg(fill=GREEN, glow=True, transform=transform)}
</svg>
'''


def render_svg(svg: str, path: Path, size: int | tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(size, int):
        width = height = size
    else:
        width, height = size
    png = svg2png(bytestring=svg.encode("utf-8"), output_width=width, output_height=height)
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    save_png(image, path)


def write_multi_image_ico(path: Path, png_paths: Sequence[Path]) -> None:
    payloads: list[tuple[int, int, bytes]] = []
    for png_path in png_paths:
        data = png_path.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
        payloads.append((width, height, data))

    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = 6 + (16 * len(payloads))
    entries = bytearray()
    body = bytearray()
    for width, height, data in payloads:
        encoded_width = 0 if width >= 256 else width
        encoded_height = 0 if height >= 256 else height
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_width,
                encoded_height,
                0,
                0,
                1,
                32,
                len(data),
                offset,
            )
        )
        body.extend(data)
        offset += len(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + body)


def copy(path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)


def contact_sheet(
    entries: Sequence[tuple[str, Image.Image]],
    path: Path,
    *,
    cell_width: int = 300,
    cell_height: int = 300,
    columns: int = 3,
    background: str = "#F4F4F2",
) -> None:
    rows = math.ceil(len(entries) / columns)
    header = 42
    sheet = Image.new("RGB", (columns * cell_width, rows * (cell_height + header)), background)
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(entries):
        column = index % columns
        row = index // columns
        x0 = column * cell_width
        y0 = row * (cell_height + header)
        draw.text((x0 + 12, y0 + 12), label, fill="#101010")
        available = (cell_width - 28, cell_height - 28)
        thumbnail = image.copy()
        thumbnail.thumbnail(available, Image.Resampling.LANCZOS)
        x = x0 + (cell_width - thumbnail.width) // 2
        y = y0 + header + (cell_height - thumbnail.height) // 2
        if thumbnail.mode == "RGBA":
            checker = Image.new("RGB", thumbnail.size, "white")
            checker_draw = ImageDraw.Draw(checker)
            block = 16
            for yy in range(0, thumbnail.height, block):
                for xx in range(0, thumbnail.width, block):
                    if ((xx // block) + (yy // block)) % 2:
                        checker_draw.rectangle(
                            (xx, yy, min(xx + block, thumbnail.width), min(yy + block, thumbnail.height)),
                            fill="#D8D8D8",
                        )
            checker.paste(thumbnail, mask=thumbnail.getchannel("A"))
            sheet.paste(checker, (x, y))
        else:
            sheet.paste(thumbnail, (x, y))
    save_png(sheet, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_record(root: Path, path: Path, role: str) -> AssetRecord:
    relative = path.relative_to(root).as_posix()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    image_width: int | None = None
    image_height: int | None = None
    image_format = path.suffix.lstrip(".").upper() or "unknown"
    icc: bool | None = None
    try:
        with Image.open(path) as image:
            image_width, image_height = image.size
            image_format = image.format or image_format
            icc = bool(image.info.get("icc_profile"))
    except Exception:
        if path.suffix.lower() == ".svg":
            image_format = "SVG"
            mime = "image/svg+xml"
            icc = None
    return AssetRecord(
        path=relative,
        role=role,
        format=image_format,
        mime_type=mime,
        width=image_width,
        height=image_height,
        bytes=path.stat().st_size,
        sha256=sha256(path),
        icc_profile_embedded=icc,
    )


def generate(source: Path, root: Path) -> None:
    root = root.resolve()
    source = source.resolve()
    cwd = Path.cwd().resolve()
    if root == cwd or (root / ".git").exists() or (root / "pyproject.toml").exists():
        raise ValueError(
            "Refusing to replace a repository or the current working directory; "
            "use a dedicated build directory such as dist/brand-assets/..."
        )
    if root in source.parents or root == source:
        raise ValueError("Output directory must not contain the archival source file")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    source_dir = root / "assets/brand/source"
    social_dir = root / "assets/brand/social"
    web_dir = root / "assets/brand/web"
    square_dir = root / "assets/brand/square"
    marks_dir = root / "assets/brand/marks"
    favicon_dir = root / "assets/brand/favicon"
    app_dir = root / "assets/brand/app"
    feed_dir = root / "assets/brand/feed"
    site_dir = root / "assets/brand/site"
    qa_dir = root / "assets/brand/qa"
    docs_dir = root / "docs/branding"
    snippets_dir = docs_dir / "snippets"
    tools_dir = root / "tools/branding"

    for directory in (
        source_dir,
        social_dir,
        web_dir,
        square_dir,
        marks_dir,
        favicon_dir,
        app_dir,
        feed_dir,
        site_dir,
        qa_dir,
        snippets_dir,
        tools_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    archival = source_dir / "social-preview-final-original-1536x768.jpeg"
    copy(source, archival)
    source_image = Image.open(source).convert("RGB")
    if source_image.size != (1536, 768):
        raise ValueError(f"Expected 1536×768 source, got {source_image.size}")

    # Social and web derivatives.
    save_jpeg(source_image, social_dir / "github-social-preview-1536x768.jpg")
    social_1280 = resize(source_image, (1280, 640))
    save_jpeg(social_1280, social_dir / "github-social-preview-1280x640.jpg")
    save_png(social_1280, social_dir / "github-social-preview-1280x640.png")
    og = resize(center_crop_aspect(source_image, 1200 / 630), (1200, 630))
    save_jpeg(og, social_dir / "open-graph-1200x630.jpg")
    save_webp(og, social_dir / "open-graph-1200x630.webp")
    if features.check("avif"):
        save_avif(og, social_dir / "open-graph-1200x630.avif")
    save_jpeg(resize(source_image, (640, 320)), social_dir / "compact-640x320.jpg")
    save_jpeg(resize(source_image, (320, 160)), social_dir / "thumbnail-320x160.jpg", quality=90)
    save_webp(source_image, web_dir / "readme-hero-1536x768.webp")
    if features.check("avif"):
        save_avif(source_image, web_dir / "readme-hero-1536x768.avif")
    save_webp(social_1280, web_dir / "readme-hero-1280x640.webp")

    # Intentional wide-art companion crop, not the favicon master.
    companion = source_image.crop((0, 0, 672, 672))
    save_jpeg(companion, square_dir / "companion-square-crop-672x672.jpg")
    save_jpeg(resize(companion, (512, 512)), square_dir / "companion-square-crop-512x512.jpg")
    save_webp(resize(companion, (512, 512)), square_dir / "companion-square-crop-512x512.webp")
    for size in (256, 128, 64):
        save_png(resize(companion, (size, size)), square_dir / f"companion-square-crop-{size}x{size}.png")

    # Purpose-built vector identity.
    canonical = canonical_svg()
    transparent = canonical_svg(transparent=True)
    micro = micro_svg()
    mono = monochrome_svg()
    regular_app = app_svg(maskable=False)
    maskable_app = app_svg(maskable=True)
    (marks_dir / "mark-canonical.svg").write_text(canonical, encoding="utf-8")
    (marks_dir / "mark-transparent.svg").write_text(transparent, encoding="utf-8")
    (marks_dir / "mark-micro.svg").write_text(micro, encoding="utf-8")
    (marks_dir / "mark-monochrome.svg").write_text(mono, encoding="utf-8")
    (marks_dir / "mark-app.svg").write_text(regular_app, encoding="utf-8")
    (marks_dir / "mark-maskable.svg").write_text(maskable_app, encoding="utf-8")
    for size in (1024, 512, 256, 128):
        render_svg(regular_app, marks_dir / f"mark-{size}x{size}.png", size)

    # Split-size favicon system: micro at 16px, canonical at 32px and above.
    (favicon_dir / "favicon.svg").write_text(canonical, encoding="utf-8")
    (favicon_dir / "favicon-micro.svg").write_text(micro, encoding="utf-8")
    render_svg(micro, favicon_dir / "favicon-16x16.png", 16)
    for size in (32, 48, 64, 96):
        render_svg(canonical, favicon_dir / f"favicon-{size}x{size}.png", size)
    write_multi_image_ico(
        favicon_dir / "favicon.ico",
        (
            favicon_dir / "favicon-16x16.png",
            favicon_dir / "favicon-32x32.png",
            favicon_dir / "favicon-48x48.png",
        ),
    )

    # Apple and installable-web-app icons. All are opaque. Maskable artwork is
    # scaled so its meaningful geometry is inside the 40%-radius safe zone.
    (app_dir / "app-icon.svg").write_text(regular_app, encoding="utf-8")
    (app_dir / "app-icon-maskable.svg").write_text(maskable_app, encoding="utf-8")
    for size in (1024, 512, 192):
        render_svg(regular_app, app_dir / f"app-icon-{size}x{size}.png", size)
    render_svg(regular_app, app_dir / "apple-touch-icon.png", 180)
    for size in (512, 192):
        render_svg(maskable_app, app_dir / f"app-icon-maskable-{size}x{size}.png", size)

    manifest = {
        "id": "./",
        "name": "Paul Graham Essay Feeds",
        "short_name": "PG Feeds",
        "description": "Unofficial metadata-only RSS, Atom, and JSON Feed for Paul Graham essays.",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": NIGHT,
        "theme_color": NIGHT,
        "icons": [
            {
                "src": "app-icon-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "app-icon-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "app-icon-maskable-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable",
            },
            {
                "src": "app-icon-maskable-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    (app_dir / "site.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Feed-native graphics.
    render_svg(regular_app, feed_dir / "json-feed-icon-512x512.png", 512)
    render_svg(canonical, feed_dir / "json-feed-favicon-64x64.png", 64)
    render_svg(regular_app, feed_dir / "atom-icon-144x144.png", 144)
    save_png(resize(source_image, (512, 256)), feed_dir / "atom-logo-512x256.png")
    render_svg(regular_app, feed_dir / "rss-channel-image-144x144.png", 144)

    # A directly copyable Pages-static tree. The current Pages assembler should
    # recursively copy this directory to _site/.
    site_map = {
        favicon_dir / "favicon.ico": site_dir / "favicon.ico",
        favicon_dir / "favicon.svg": site_dir / "favicon.svg",
        favicon_dir / "favicon-48x48.png": site_dir / "favicon-48x48.png",
        favicon_dir / "favicon-96x96.png": site_dir / "favicon-96x96.png",
        app_dir / "apple-touch-icon.png": site_dir / "apple-touch-icon.png",
        app_dir / "site.webmanifest": site_dir / "site.webmanifest",
        app_dir / "app-icon-192x192.png": site_dir / "app-icon-192x192.png",
        app_dir / "app-icon-512x512.png": site_dir / "app-icon-512x512.png",
        app_dir / "app-icon-maskable-192x192.png": site_dir / "app-icon-maskable-192x192.png",
        app_dir / "app-icon-maskable-512x512.png": site_dir / "app-icon-maskable-512x512.png",
        feed_dir / "json-feed-icon-512x512.png": site_dir / "feed-icon-512x512.png",
        feed_dir / "json-feed-favicon-64x64.png": site_dir / "feed-favicon-64x64.png",
        feed_dir / "atom-icon-144x144.png": site_dir / "atom-icon-144x144.png",
        feed_dir / "atom-logo-512x256.png": site_dir / "atom-logo-512x256.png",
        feed_dir / "rss-channel-image-144x144.png": site_dir / "rss-channel-image-144x144.png",
        social_dir / "open-graph-1200x630.jpg": site_dir / "open-graph-1200x630.jpg",
    }
    for src, dst in site_map.items():
        copy(src, dst)

    # QA: favicon breakpoint and family previews.
    favicon_entries: list[tuple[str, Image.Image]] = []
    for size in (16, 32, 48, 64, 96):
        favicon_entries.append(
            (
                f"{size}×{size}",
                Image.open(favicon_dir / f"favicon-{size}x{size}.png").convert("RGBA"),
            )
        )
    contact_sheet(
        favicon_entries,
        qa_dir / "favicon-scale-preview.png",
        cell_width=220,
        cell_height=220,
        columns=5,
    )

    family_entries = [
        ("Canonical tile", Image.open(marks_dir / "mark-512x512.png").convert("RGBA")),
        ("Transparent mark", Image.open(io.BytesIO(svg2png(bytestring=transparent.encode(), output_width=512, output_height=512))).convert("RGBA")),
        ("Micro geometry", Image.open(favicon_dir / "favicon-16x16.png").convert("RGBA").resize((512, 512), Image.Resampling.NEAREST)),
        ("App icon", Image.open(app_dir / "app-icon-512x512.png").convert("RGBA")),
        ("Maskable icon", Image.open(app_dir / "app-icon-maskable-512x512.png").convert("RGBA")),
        ("Feed favicon", Image.open(feed_dir / "json-feed-favicon-64x64.png").convert("RGBA").resize((512, 512), Image.Resampling.NEAREST)),
    ]
    contact_sheet(family_entries, qa_dir / "icon-family-preview.png", columns=3)

    maskable = Image.open(app_dir / "app-icon-maskable-512x512.png").convert("RGBA")
    safe = maskable.copy()
    safe_draw = ImageDraw.Draw(safe)
    safe_draw.ellipse((51.2, 51.2, 460.8, 460.8), outline=(255, 255, 255, 235), width=5)
    safe_draw.rectangle((0, 0, 511, 511), outline=(255, 255, 255, 110), width=2)
    save_png(safe, qa_dir / "maskable-safe-zone-preview.png")

    # Existing social/crop QA.
    scale_widths = (600, 300, 150, 96)
    scale_entries = [(f"{width}px wide", resize(source_image, (width, width // 2))) for width in scale_widths]
    contact_sheet(scale_entries, qa_dir / "social-scale-preview.png", cell_width=660, cell_height=360, columns=1)

    aspect_entries = [
        ("2:1 canonical", resize(source_image, (600, 300))),
        ("1.91:1 Open Graph", resize(center_crop_aspect(source_image, 1.91), (600, 314))),
        ("16:9 stress test", resize(center_crop_aspect(source_image, 16 / 9), (600, 338))),
        ("1:1 center crop stress test", resize(center_crop_aspect(source_image, 1.0), (420, 420))),
    ]
    contact_sheet(aspect_entries, qa_dir / "aspect-ratio-preview.png", cell_width=660, cell_height=440, columns=1)

    safe_social = source_image.copy()
    social_draw = ImageDraw.Draw(safe_social)
    social_draw.rectangle((64, 32, 1536 - 64, 768 - 32), outline=(255, 255, 255), width=3)
    social_draw.text((74, 42), "conservative semantic safe area", fill=(255, 255, 255))
    save_jpeg(safe_social, qa_dir / "social-safe-area-preview.jpg", quality=90)

    # Brand tokens.
    tokens = {
        "schema_version": 1,
        "project": PROJECT,
        "colors": {
            "night": NIGHT,
            "night_border": NIGHT_BORDER,
            "emitter_green": GREEN,
            "micro_emitter_green": GREEN_MICRO,
            "warm_paper": CREAM,
        },
        "alt_text": ALT_TEXT,
        "favicon_breakpoint_css_px": 24,
        "favicon_policy": {
            "below_breakpoint": "dedicated micro pedestrian geometry",
            "at_or_above_breakpoint": "canonical pedestrian plus RSS geometry",
        },
    }
    (root / "assets/brand/brand-tokens.json").write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8")

    # Integration snippets.
    (snippets_dir / "html-head.html").write_text(
        f'''<!-- Relative icon URLs work under the repository's GitHub Pages subpath. -->
<link rel="icon" href="favicon.ico" sizes="16x16 32x32 48x48">
<link rel="icon" href="favicon.svg" type="image/svg+xml" sizes="any">
<link rel="icon" href="favicon-48x48.png" type="image/png" sizes="48x48">
<link rel="apple-touch-icon" href="apple-touch-icon.png" sizes="180x180">
<link rel="manifest" href="site.webmanifest">
<meta name="theme-color" content="{NIGHT}">

<meta property="og:type" content="website">
<meta property="og:title" content="Paul Graham Essay Feeds">
<meta property="og:description" content="Unofficial metadata-only RSS, Atom, and JSON Feed for Paul Graham essays.">
<meta property="og:url" content="https://wyattowalsh.github.io/paul-graham-essay-feeds/">
<meta property="og:image" content="https://wyattowalsh.github.io/paul-graham-essay-feeds/open-graph-1200x630.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{ALT_TEXT}">
<meta name="twitter:card" content="summary_large_image">
''',
        encoding="utf-8",
    )
    (snippets_dir / "pages-static-copy.py").write_text(
        '''# Add after the destination directory is created in assemble_pages().
site_assets = root / "assets" / "brand" / "site"
if not site_assets.is_dir():
    raise FeedError(f"Missing Pages brand assets: {site_assets}")
shutil.copytree(site_assets, dest, dirs_exist_ok=True)
''',
        encoding="utf-8",
    )
    (snippets_dir / "pages-tests.py").write_text(
        '''# Add to the Pages assembly test after assemble_pages(repo, dest).
for name in (
    "favicon.ico",
    "favicon.svg",
    "favicon-48x48.png",
    "apple-touch-icon.png",
    "site.webmanifest",
    "app-icon-192x192.png",
    "app-icon-512x512.png",
    "feed-icon-512x512.png",
    "feed-favicon-64x64.png",
    "open-graph-1200x630.jpg",
):
    assert (dest / name).is_file()
''',
        encoding="utf-8",
    )
    (snippets_dir / "json-feed-fields.py").write_text(
        '''# In render_json(), after payload creation and only when public_base_url exists.
asset_base = _directory_base_url(snapshot.public_base_url)
payload["icon"] = urljoin(asset_base, "feed-icon-512x512.png")
payload["favicon"] = urljoin(asset_base, "feed-favicon-64x64.png")
''',
        encoding="utf-8",
    )
    (snippets_dir / "atom-icon-elements.py").write_text(
        '''# In render_atom(), when snapshot.public_base_url is not None.
asset_base = _directory_base_url(snapshot.public_base_url)
ET.SubElement(feed, "icon").text = urljoin(asset_base, "atom-icon-144x144.png")
ET.SubElement(feed, "logo").text = urljoin(asset_base, "atom-logo-512x256.png")
''',
        encoding="utf-8",
    )
    (snippets_dir / "rss-image-element.py").write_text(
        '''# In render_rss(), when snapshot.public_base_url is not None.
asset_base = _directory_base_url(snapshot.public_base_url)
image = ET.SubElement(ch, "image")
ET.SubElement(image, "url").text = urljoin(asset_base, "rss-channel-image-144x144.png")
ET.SubElement(image, "title").text = snapshot.title
ET.SubElement(image, "link").text = SOURCE_URL
ET.SubElement(image, "width").text = "144"
ET.SubElement(image, "height").text = "144"
''',
        encoding="utf-8",
    )
    (snippets_dir / "readme-hero.md").write_text(
        f'''<picture>
  <source srcset="assets/brand/web/readme-hero-1536x768.avif" type="image/avif">
  <source srcset="assets/brand/web/readme-hero-1536x768.webp" type="image/webp">
  <img src="assets/brand/social/github-social-preview-1536x768.jpg"
       alt="{ALT_TEXT}"
       width="1536" height="768">
</picture>
''',
        encoding="utf-8",
    )
    (snippets_dir / "open-graph.html").write_text(
        f'''<meta property="og:image" content="https://wyattowalsh.github.io/paul-graham-essay-feeds/open-graph-1200x630.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{ALT_TEXT}">
<meta name="twitter:card" content="summary_large_image">
''',
        encoding="utf-8",
    )
    (snippets_dir / "css-tokens.css").write_text(
        f''':root {{
  --pg-feeds-night: {NIGHT};
  --pg-feeds-night-border: {NIGHT_BORDER};
  --pg-feeds-emitter: {GREEN};
  --pg-feeds-paper: {CREAM};
}}
''',
        encoding="utf-8",
    )

    # Documentation.
    (root / "README-FIRST.md").write_text(
        '''# Install the brand assets

This is the complete repository bundle, including the social-preview family,
a purpose-built split-size favicon system, installable-web-app icons, and
feed-native graphics.

1. Copy `assets/`, `docs/`, and `tools/` into the repository root.
2. Upload `assets/brand/social/github-social-preview-1536x768.jpg` in GitHub
   repository settings as the social preview.
3. Copy `assets/brand/site/` into the generated `_site/` artifact from
   `assemble_pages()` and add `docs/branding/snippets/html-head.html` to the
   generated `<head>`.
4. Add the JSON Feed, Atom, and RSS image fields from the snippets only after
   the Pages assets are deployed at their stable URLs.

Start with `docs/branding/README.md` and `docs/branding/INTEGRATION.md`.
''',
        encoding="utf-8",
    )

    (docs_dir / "README.md").write_text(
        f'''# Paul Graham Essay Feeds brand assets

A repository-ready visual system derived from the final text-free social
preview, with a separate vector mark engineered for tiny and square contexts.

## Canonical choices

| Use | File |
|---|---|
| GitHub repository social preview | `assets/brand/social/github-social-preview-1536x768.jpg` |
| General Open Graph card | `assets/brand/social/open-graph-1200x630.jpg` |
| Browser favicon | `assets/brand/favicon/favicon.ico` plus `favicon.svg` |
| 16 px favicon source | `assets/brand/marks/mark-micro.svg` |
| Canonical square mark | `assets/brand/marks/mark-canonical.svg` |
| Apple touch icon | `assets/brand/app/apple-touch-icon.png` |
| Web-app icons | `assets/brand/app/app-icon-192x192.png` and `app-icon-512x512.png` |
| Maskable icons | `assets/brand/app/app-icon-maskable-192x192.png` and `app-icon-maskable-512x512.png` |
| JSON Feed icon / favicon | `assets/brand/feed/json-feed-icon-512x512.png` and `json-feed-favicon-64x64.png` |
| Atom icon / logo | `assets/brand/feed/atom-icon-144x144.png` and `atom-logo-512x256.png` |
| RSS channel image | `assets/brand/feed/rss-channel-image-144x144.png` |
| README hero | `assets/brand/web/readme-hero-1536x768.webp` |

## Favicon policy

The favicon is not a downscaled cinematic crop.

- At **16 px**, the bundle uses a dedicated micro pedestrian geometry with no
  RSS arcs. This protects recognition when there are too few pixels to preserve
  both ideas cleanly.
- At **32 px and above**, the canonical vector includes the source-derived
  dot-matrix pedestrian and two-arc RSS construction.
- `favicon.ico` contains distinct 16, 32, and 48 px entries.
- The opaque navy tile works in light and dark browser chrome, so theme-specific
  duplicates are intentionally omitted.

## Deliberate omissions

The bundle does not include obsolete Windows tile metadata, a native-mobile
adaptive-icon project, or a fictitious GitHub repository avatar. Those add
maintenance surface without a current use in this codebase.

Suggested alt text:

> {ALT_TEXT}

See `INTEGRATION.md`, `ASSET_PROVENANCE.md`, `manifest.json`, and the QA images
under `assets/brand/qa/`.
''',
        encoding="utf-8",
    )

    (docs_dir / "INTEGRATION.md").write_text(
        '''# Integration into the current repository

The current GitHub Pages product is assembled by
`src/paul_graham_essay_feeds/pages.py`. It writes the feed files, `/latest/`,
`index.html`, and `.nojekyll`, but it does not yet copy static brand assets.

## 1. Keep the source assets in the repository

Copy the bundle's `assets/brand/` directory to the same path in the repository.
The directly deployable static tree is `assets/brand/site/`.

## 2. Copy static assets into `_site/`

Insert the block in `snippets/pages-static-copy.py` after the destination
folder is created in `assemble_pages()`. It recursively overlays the contents
of `assets/brand/site/` into `_site/`.

Failing closed is recommended here: if the icon URLs are emitted into HTML or
feeds but the files are missing, feed readers and browsers receive broken
metadata.

## 3. Extend the generated `<head>`

Insert `snippets/html-head.html` into `index_html()`. The icon and manifest URLs
are relative because the site is hosted below the repository's GitHub Pages
subpath. Open Graph image URLs are absolute.

## 4. Lock the Pages contract

Add the assertions in `snippets/pages-tests.py` to the Pages assembly test.
Also assert that the generated HTML contains `rel="icon"`,
`rel="apple-touch-icon"`, `rel="manifest"`, and `og:image`.

## 5. Add feed-native graphics after deployment

The highest-value repository-specific addition is feed metadata:

- JSON Feed: top-level `icon` and `favicon`
- Atom: feed-level `icon` and `logo`
- RSS: channel-level `image`

Use the three snippets in this directory. Emit the fields only when
`public_base_url` exists so local/offline generation does not invent public
URLs. The `/latest/` slicer preserves the feed header fields automatically.

## 6. Regenerate deterministically

From the repository root:

```bash
uv run --with pillow --with cairosvg \\
  python tools/branding/render_assets.py \\
  --source assets/brand/source/social-preview-final-original-1536x768.jpeg \\
  --out dist/brand-assets/paul-graham-essay-feeds-brand-assets-complete
```

The script preserves the archival source, rebuilds derivatives, refreshes the
manifest, and rewrites checksums.
''',
        encoding="utf-8",
    )

    (docs_dir / "ASSET_PROVENANCE.md").write_text(
        f'''# Asset provenance

## Source

- Project: `{PROJECT}`
- Source file: `assets/brand/source/social-preview-final-original-1536x768.jpeg`
- Source dimensions: 1536×768
- Source SHA-256: `{sha256(archival)}`
- Assumed source color space: sRGB

The archival source is preserved byte-for-byte. It is not the preferred web
asset because the supplied file lacks an embedded ICC profile and retains
source metadata.

## Derivative policy

The cinematic derivatives use deterministic crop, resize, format conversion,
metadata stripping, and explicit sRGB tagging.

The square identity is purpose-built vector artwork. Its pedestrian emitter
centroids were reconstructed from the final signal glyph and normalized into a
512×512 geometry; the RSS origin and two arcs were redrawn in the same
emitter system. The 16 px micro mark is a separately tuned geometry rather than
a blind downscale.

No third-party font files, logos, or official Paul Graham brand assets are
included. The artwork is unofficial and does not imply affiliation or
endorsement.
''',
        encoding="utf-8",
    )

    # Copy this executable source into the bundle after every other output is
    # generated. The caller supplies the path through __file__.
    copy(Path(__file__), tools_dir / "render_assets.py")

    # Manifest and checksums are last so they cover the complete bundle except
    # themselves. Roles are intentionally explicit for production assets.
    roles: dict[str, str] = {
        "assets/brand/social/github-social-preview-1536x768.jpg": "Canonical GitHub social-preview upload",
        "assets/brand/social/github-social-preview-1280x640.jpg": "GitHub recommended display-size fallback",
        "assets/brand/social/open-graph-1200x630.jpg": "General Open Graph card",
        "assets/brand/favicon/favicon.ico": "Multi-entry browser favicon: 16, 32, and 48 px",
        "assets/brand/favicon/favicon.svg": "Scalable canonical browser favicon",
        "assets/brand/favicon/favicon-16x16.png": "Dedicated micro favicon raster",
        "assets/brand/app/apple-touch-icon.png": "Apple touch icon",
        "assets/brand/app/app-icon-512x512.png": "Installable web-app icon",
        "assets/brand/app/app-icon-maskable-512x512.png": "Maskable web-app icon",
        "assets/brand/feed/json-feed-icon-512x512.png": "JSON Feed large timeline icon",
        "assets/brand/feed/json-feed-favicon-64x64.png": "JSON Feed small source-list icon",
        "assets/brand/feed/atom-icon-144x144.png": "Atom small square icon",
        "assets/brand/feed/atom-logo-512x256.png": "Atom 2:1 logo",
        "assets/brand/feed/rss-channel-image-144x144.png": "RSS channel image",
        "assets/brand/marks/mark-canonical.svg": "Canonical source-derived pedestrian plus RSS vector mark",
        "assets/brand/marks/mark-micro.svg": "Dedicated sub-24-pixel pedestrian vector geometry",
        "assets/brand/marks/mark-monochrome.svg": "CurrentColor monochrome vector mark",
    }
    records: list[AssetRecord] = []
    for path in sorted((root / "assets").rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            records.append(asset_record(root, path, roles.get(relative, "Brand asset")))

    manifest_data = {
        "schema_version": 2,
        "project": PROJECT,
        "generated_on": date.today().isoformat(),
        "source_assumption": "The untagged source pixels are treated as sRGB for web delivery.",
        "rendering": {
            "pillow_version": Image.__version__,
            "cairosvg": True,
            "webp_supported": features.check("webp"),
            "avif_supported": features.check("avif"),
        },
        "recommended": {
            "github_upload": "assets/brand/social/github-social-preview-1536x768.jpg",
            "open_graph": "assets/brand/social/open-graph-1200x630.jpg",
            "favicon_ico": "assets/brand/favicon/favicon.ico",
            "favicon_svg": "assets/brand/favicon/favicon.svg",
            "apple_touch_icon": "assets/brand/app/apple-touch-icon.png",
            "web_app_icon": "assets/brand/app/app-icon-512x512.png",
            "maskable_icon": "assets/brand/app/app-icon-maskable-512x512.png",
            "json_feed_icon": "assets/brand/feed/json-feed-icon-512x512.png",
            "json_feed_favicon": "assets/brand/feed/json-feed-favicon-64x64.png",
            "atom_icon": "assets/brand/feed/atom-icon-144x144.png",
            "atom_logo": "assets/brand/feed/atom-logo-512x256.png",
            "rss_image": "assets/brand/feed/rss-channel-image-144x144.png",
        },
        "assets": [record.__dict__ for record in records],
    }
    (docs_dir / "manifest.json").write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")

    checksum_targets = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != docs_dir / "SHA256SUMS"
    ]
    checksum_lines = [f"{sha256(path)}  {path.relative_to(root).as_posix()}" for path in checksum_targets]
    (docs_dir / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the complete Paul Graham Essay Feeds brand bundle")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("assets/brand/source/social-preview-final-original-1536x768.jpeg"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("dist/brand-assets/paul-graham-essay-feeds-brand-assets-complete"),
    )
    args = parser.parse_args()
    generate(args.source.resolve(), args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
