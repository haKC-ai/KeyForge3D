"""
api.py — KeyForge3D FastAPI server

Endpoints:
    POST /analyze          — upload image, get full analysis JSON
    GET  /stl/{filename}   — download generated STL
    GET  /log              — view key_log.csv as JSON
    GET  /formats          — list supported key formats
    GET  /health           — liveness check
"""
import csv
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import analyze_image
from key_specs import KEY_SPECS, KNOWN_FORMATS

STL_DIR = Path("stl_output")
STL_DIR.mkdir(exist_ok=True)
LOG_FILE = "key_log.csv"

app = FastAPI(
    title="KeyForge3D API",
    description="Key photograph analysis — bitting extraction, EXIF logging, STL generation",
    version="1.0.0",
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/formats")
def list_formats():
    return {
        fmt: {
            "brand": spec["brand"],
            "num_cuts": spec["num_cuts"],
            "depths": spec["depths"],
            "depth_increment_mm": spec["depth_increment_mm"],
        }
        for fmt, spec in KEY_SPECS.items()
        if fmt != "UNKNOWN"
    }


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    key_format: str = Form(None, description="Key format override (e.g. KW1). Omit to auto-detect."),
):
    allowed_types = {"image/jpeg", "image/png", "image/tiff", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.content_type}")

    suffix = Path(file.filename or "upload.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        result = analyze_image(tmp_path, key_format=key_format, output_dir=str(STL_DIR))
    finally:
        os.unlink(tmp_path)

    d = result.to_dict()
    if result.stl_path and Path(result.stl_path).exists():
        d["stl_download"] = f"/stl/{Path(result.stl_path).name}"

    return JSONResponse(content=d)


@app.get("/stl/{filename}")
def download_stl(filename: str):
    # Prevent path traversal
    safe = Path(filename).name
    path = STL_DIR / safe
    if not path.exists() or path.suffix.lower() != ".stl":
        raise HTTPException(status_code=404, detail="STL not found")
    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename=safe,
    )


@app.get("/log")
def get_log():
    if not Path(LOG_FILE).exists():
        return []
    with open(LOG_FILE, newline="") as f:
        return list(csv.DictReader(f))


