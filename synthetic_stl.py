"""
synthetic_stl.py

Generates a CANONICAL key STL from (format, bitting_code) using spec parameters.
Does NOT touch the source photo. Photo contour extraction was unreliable on
random in-the-wild photos; this produces consistent printable geometry.

Geometry layout (top-down, key lying flat):
    +-------+ +-------- blade ----------+
    | bow   | | shoulder                |  ← top edge (no cuts)
    |       |+|     ↓ cuts cut into bottom
    +-------+ +-------------------------+
    (handle)   (blade narrows from shoulder to tip)
"""
import math
from pathlib import Path

import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

from key_specs import get_spec
from stl_sanitizer import sanitize as sanitize_stl


def build_key_polygon(fmt: str, bitting: list[int]) -> Polygon:
    """
    Build top-down 2D outline of a key with the given bitting pattern.
    Origin at tip; +x toward bow; +y is the blade's top edge (no cuts).
    """
    spec = get_spec(fmt)
    n = spec["num_cuts"]
    bw = spec["blade_width_mm"]           # blade height at shoulder
    cs = spec["cut_spacing_mm"]
    tip2first = spec["tip_to_first_cut_mm"]
    di = spec["depth_increment_mm"]

    # Geometry constants
    blade_len = tip2first + cs * (n - 1) + 5  # blade extends past last cut
    shoulder_x = blade_len                      # where blade meets bow
    bow_w = bw * 2.4                            # bow handle width
    bow_h = bw * 2.0                            # bow handle height
    bow_x = shoulder_x + bow_w

    # Pad bitting to num_cuts
    bits = list(bitting) + [min(spec["depths"])] * max(0, n - len(bitting))
    bits = bits[:n]

    # Build the bottom edge of the blade with cuts.
    # Top edge of the blade is flat at y = bw. Bottom edge has cuts at
    # x = tip2first + i*cs, each cut depth = bits[i] * di (into the blade from y=0).
    # Cut profile uses trapezoidal V-cut.
    points_top = []
    points_bot = []

    # Tip (left side) - taper from 0 to bw over first 2mm
    tip_taper = min(2.0, tip2first / 2)
    points_top.append((0.0, bw * 0.5))
    points_top.append((tip_taper, bw))
    points_bot.append((0.0, bw * 0.5))
    points_bot.append((tip_taper, 0.0))

    # Blade body
    points_top.append((shoulder_x, bw))

    # Bottom edge with cuts
    cut_half_w = cs * 0.35
    for i, depth_val in enumerate(bits):
        cx = tip2first + i * cs
        depth_mm = depth_val * di
        # Approach edge
        points_bot.append((cx - cut_half_w, 0.0))
        # Down to cut bottom
        points_bot.append((cx - cut_half_w * 0.3, depth_mm))
        points_bot.append((cx + cut_half_w * 0.3, depth_mm))
        # Back up
        points_bot.append((cx + cut_half_w, 0.0))

    points_bot.append((shoulder_x, 0.0))

    # Bow (right side - the handle)
    points_top.append((shoulder_x, bw + (bow_h - bw) * 0.5))
    points_top.append((shoulder_x + bow_w * 0.15, bow_h))
    points_top.append((bow_x - bow_w * 0.15, bow_h))
    points_top.append((bow_x, bow_h * 0.65))
    points_top.append((bow_x, bow_h * 0.35))
    points_top.append((bow_x - bow_w * 0.15, 0.0))
    points_top.append((shoulder_x + bow_w * 0.15, 0.0))
    # close to shoulder bottom
    points_top.append((shoulder_x, -(bow_h - bw) * 0.5))

    # Combine top + reversed bottom for outline
    outline = points_top + list(reversed(points_bot))
    poly = Polygon(outline)

    # Punch a hole in the bow for the keyring
    if poly.is_valid:
        keyring_cx = shoulder_x + bow_w * 0.65
        keyring_cy = bow_h * 0.5
        ring_r = min(bow_h, bow_w) * 0.18
        ring = Polygon([
            (keyring_cx + ring_r * math.cos(a),
             keyring_cy + ring_r * math.sin(a))
            for a in [i * math.pi / 12 for i in range(24)]
        ])
        try:
            poly = poly.difference(ring)
        except Exception:
            pass

    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def generate_synthetic_stl(
    fmt: str,
    bitting: list[int],
    output_path: str,
) -> str:
    """
    Build a clean, canonical STL of a key in the given format with the given bitting.
    Returns the output path.
    """
    spec = get_spec(fmt)
    thickness = spec["key_thickness_mm"]

    poly = build_key_polygon(fmt, bitting)

    # Extrude
    mesh = trimesh.creation.extrude_polygon(poly, height=thickness)

    # Center the mesh at origin
    mesh.apply_translation(-mesh.centroid)

    # Export + sanitize
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    mesh.export(output_path)
    sanitize_stl(output_path)
    return output_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <format> <bitting> <output.stl>")
        print(f"Example: {sys.argv[0]} KW1 '4 5 1 3 6' key.stl")
        sys.exit(1)
    fmt = sys.argv[1]
    bitting = [int(x) for x in sys.argv[2].split()]
    out = generate_synthetic_stl(fmt, bitting, sys.argv[3])
    print(f"[+] {out}")
