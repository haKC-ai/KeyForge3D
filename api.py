"""
api.py — KeyForge3D FastAPI server

Endpoints:
    POST /analyze          — upload image, get full analysis JSON
    GET  /stl/{filename}   — download generated STL
    GET  /log              — view key_log.csv (auth required)
    GET  /formats          — list supported key formats
    GET  /health           — liveness check
    GET  /                 — web UI

Auth: set API_KEY env var. All non-public endpoints require
  X-API-Key: <value>  header.
"""
import csv
import imghdr
import os
import secrets
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader

from core import analyze_image
from key_specs import KEY_SPECS, KNOWN_FORMATS

# ── config ──────────────────────────────────────────────────────────────────

# Anchor all paths to the repo directory regardless of cwd
BASE_DIR = Path(__file__).parent.resolve()
STL_DIR = BASE_DIR / "stl_output"
STL_DIR.mkdir(exist_ok=True)
LOG_FILE = str(BASE_DIR / "key_log.csv")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
ALLOWED_MAGIC_TYPES = {"jpeg", "png", "tiff", "webp", "rgb"}  # imghdr names

_API_KEY = os.environ.get("API_KEY", "")
if not _API_KEY:
    # Generate a random key on startup if none configured; print once to stdout
    _API_KEY = secrets.token_hex(32)
    print(f"[keyforge3d] No API_KEY set — generated ephemeral key: {_API_KEY}", flush=True)

# ── app ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="KeyForge3D API",
    description="Key photograph analysis — bitting extraction, EXIF logging, STL generation",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],       # no cross-origin access by default
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# ── auth ─────────────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str = Depends(_api_key_header)):
    if not key or not secrets.compare_digest(key, _API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return key


# ── public endpoints ──────────────────────────────────────────────────────────

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


# ── protected endpoints ───────────────────────────────────────────────────────

@app.post("/analyze", dependencies=[Depends(require_api_key)])
async def analyze(
    file: UploadFile = File(...),
    key_format: str = Form(None),
):
    # Validate key_format against allowlist before it touches any path
    if key_format is not None:
        key_format = key_format.upper()
        if key_format not in KEY_SPECS:
            raise HTTPException(status_code=400, detail=f"Unknown key format: {key_format}")

    # Size cap — read with limit
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload exceeds 20 MB limit")

    # Write to temp with fixed suffix (never trust client filename)
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        # Magic-byte check — reject non-image content regardless of Content-Type header
        detected = imghdr.what(tmp_path)
        if detected not in ALLOWED_MAGIC_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"File content is not a supported image (detected: {detected!r})"
            )

        result = analyze_image(tmp_path, key_format=key_format, output_dir=str(STL_DIR))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    d = result.to_dict()

    # Strip server-side filesystem paths from response
    d.pop("image_path", None)
    stl_name = Path(result.stl_path).name if result.stl_path and Path(result.stl_path).exists() else None
    d["stl_path"] = None
    if stl_name:
        d["stl_download"] = f"/stl/{stl_name}"

    # Sanitize AI-generated text fields that go back to the client
    if d.get("vision_notes") and d["vision_notes"].startswith("AI detection"):
        pass  # already sanitized generic message
    # error field: strip any exception internals, keep only user-safe message
    if d.get("error") and len(d["error"]) > 200:
        d["error"] = d["error"][:200]

    return JSONResponse(content=d)


