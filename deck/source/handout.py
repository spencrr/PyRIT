"""Assemble dist/handout.pdf (one page per tour step) from the exported PNGs."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=HERE / "dist" / "png-tour")
    parser.add_argument("--out", type=Path, default=HERE / "dist" / "handout.pdf")
    parser.add_argument("--width", type=int, default=1600, help="downscale width to keep the PDF small")
    args = parser.parse_args()
    frames = sorted(args.source.glob("*.png"))
    if not frames:
        raise SystemExit(f"no PNGs in {args.source}")
    pages = []
    for frame in frames:
        image = Image.open(frame).convert("RGB")
        ratio = args.width / image.width
        pages.append(image.resize((args.width, round(image.height * ratio)), Image.LANCZOS))
    pages[0].save(args.out, save_all=True, append_images=pages[1:], resolution=120, quality=85)
    print(f"wrote {args.out} ({len(pages)} pages, {args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
