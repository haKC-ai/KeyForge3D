import cv2
import numpy as np
import trimesh
import base64
import json
import csv
import os
from datetime import datetime, timezone
from shapely.geometry import Polygon
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
from openai import OpenAI
from key_specs import get_spec, KNOWN_FORMATS
from stl_sanitizer import sanitize as sanitize_stl

LOG_FILE = "key_log.csv"
LOG_FIELDS = [
    "timestamp", "image_path", "key_format", "brand",
    "num_cuts", "bitting_code", "depth_increment_mm",
    "stl_output", "notes",
]


def _init_log():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=LOG_FIELDS).writeheader()


def _append_log(record: dict):
    _init_log()
    with open(LOG_FILE, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore").writerow(record)


# ── image normalization ─────────────────────────────────────────────────────

def normalize_key_image(gray: np.ndarray) -> np.ndarray:
    """
    CLAHE contrast normalization + denoising for consistent edge detection
    across varying photo conditions (lighting, background, phone camera).
    """
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    eq = clahe.apply(gray)
    denoised = cv2.fastNlMeansDenoising(eq, h=10)
    return denoised


def extract_key_mask(gray_normalized: np.ndarray) -> np.ndarray:
    """
    Adaptive threshold + morphological clean-up to isolate key silhouette.
    More robust than fixed Canny thresholds against light/shadow variation.
    """
    thresh = cv2.adaptiveThreshold(
        gray_normalized, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        blockSize=25, C=8
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=1)
    return opened


# ── key format detection via vision ────────────────────────────────────────

def detect_key_format(image_path: str) -> tuple[str, str]:
    """
    Use GPT-4o Vision to identify key format and brand from image.
    Returns (format_code, brand). Falls back to UNKNOWN on error.
    """
    known = ", ".join(KNOWN_FORMATS[:-1])  # exclude UNKNOWN
    prompt = (
        f"Identify the key format in this image. Known formats: {known}. "
        "Look at the bow (handle) shape, tip shape, blade profile, and number of cuts. "
        "Return only a JSON object: "
        '{\"format\": \"KW1\", \"brand\": \"Kwikset\", \"confidence\": \"high\", \"notes\": \"...\"}. '
        "If uncertain, use UNKNOWN for format. No other text."
    )
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        mime = "jpeg" if ext in ("jpg", "jpeg") else ext
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/{mime};base64,{b64}",
                        "detail": "high"
                    }},
                ],
            }],
            max_tokens=150,
            temperature=0.0,
        )
        result = json.loads(resp.choices[0].message.content)
        fmt = result.get("format", "UNKNOWN").upper()
        brand = result.get("brand", "Unknown")
        return fmt, brand
    except Exception as e:
        return "UNKNOWN", "Unknown"


# ── bitting analysis ────────────────────────────────────────────────────────