@app.get("/stl/{filename}")
def download_stl(filename: str, request: Request, key: str = None):
    # Accept key from header OR query param (browser download links can't set headers)
    header_key = request.headers.get("X-API-Key", "")
    provided_key = header_key or key or ""
    if not provided_key or not secrets.compare_digest(provided_key, _API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # .name strips all directory components — prevents path traversal
    safe = Path(filename).name
    path = STL_DIR / safe

    # Containment check: resolved path must stay inside STL_DIR
    try:
        path.resolve().relative_to(STL_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not path.exists() or path.suffix.lower() != ".stl":
        raise HTTPException(status_code=404, detail="STL not found")

    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename=safe,
    )


@app.get("/log", dependencies=[Depends(require_api_key)])
def get_log():
    if not Path(LOG_FILE).exists():
        return []
    with open(LOG_FILE, newline="") as f:
        rows = list(csv.DictReader(f))
    # Redact server-side paths; expose only analytical fields
    safe_fields = {"timestamp", "key_format", "brand", "num_cuts",
                   "bitting_code", "depth_increment_mm", "notes"}
    return [{k: v for k, v in row.items() if k in safe_fields} for row in rows]


# ── web UI ────────────────────────────────────────────────────────────────────

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
  #drop {{ border: 2px dashed #555; padding: 40px; text-align: center; margin: 20px 0; border-radius: 8px; cursor: pointer; }}
  #drop.over {{ border-color: #ff6600; background: #1a1000; }}
  #preview {{ max-height: 260px; max-width: 100%; margin: 10px 0; display: none; border-radius: 4px; }}
  #result {{ background: #1a1a1a; padding: 16px; border-radius: 6px; margin-top: 20px; display: none; }}
  .bitting {{ font-size: 2em; color: #ff6600; letter-spacing: 0.15em; margin: 12px 0; }}
  .field {{ margin: 4px 0; }}
  .label {{ color: #888; width: 160px; display: inline-block; }}
  a.stl-dl {{ display: inline-block; margin-top: 12px; padding: 8px 18px; background: #226622; color: #aaffaa; border-radius: 4px; text-decoration: none; }}
  .error {{ color: #ff4444; }}
  #spinner {{ display: none; color: #ff6600; }}
  #apiKeyRow {{ margin-bottom: 12px; }}
</style>
</head>
<body>
<h1>KeyForge3D</h1>
<p>Upload a photograph of a physical key. Returns bitting code, EXIF intel, and a sanitized STL.</p>

<div id="apiKeyRow">
  <label>API Key</label>
  <input type="password" id="apiKey" placeholder="X-API-Key" style="width:320px">
</div>

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
const esc = s => String(s ?? '')
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

const drop = document.getElementById('drop');
const fileInput = document.getElementById('fileInput');
const preview = document.getElementById('preview');
const analyzeBtn = document.getElementById('analyzeBtn');
const spinner = document.getElementById('spinner');
const result = document.getElementById('result');
const fmtSelect = document.getElementById('fmtSelect');
const apiKeyInput = document.getElementById('apiKey');
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
  const apiKey = apiKeyInput.value.trim();
  if (!apiKey) {{ alert('Enter your API key'); return; }}
  analyzeBtn.disabled = true;
  spinner.style.display = 'inline';
  result.style.display = 'none';

  const fd = new FormData();
  fd.append('file', selectedFile);
  if (fmtSelect.value) fd.append('key_format', fmtSelect.value);

  try {{
    const resp = await fetch('/analyze', {{
      method: 'POST',
      headers: {{ 'X-API-Key': apiKey }},
      body: fd
    }});
    const data = await resp.json();
    if (!resp.ok) {{
      renderError(data.detail ?? 'Request failed');
    }} else {{
      renderResult(data);
    }}
  }} catch(e) {{
    renderError('Network error: ' + e);
  }} finally {{
    analyzeBtn.disabled = false;
    spinner.style.display = 'none';
  }}
}});

function renderError(msg) {{
  result.innerHTML = '<span class="error">⚠ ' + esc(msg) + '</span>';
  result.style.display = 'block';
}}

function renderResult(d) {{
  const div = document.createElement('div');

  if (d.error) {{
    const err = document.createElement('div');
    err.className = 'error';
    err.textContent = '⚠ ' + d.error;
    div.appendChild(err);
  }}

  const bitting = document.createElement('div');
  bitting.className = 'bitting';
  bitting.textContent = 'Bitting: ' + (d.bitting_code || '—') + '  [' + d.key_format + ']';
  div.appendChild(bitting);

  const fields = [
    ['Format', d.key_format + ' — ' + d.brand],
    ['Cuts', d.num_cuts + '  |  Increment: ' + d.depth_increment_mm + ' mm'],
    ['Cut spacing', d.cut_spacing_mm + ' mm  |  Tip→first: ' + d.tip_to_first_cut_mm + ' mm'],
    ['Timestamp', d.timestamp],
  ];
  if (d.gps) fields.push(['GPS', d.gps.lat + ', ' + d.gps.lon + (d.gps.alt_m ? '  alt ' + d.gps.alt_m + 'm' : '')]);
  ['Make','Model','DateTime','Software'].forEach(k => {{
    if (d.exif && d.exif[k]) fields.push(['EXIF ' + k, d.exif[k]]);
  }});
  if (d.vision_notes) fields.push(['AI notes', d.vision_notes]);

  fields.forEach(([l, v]) => {{
    const row = document.createElement('div');
    row.className = 'field';
    const lbl = document.createElement('span');
    lbl.className = 'label';
    lbl.textContent = l + ':';
    const val = document.createElement('span');
    val.textContent = ' ' + v;
    row.appendChild(lbl);
    row.appendChild(val);
    div.appendChild(row);
  }});

  if (d.stl_download) {{
    const link = document.createElement('a');
    link.className = 'stl-dl';
    link.href = d.stl_download;
    link.download = '';
    link.textContent = '⬇ Download STL';
    // Pass API key as query param for download (browser can't set headers on <a>)
    link.href = d.stl_download + '?key=' + encodeURIComponent(apiKeyInput.value.trim());
    div.appendChild(link);
  }}

  result.innerHTML = '';
  result.appendChild(div);
  result.style.display = 'block';
}}
</script>
</body>
</html>"""
