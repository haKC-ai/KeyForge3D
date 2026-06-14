#!/usr/bin/env python3
"""
cli.py — KeyForge3D command-line interface

Usage:
    python cli.py image.jpg
    python cli.py image.jpg --format KW1 --output ./results
    python cli.py image.jpg --json
    python cli.py *.jpg --output ./batch_results
"""
import argparse
import json
import sys
from pathlib import Path
from core import analyze_image


def print_result(r, as_json: bool):
    if as_json:
        print(r.to_json())
        return

    ok = r.error is None
    status = "OK" if ok else "FAIL"
    print(f"\n[{status}] {Path(r.image_path).name}")
    print(f"  Format      : {r.key_format} ({r.brand})")
    print(f"  Bitting     : {r.bitting_code or '—'}")
    print(f"  Cuts        : {r.num_cuts}  |  Increment: {r.depth_increment_mm} mm")
    print(f"  STL         : {r.stl_path or '—'}")
    print(f"  Timestamp   : {r.timestamp}")
    if r.gps:
        print(f"  GPS         : {r.gps['lat']}, {r.gps['lon']}"
              + (f"  alt {r.gps['alt_m']}m" if "alt_m" in r.gps else ""))
    if r.exif:
        interesting = ["Make", "Model", "DateTime", "Software", "LensModel"]
        exif_lines = {k: r.exif[k] for k in interesting if k in r.exif}
        if exif_lines:
            print("  EXIF        :", json.dumps(exif_lines))
    if r.vision_notes:
        print(f"  AI notes    : {r.vision_notes}")
    if r.error:
        print(f"  Error       : {r.error}")


def main():
    parser = argparse.ArgumentParser(
        prog="keyforge3d",
        description="Extract bitting code and generate STL from key photograph",
    )
    parser.add_argument("images", nargs="+", help="Image file(s) to analyze")
    parser.add_argument("--format", "-f", help="Key format (e.g. KW1, SC1). Skips AI detection.")
    parser.add_argument("--output", "-o", default=".", help="Output directory for STL files")
    parser.add_argument("--json", "-j", action="store_true", dest="as_json",
                        help="Output results as JSON (one object per line for batch)")
    args = parser.parse_args()

    results = []
    for img_path in args.images:
        p = Path(img_path)
        if not p.exists():
            print(f"[SKIP] not found: {img_path}", file=sys.stderr)
            continue
        r = analyze_image(str(p), key_format=args.format, output_dir=args.output)
        results.append(r)
        print_result(r, args.as_json)

    errors = [r for r in results if r.error]
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