def analyze_bitting(key_region: np.ndarray, spec: dict) -> list[int]:
    """
    Sample the blade profile at each cut position and map pixel depth
    to the nearest valid bitting value using spec parameters.
    """
    h, w = key_region.shape
    num_cuts = spec["num_cuts"]
    depths = spec["depths"]
    depth_increment = spec["depth_increment_mm"]

    # Normalize pixel-space depth to mm using blade_width as reference
    blade_width_px = h  # lower half of key region = blade
    px_per_mm = blade_width_px / spec["blade_width_mm"]

    blade = key_region[h // 2:, :]
    bh, bw = blade.shape
    segment_w = bw // num_cuts

    bitting = []
    for i in range(num_cuts):
        seg = blade[:, i * segment_w:(i + 1) * segment_w]
        # Find lowest dark row in segment (deepest cut = most material removed)
        col_profiles = np.mean(seg, axis=1)
        # Invert: dark pixels = key material, find lowest point of material
        cut_px = np.argmax(col_profiles < 128) if np.any(col_profiles < 128) else 0
        cut_mm = cut_px / px_per_mm
        # Map to nearest valid depth value
        depth_val = round(cut_mm / depth_increment)
        depth_val = max(min(depths), min(max(depths), depth_val))
        if depth_val not in depths:
            depth_val = min(depths, key=lambda d: abs(d - depth_val))
        bitting.append(depth_val)

    return bitting


# ── main app ────────────────────────────────────────────────────────────────

class KeyForge3DApp:
    def __init__(self, root):
        self.root = root
        self.root.title("KeyForge3D")
        self.root.geometry("680x520")

        self.image_path = None
        self.detected_format = tk.StringVar(value="UNKNOWN")
        self.detected_brand = tk.StringVar(value="—")

        # Layout
        tk.Label(root, text="KeyForge3D", font=("Arial", 18, "bold")).pack(pady=8)

        # Key format row
        fmt_frame = tk.Frame(root)
        fmt_frame.pack(fill="x", padx=20, pady=4)
        tk.Label(fmt_frame, text="Key Format:", width=12, anchor="w").pack(side="left")
        self.format_menu = ttk.Combobox(
            fmt_frame, textvariable=self.detected_format,
            values=KNOWN_FORMATS, state="readonly", width=12
        )
        self.format_menu.pack(side="left", padx=4)
        self.format_menu.bind("<<ComboboxSelected>>", self._on_format_change)
        tk.Label(fmt_frame, text="Brand:").pack(side="left", padx=(16, 4))
        tk.Label(fmt_frame, textvariable=self.detected_brand, anchor="w", width=20).pack(side="left")

        # Buttons
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=6)
        tk.Button(btn_frame, text="Upload Image", command=self.upload_image).pack(side="left", padx=6)
        self.detect_btn = tk.Button(btn_frame, text="Detect Format (AI)", command=self.run_detection, state=tk.DISABLED)
        self.detect_btn.pack(side="left", padx=6)
        self.process_btn = tk.Button(btn_frame, text="Generate STL", command=self.process_key, state=tk.DISABLED)
        self.process_btn.pack(side="left", padx=6)

        # Image preview
        self.image_label = tk.Label(root)
        self.image_label.pack(pady=4)

        # Bitting display
        self.bitting_var = tk.StringVar(value="Bitting: —")
        tk.Label(root, textvariable=self.bitting_var, font=("Courier", 14)).pack(pady=4)

        self.result_label = tk.Label(root, text="", font=("Arial", 11), wraplength=620)
        self.result_label.pack(pady=4)

    def _on_format_change(self, _=None):
        spec = get_spec(self.detected_format.get())
        self.detected_brand.set(spec["brand"])

    def upload_image(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.jpg *.jpeg *.png")])
        if not path:
            return
        self.image_path = path
        img = Image.open(path).resize((360, 180), Image.Resampling.LANCZOS)
        img_tk = ImageTk.PhotoImage(img)
        self.image_label.config(image=img_tk)
        self.image_label.image = img_tk
        self.detect_btn.config(state=tk.NORMAL)
        self.process_btn.config(state=tk.NORMAL)
        self.bitting_var.set("Bitting: —")
        self.result_label.config(text="Image loaded. Run AI detection or select format manually.")

    def run_detection(self):
        if not self.image_path:
            return
        self.result_label.config(text="Detecting key format via AI…")
        self.root.update()
        fmt, brand = detect_key_format(self.image_path)
        self.detected_format.set(fmt)
        self.detected_brand.set(brand)
        spec = get_spec(fmt)
        self.result_label.config(
            text=f"Detected: {fmt} ({brand}) — {spec['num_cuts']} cuts, "
                 f"{spec['depth_increment_mm']}mm increment. Verify or override above."
        )

    def process_key(self):
        if not self.image_path:
            messagebox.showerror("Error", "Upload an image first.")
            return

        fmt = self.detected_format.get()
        spec = get_spec(fmt)

        try:
            image = cv2.imread(self.image_path)
            if image is None:
                raise ValueError("Could not load image.")

            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            normalized = normalize_key_image(gray)
            mask = extract_key_mask(normalized)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            key_contour = None
            for c in sorted(contours, key=cv2.contourArea, reverse=True):
                x, y, w, h = cv2.boundingRect(c)
                ratio = w / float(h) if h else 0
                if 2 < ratio < 7 and w > 80:
                    key_contour = c
                    break

            if key_contour is None:
                raise ValueError(
                    "Key not detected. Ensure key is horizontal on a contrasting background."
                )

            x, y, w, h = cv2.boundingRect(key_contour)
            key_region = normalized[y:y + h, x:x + w]

            # Bitting analysis with spec
            bitting = analyze_bitting(key_region, spec)
            bitting_str = " ".join(str(b) for b in bitting)
            self.bitting_var.set(f"Bitting: {bitting_str}  [{fmt}]")

            # Build 3D mesh
            scale = spec["blade_width_mm"] / h  # px → mm calibrated to spec
            points = key_contour.reshape(-1, 2) * scale
            key_polygon = Polygon(points)
            key_mesh = trimesh.creation.extrude_polygon(key_polygon, height=spec["key_thickness_mm"])

            # Apply bitting cuts
            segment_w_mm = (w * scale) / spec["num_cuts"]
            for i, depth_val in enumerate(bitting):
                cut_depth_mm = depth_val * spec["depth_increment_mm"]
                cut_x = (i * segment_w_mm) + (segment_w_mm / 2)
                cut_box = trimesh.creation.box(
                    extents=[segment_w_mm, cut_depth_mm, spec["key_thickness_mm"] + 1],
                    transform=trimesh.transformations.translation_matrix([cut_x, 0, 0])
                )
                key_mesh = key_mesh.difference(cut_box)

            output_path = f"key_{fmt}_{bitting_str.replace(' ', '')}.stl"
            key_mesh.export(output_path)
            sanitize_stl(output_path)

            # Log record
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "image_path": self.image_path,
                "key_format": fmt,
                "brand": spec["brand"],
                "num_cuts": spec["num_cuts"],
                "bitting_code": bitting_str,
                "depth_increment_mm": spec["depth_increment_mm"],
                "stl_output": output_path,
                "notes": "",
            }
            _append_log(record)

            self.result_label.config(
                text=(
                    f"STL saved: {output_path}\n"
                    f"Format: {fmt} | Brand: {spec['brand']} | "
                    f"Cuts: {spec['num_cuts']} | Bitting: {bitting_str}"
                )
            )

        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.result_label.config(text=f"Error: {e}")


if __name__ == "__main__":
    root = tk.Tk()
    app = KeyForge3DApp(root)
    root.mainloop()
