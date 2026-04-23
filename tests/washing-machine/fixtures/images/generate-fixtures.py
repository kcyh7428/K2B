#!/usr/bin/env python3
"""Generate the image fixtures used by washing-machine OCR tests.

Reproducible source: re-run this script to regenerate the fixtures if they
get corrupted, deleted, or if we decide to tune (larger, different font,
different Chinese characters, etc.).

**Idempotent by default.** If a target file already exists, the generator
skips it and prints "skip (exists)". This prevents PIL render-determinism
drift (font hinting, PNG metadata) from dirtying the git working tree on
every invocation. Pass --force to overwrite.

Output:
- test-128.png          -- 128x128 "TEST" image for preflight VLM smoke
- dr-lo-card.png        -- synthetic business card matching Dr. Lo Hak Keung's
                           public details from wiki/people/person_Dr-Lo-Hak-Keung.md.
                           Used by Chinese-OCR accuracy gate in classify.test.sh.
- invalid.gif           -- 1x1 GIF to test extract-attachment's format-rejection
                           path (VLM supports JPEG/PNG/WebP only)
- corrupted.png         -- truncated PNG (header only, no IDAT). Tests the error
                           path where VLM or image decoder fails cleanly.
- synthetic-*.png       -- 4 additional card fixtures for the Ship 1B OCR
                           accuracy gate corpus (see ocr-expected.json).

Run:
    python3 generate-fixtures.py           # idempotent: skip existing files
    python3 generate-fixtures.py --force   # regenerate everything

Requires: Pillow (PIL). Available on Mac Mini via the washing-machine venv
(sentence-transformers depends on torch which depends on PIL). On MacBook,
install via `python3 -m pip install --user Pillow` if missing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FORCE = "--force" in sys.argv[1:]


def skip_if_exists(path: Path) -> bool:
    if path.exists() and not FORCE:
        print(f"skip (exists): {path}")
        return True
    return False


_FONT_FALLBACK_WARNED = False


def find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try to use a TTF that supports CJK. Fall back to default if none found.

    Emits a one-shot warning to stderr when the default bitmap font is used;
    it does not render CJK characters. Fixtures generated on such a box will
    have tofu/missing Chinese text, which makes the OCR accuracy gate fail
    silently in ways that are hard to debug.
    """
    global _FONT_FALLBACK_WARNED
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",                # macOS, native CJK
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    if not _FONT_FALLBACK_WARNED:
        print(
            "WARNING: no CJK-capable system font found. Chinese text in "
            "generated fixtures will render as tofu/missing. Install "
            "PingFang (macOS) or NotoSansCJK (Linux) before regenerating.",
            file=sys.stderr,
        )
        _FONT_FALLBACK_WARNED = True
    return ImageFont.load_default()


