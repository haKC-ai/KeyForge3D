"""
core.py

Headless key analysis pipeline — no GUI dependency.
Used by CLI, API, and web interfaces.
"""
import base64
import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict

import cv2
import numpy as np
import trimesh
from shapely.geometry import Polygon
from openai import OpenAI
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import exifread

from key_specs import get_spec, KNOWN_FORMATS
from stl_sanitizer import sanitize as sanitize_stl

# Anchor to repo directory regardless of cwd
_BASE = Path(__file__).parent.resolve()
LOG_FILE = str(_BASE / "key_log.csv")
LOG_FIELDS = [
    "timestamp", "image_path", "key_format", "brand",
    "num_cuts", "bitting_code", "depth_increment_mm",
    "stl_output", "notes",
]

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


# ── result type ─────────────────────────────────────────────────────────────

@dataclass
class KeyAnalysisResult:
    timestamp: str
    image_path: str
    key_format: str
    brand: str
    num_cuts: int
    bitting_code: str          # e.g. "4 5 1 3 6"
    bitting_list: list[int]
    depth_increment_mm: float
    cut_spacing_mm: float
    tip_to_first_cut_mm: float
    stl_path: str
    exif: dict
    gps: dict | None
    vision_notes: str
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ── EXIF extraction ─────────────────────────────────────────────────────────

def _rational_to_float(val) -> float:
    if isinstance(val, tuple):
        return val[0] / val[1] if val[1] else 0.0
    return float(val)


def _dms_to_decimal(dms, ref: str) -> float | None:
    try:
        d = _rational_to_float(dms[0])
        m = _rational_to_float(dms[1])
        s = _rational_to_float(dms[2])
        dec = d + m / 60 + s / 3600
        return -dec if ref in ("S", "W") else dec
    except Exception:
        return None


def extract_exif(image_path: str) -> tuple[dict, dict | None]:
    """Returns (flat_exif_dict, gps_dict_or_None)."""
    result: dict = {}
    gps: dict | None = None

    with open(image_path, "rb") as f:
        data = f.read()

    # Pillow
    try:
        img = Image.open(io.BytesIO(data))
        raw = img._getexif() or {}
        for tag_id, val in raw.items():
            name = TAGS.get(tag_id, str(tag_id))
            try:
                result[str(name)] = str(val)
            except Exception:
                pass
        gps_raw = raw.get(34853)
        if gps_raw:
            g = {GPSTAGS.get(k, k): v for k, v in gps_raw.items()}
            lat = _dms_to_decimal(g.get("GPSLatitude"), g.get("GPSLatitudeRef", "N"))
            lon = _dms_to_decimal(g.get("GPSLongitude"), g.get("GPSLongitudeRef", "E"))
            if lat is not None and lon is not None:
                gps = {"lat": round(lat, 7), "lon": round(lon, 7)}
                alt = g.get("GPSAltitude")
                if alt is not None:
                    gps["alt_m"] = round(_rational_to_float(alt), 2)
    except Exception:
        pass

    # exifread — broader format support
    try:
        tags = exifread.process_file(io.BytesIO(data), details=True)
        for key, val in tags.items():
            if str(key) not in result:
                result[str(key)] = str(val)
    except Exception:
        pass

    return result, gps


# ── image normalization ─────────────────────────────────────────────────────

def normalize_key_image(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    eq = clahe.apply(gray)
    return cv2.fastNlMeansDenoising(eq, h=10)


def extract_key_mask(gray_norm: np.ndarray) -> np.ndarray:
    thresh = cv2.adaptiveThreshold(
        gray_norm, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        blockSize=25, C=8,
    )
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    return cv2.morphologyEx(
        cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, k, iterations=2),
        cv2.MORPH_OPEN, k, iterations=1,
    )


# ── AI format detection ─────────────────────────────────────────────────────