@app.get("/", response_class=HTMLResponse)
def web_ui():
    formats_opts = "".join(
        f'<option value="{f}">{f} — {KEY_SPECS[f]["brand"]}</option>'
        for f in KNOWN_FORMATS if f != "UNKNOWN"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>KeyForge3D</title>
<style>
  body {{ font-family: monospace; background: #111; color: #e0e0e0; max-width: 860px; margin: 40px auto; padding: 0 20px; }}
  h1 {{ color: #ff6600; }}
  label {{ display: block; margin: 10px 0 4px; }}
  input, select, button {{ background: #222; color: #e0e0e0; border: 1px solid #444; padding: 8px; border-radius: 4px; }}
  button {{ background: #ff6600; color: #111; font-weight: bold; cursor: pointer; padding: 10px 24px; }}
  button:disabled {{ background: #555; color: #888; cursor: default; }}
  #drop {{ border: 2px dashed #555; padding: 40px; text-align: center; margin: 20px 0; border-radius: 8px; }}
  #drop.over {{ border-color: #ff6600; background: #1a1000; }}
  #preview {{ max-height: 260px; max-width: 100%; margin: 10px 0; display: none; border-radius: 4px; }}
  #result {{ background: #1a1a1a; padding: 16px; border-radius: 6px; margin-top: 20px; white-space: pre-wrap; display: none; }}
  .bitting {{ font-size: 2em; color: #ff6600; letter-spacing: 0.15em; margin: 12px 0; }}
  .field {{ margin: 4px 0; }}
  .label {{ color: #888; width: 160px; display: inline-block; }}
  a.stl-dl {{ display: inline-block; margin-top: 12px; padding: 8px 18px; background: #226622; color: #aaffaa; border-radius: 4px; text-decoration: none; }}
  .error {{ color: #ff4444; }}
  #spinner {{ display: none; color: #ff6600; }}
</style>
</head>
<body>
<h1>KeyForge3D</h1>
<p>Upload a photograph of a physical key. Returns bitting code, EXIF intel, and a sanitized STL.</p>

<div id="drop">Drop image here or click to select
  <input type="file" id="fileInput" accept="image/*" style="display:none">
</div>
<img id="preview">

<label>Key Format (leave blank for AI detection)</label>
<select id="fmtSelect">
  <option value="">— Auto detect —</option>
  {formats_opts}
</select>

<br><br>
<button id="analyzeBtn" disabled>Analyze</button>
<span id="spinner"> ⏳ analyzing…</span>

<div id="result"></div>

<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('fileInput');
const preview = document.getElementById('preview');
const analyzeBtn = document.getElementById('analyzeBtn');
const spinner = document.getElementById('spinner');
const result = document.getElementById('result');
const fmtSelect = document.getElementById('fmtSelect');
let selectedFile = null;

drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('dragover', e => {{ e.preventDefault(); drop.classList.add('over'); }});
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', e => {{
  e.preventDefault(); drop.classList.remove('over');
  if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
}});
fileInput.addEventListener('change', () => {{ if (fileInput.files[0]) setFile(fileInput.files[0]); }});

function setFile(f) {{
  selectedFile = f;
  preview.src = URL.createObjectURL(f);
  preview.style.display = 'block';
  analyzeBtn.disabled = false;
  result.style.display = 'none';
}}

analyzeBtn.addEventListener('click', async () => {{
  if (!selectedFile) return;
  analyzeBtn.disabled = true;
  spinner.style.display = 'inline';
  result.style.display = 'none';

  const fd = new FormData();
  fd.append('file', selectedFile);
  if (fmtSelect.value) fd.append('key_format', fmtSelect.value);

  try {{
    const resp = await fetch('/analyze', {{ method: 'POST', body: fd }});
    const data = await resp.json();
    renderResult(data);
  }} catch(e) {{
    result.innerHTML = '<span class="error">Request failed: ' + e + '</span>';
    result.style.display = 'block';
  }} finally {{
    analyzeBtn.disabled = false;
    spinner.style.display = 'none';
  }}
}});

function renderResult(d) {{
  let html = '';
  if (d.error) {{
    html += '<span class="error">⚠ ' + d.error + '</span>\\n';
  }}
  html += '<div class="bitting">Bitting: ' + (d.bitting_code || '—') + '  [' + d.key_format + ']</div>';
  const fields = [
    ['Format', d.key_format + ' — ' + d.brand],
    ['Cuts', d.num_cuts + '  |  Increment: ' + d.depth_increment_mm + ' mm'],
    ['Cut spacing', d.cut_spacing_mm + ' mm  |  Tip→first cut: ' + d.tip_to_first_cut_mm + ' mm'],
    ['Timestamp', d.timestamp],
  ];
  if (d.gps) fields.push(['GPS', d.gps.lat + ', ' + d.gps.lon + (d.gps.alt_m ? '  alt ' + d.gps.alt_m + 'm' : '')]);
  const exifKeys = ['Make', 'Model', 'DateTime', 'Software'];
  exifKeys.forEach(k => {{ if (d.exif && d.exif[k]) fields.push(['EXIF ' + k, d.exif[k]]); }});
  if (d.vision_notes) fields.push(['AI notes', d.vision_notes]);

  fields.forEach(([l, v]) => {{
    html += '<div class="field"><span class="label">' + l + ':</span> ' + v + '</div>';
  }});

  if (d.stl_download) {{
    html += '<a class="stl-dl" href="' + d.stl_download + '" download>⬇ Download STL</a>';
  }}

  result.innerHTML = html;
  result.style.display = 'block';
}}
</script>
</body>
</html>"""