def make_test_128() -> None:
    """Simple 128x128 PNG with 'TEST' text. Used by preflight VLM smoke."""
    target = HERE / "test-128.png"
    if skip_if_exists(target):
        return
    img = Image.new("RGB", (128, 128), "white")
    draw = ImageDraw.Draw(img)
    font = find_font(40)
    bbox = draw.textbbox((0, 0), "TEST", font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text(((128 - w) // 2, (128 - h) // 2 - bbox[1]), "TEST", fill="black", font=font)
    img.save(target, "PNG")
    print(f"wrote {target}")


def make_dr_lo_card() -> None:
    """Synthetic business card matching Dr. Lo Hak Keung's public contact info.

    Source: wiki/people/person_Dr-Lo-Hak-Keung.md (public business card info
    Keith received via Telegram 2026-04-01). All fields are what appear on
    an actual medical business card -- nothing private or PII beyond what
    Dr. Lo prints on his card.

    This fixture drives the Chinese-OCR accuracy gate in Commit 3 tests:
    extract-attachment.test.sh must achieve >= 80% field match (name,
    phone, whatsapp, specialty, hospital) or Ship 1 falls back to
    Opus vision.
    """
    target = HERE / "dr-lo-card.png"
    if skip_if_exists(target):
        return
    W, H = 600, 360
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    title = find_font(28)
    subtitle = find_font(20)
    body = find_font(18)
    small = find_font(16)

    x_pad = 40
    y = 40
    draw.text((x_pad, y), "St. Paul's Hospital", fill="#0a3d62", font=title)
    y += 36
    draw.text((x_pad, y), "聖保祿醫院", fill="#0a3d62", font=subtitle)
    y += 40
    draw.line([(x_pad, y), (W - x_pad, y)], fill="#0a3d62", width=2)
    y += 20

    draw.text((x_pad, y), "Dr. Lo Hak Keung", fill="black", font=title)
    y += 36
    draw.text((x_pad, y), "羅克強醫生", fill="black", font=subtitle)
    y += 30

    draw.text((x_pad, y), "Specialist in Urology / 泌尿外科專科醫生", fill="#333", font=body)
    y += 26
    draw.text((x_pad, y), "Head, Urology Centre / 泌尿中心主管", fill="#333", font=body)
    y += 34

    draw.text((x_pad, y), "Tel: 2830 3709", fill="black", font=body)
    y += 24
    draw.text((x_pad, y), "WhatsApp: 9861 9017", fill="black", font=body)
    y += 24
    draw.text((x_pad, y), "2 Eastern Hospital Road, Causeway Bay, HK", fill="black", font=small)

    img.save(target, "PNG")
    print(f"wrote {target}")


def make_invalid_gif() -> None:
    """Minimal 1x1 GIF. VLM should reject this (supports JPEG/PNG/WebP only)."""
    target = HERE / "invalid.gif"
    if skip_if_exists(target):
        return
    img = Image.new("RGB", (1, 1), "white")
    img.save(target, "GIF")
    print(f"wrote {target}")


def make_corrupted_png() -> None:
    """Truncated PNG file -- valid PNG header + IHDR, no IDAT or IEND chunks.

    Tests the error path in extract-attachment.sh where the VLM or image
    decoder fails cleanly (not a silent 0-byte read or an exception-crash).
    """
    target = HERE / "corrupted.png"
    if skip_if_exists(target):
        return
    # PNG signature (8 bytes) + IHDR chunk (25 bytes including CRC) only.
    # Real PNG would continue with IDAT + IEND.
    png_sig = b"\x89PNG\r\n\x1a\n"
    # IHDR for a 1x1 RGB image
    ihdr_length = b"\x00\x00\x00\x0d"
    ihdr_type = b"IHDR"
    ihdr_data = b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    ihdr_crc = b"\x90wS\xde"
    target.write_bytes(png_sig + ihdr_length + ihdr_type + ihdr_data + ihdr_crc)
    print(f"wrote {target} (truncated at IHDR on purpose)")


SYNTHETIC_CARDS: dict[str, dict[str, str]] = {
    "synthetic-andrew.png": {
        "name": "Andrew Shwetzer",
        "phone": "9876 5432",
        "email": "andrew@talentsignals.co",
        "organization": "TalentSignals",
    },
    "synthetic-mei-ling.png": {
        "name": "Chen Mei Ling",
        "name_zh": "陳美玲",
        "phone": "2811 2233",
        "role": "Architect",
        "organization": "MLA Design",
    },
    "synthetic-physio.png": {
        "name": "Dr. Chan Wai Ming",
        "name_zh": "陳偉明醫生",
        "phone": "2567 1234",
        "role": "Physiotherapist",
        "organization": "Central Wellness",
    },
    "synthetic-minimal.png": {
        "name": "James Lau",
        "phone": "6123 4567",
    },
}


def make_synthetic_cards() -> None:
    """Render the 4 synthetic cards used by the Ship 1B OCR accuracy gate.

    These add corpus variety beyond the single Dr. Lo fixture: English-only,
    English+Chinese mixed, minimal-fields. The gate script computes per-image
    field-match accuracy against ocr-expected.json (same directory).
    """
    W, H = 600, 360
    title = find_font(28)
    body = find_font(20)
    for filename, fields in SYNTHETIC_CARDS.items():
        target = HERE / filename
        if skip_if_exists(target):
            continue
        img = Image.new("RGB", (W, H), "white")
        draw = ImageDraw.Draw(img)
        x_pad, y = 40, 40
        for key, value in fields.items():
            font = title if key == "name" else body
            label = key.replace("_", " ").title()
            draw.text((x_pad, y), f"{label}: {value}", fill="black", font=font)
            y += 40
        img.save(target, "PNG")
        print(f"wrote {target}")


def main() -> None:
    make_test_128()
    make_dr_lo_card()
    make_invalid_gif()
    make_corrupted_png()
    make_synthetic_cards()
    print("\nAll fixtures generated. See ../calibration-corpus.md for usage context.")


if __name__ == "__main__":
    main()