def detect_key_format(image_path: str) -> tuple[str, str, str]:
    """Returns (format_code, brand, vision_notes)."""
    known = ", ".join(f for f in KNOWN_FORMATS if f != "UNKNOWN")
    prompt = (
        f"Identify the key format in this image. Known formats: {known}. "
        "Examine bow shape, tip shape, blade profile, and cut count. "
        'Return JSON only: {"format":"KW1","brand":"Kwikset","confidence":"high","notes":"..."}'
    )
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = Path(image_path).suffix.lower().lstrip(".")
        mime = "jpeg" if ext in ("jpg", "jpeg") else ext
        resp = _get_client().chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/{mime};base64,{b64}",
                    "detail": "high",
                }},
            ]}],
            max_tokens=200,
            temperature=0.0,
        )
        r = json.loads(resp.choices[0].message.content)
        return (
            r.get("format", "UNKNOWN").upper(),
            r.get("brand", "Unknown"),
            r.get("notes", ""),
        )
    except Exception:
        return "UNKNOWN", "Unknown", "AI detection unavailable"


# ── bitting analysis ────────────────────────────────────────────────────────

def analyze_bitting(key_region: np.ndarray, spec: dict) -> list[int]:
    h, w = key_region.shape
    num_cuts = spec["num_cuts"]
    depths = spec["depths"]
    depth_increment = spec["depth_increment_mm"]
    px_per_mm = h / spec["blade_width_mm"]

    blade = key_region[h // 2:, :]
    bh, bw = blade.shape
    seg_w = bw // num_cuts

    bitting = []
    for i in range(num_cuts):
        seg = blade[:, i * seg_w:(i + 1) * seg_w]
        col_profiles = np.mean(seg, axis=1)
        cut_px = int(np.argmax(col_profiles < 128)) if np.any(col_profiles < 128) else 0
        cut_mm = cut_px / px_per_mm
        depth_val = round(cut_mm / depth_increment)
        depth_val = max(min(depths), min(max(depths), depth_val))
        if depth_val not in depths:
            depth_val = min(depths, key=lambda d: abs(d - depth_val))
        bitting.append(depth_val)

    return bitting


# ── STL generation ──────────────────────────────────────────────────────────

def generate_stl(key_contour: np.ndarray, bitting: list[int],
                 spec: dict, h: int, w: int, output_path: str) -> str:
    scale = spec["blade_width_mm"] / h
    points = key_contour.reshape(-1, 2) * scale
    key_mesh = trimesh.creation.extrude_polygon(Polygon(points), height=spec["key_thickness_mm"])

    seg_w_mm = (w * scale) / spec["num_cuts"]
    for i, depth_val in enumerate(bitting):
        cut_depth_mm = depth_val * spec["depth_increment_mm"]
        cut_x = i * seg_w_mm + seg_w_mm / 2
        cut_box = trimesh.creation.box(
            extents=[seg_w_mm, cut_depth_mm, spec["key_thickness_mm"] + 1],
            transform=trimesh.transformations.translation_matrix([cut_x, 0, 0]),
        )
        key_mesh = key_mesh.difference(cut_box)

    key_mesh.export(output_path)
    sanitize_stl(output_path)
    return output_path


# ── main pipeline ───────────────────────────────────────────────────────────

def analyze_image(
    image_path: str,
    key_format: str | None = None,
    output_dir: str = ".",
) -> KeyAnalysisResult:
    """
    Full pipeline: normalize → detect format → bitting → STL → EXIF.
    Pass key_format to skip AI detection.
    """
    ts = datetime.now(timezone.utc).isoformat()
    exif, gps = extract_exif(image_path)

    # Format detection
    vision_notes = ""
    if key_format:
        fmt = key_format.upper()
        spec = get_spec(fmt)
        brand = spec["brand"]
    else:
        fmt, brand, vision_notes = detect_key_format(image_path)
        spec = get_spec(fmt)
        brand = brand or spec["brand"]

    # Image processing
    image = cv2.imread(image_path)
    if image is None:
        return KeyAnalysisResult(
            timestamp=ts, image_path=image_path, key_format=fmt, brand=brand,
            num_cuts=0, bitting_code="", bitting_list=[], depth_increment_mm=0,
            cut_spacing_mm=0, tip_to_first_cut_mm=0,
            stl_path="", exif=exif, gps=gps, vision_notes=vision_notes,
            error="Could not load image",
        )

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    norm = normalize_key_image(gray)
    mask = extract_key_mask(norm)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    key_contour = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True):
        x, y, w, h = cv2.boundingRect(c)
        if w / float(h) > 2 and w > 80:
            key_contour = c
            break

    if key_contour is None:
        return KeyAnalysisResult(
            timestamp=ts, image_path=image_path, key_format=fmt, brand=brand,
            num_cuts=spec["num_cuts"], bitting_code="", bitting_list=[],
            depth_increment_mm=spec["depth_increment_mm"],
            cut_spacing_mm=spec["cut_spacing_mm"],
            tip_to_first_cut_mm=spec["tip_to_first_cut_mm"],
            stl_path="", exif=exif, gps=gps, vision_notes=vision_notes,
            error="Key not detected — ensure horizontal key on contrasting background",
        )

    x, y, w, h = cv2.boundingRect(key_contour)
    key_region = norm[y:y + h, x:x + w]
    bitting = analyze_bitting(key_region, spec)
    bitting_str = " ".join(str(b) for b in bitting)

    stem = Path(image_path).stem
    # Sanitize fmt: only allow known format codes in filename (already validated by caller,
    # but defend in depth — strip anything that isn't alphanumeric)
    safe_fmt = "".join(c for c in fmt if c.isalnum())
    stl_name = f"{stem}_{safe_fmt}_{bitting_str.replace(' ', '')}.stl"
    output_dir_resolved = Path(output_dir).resolve()
    output_dir_resolved.mkdir(parents=True, exist_ok=True)
    stl_path = str(output_dir_resolved / stl_name)

    # Containment check: ensure resolved stl_path stays inside output_dir
    try:
        Path(stl_path).resolve().relative_to(output_dir_resolved)
    except ValueError:
        return KeyAnalysisResult(
            timestamp=ts, image_path=image_path, key_format=fmt, brand=brand,
            num_cuts=spec["num_cuts"], bitting_code=bitting_str, bitting_list=bitting,
            depth_increment_mm=spec["depth_increment_mm"],
            cut_spacing_mm=spec["cut_spacing_mm"],
            tip_to_first_cut_mm=spec["tip_to_first_cut_mm"],
            stl_path="", exif=exif, gps=gps, vision_notes=vision_notes,
            error="Output path containment check failed",
        )

    try:
        generate_stl(key_contour, bitting, spec, h, w, stl_path)
    except Exception as e:
        stl_path = f"STL generation failed: {e}"

    result = KeyAnalysisResult(
        timestamp=ts,
        image_path=str(Path(image_path).resolve()),
        key_format=fmt,
        brand=brand,
        num_cuts=spec["num_cuts"],
        bitting_code=bitting_str,
        bitting_list=bitting,
        depth_increment_mm=spec["depth_increment_mm"],
        cut_spacing_mm=spec["cut_spacing_mm"],
        tip_to_first_cut_mm=spec["tip_to_first_cut_mm"],
        stl_path=stl_path,
        exif=exif,
        gps=gps,
        vision_notes=vision_notes,
    )

    _log_result(result)
    return result


def _log_result(r: KeyAnalysisResult):
    write_header = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow({
            "timestamp": r.timestamp,
            "image_path": r.image_path,
            "key_format": r.key_format,
            "brand": r.brand,
            "num_cuts": r.num_cuts,
            "bitting_code": r.bitting_code,
            "depth_increment_mm": r.depth_increment_mm,
            "stl_output": r.stl_path,
            "notes": r.vision_notes,
        })
