"""
PC Receiver Viewer -- "Mahindra Digital Eye Vault (Read-Only Viewer)"

A separate, standalone companion to receiver.py. It does NOT run the
pairing/upload pipeline and never writes to the inspection data at all --
it only opens (read-only) the same `received_data/` folder + per-app
data.xlsx files that receiver.py already produces, and serves them to
other users as a password-protected web page with an Excel download.

Why a separate app instead of adding routes to receiver.py:
  - receiver.py's dashboard is deliberately restricted to 127.0.0.1 only
    (see its module docstring) -- it was never designed to be reachable
    from the network. This app is the opposite: it's MEANT to be reached
    by other machines, so it needs its own auth (a password) and its own
    exposure story, kept completely separate from the phone-pairing
    security model.
  - Keeping this as its own process means a bug, restart, or crash here
    can never affect the live phone -> PC sync pipeline receiver.py runs.
  - It can run on the same PC as receiver.py (different port) or point at
    a synced copy of the data elsewhere -- see README.md.

Security model (read this before deploying):
  - Every page and API route requires a valid login (single shared
    password by default; see README.md for how to set/rotate it).
  - This process is plain HTTP by default, same as receiver.py. Unlike
    receiver.py (LAN-only, low practical exposure), THIS app is meant to
    be reached from other networks/WiFi -- sending a password over plain
    HTTP on an untrusted path is a real risk. Put this behind HTTPS
    (a reverse proxy, or the built-in optional TLS -- see README.md)
    before exposing it beyond a trusted LAN.
  - Every route here is read-only: no upload, no delete, no settings,
    no device pairing. Files are only ever opened for reading.
"""

import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

import mimetypes

import uvicorn
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from assets import FAVICON_PNG_BASE64, LOGO_PNG_BASE64

# ── Paths (works both as a plain script and a PyInstaller --onefile exe) ───

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_FILE = APP_DIR / "viewer_config.json"


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


_config = _load_config()

# ── Where the (read-only) inspection data lives ─────────────────────────────
# This must point at the SAME folder receiver.py writes to -- either the
# literal same path (if this runs on the same PC as receiver.py), or a
# synced/shared copy of it if this runs elsewhere. See README.md.
DATA_DIR = Path(
    _config.get("dataDir")
    or os.environ.get("VIEWER_DATA_DIR")
    or (APP_DIR / "received_data")
)
if not DATA_DIR.exists():
    print(f"[warn] Data folder {DATA_DIR} does not exist yet -- the viewer will "
          f"show no data until it does. Set VIEWER_DATA_DIR to point at the "
          f"receiver's data folder.")

PORT = int(os.environ.get("VIEWER_PORT", "8766"))
TLS_CERT = os.environ.get("VIEWER_TLS_CERT")
TLS_KEY = os.environ.get("VIEWER_TLS_KEY")

SESSION_TTL_SECONDS = 12 * 3600  # a logged-in session stays valid 12h
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
MAX_ROWS_RETURNED = 5000  # keeps very large datasets from freezing the browser

# ── One-time secrets: session-signing key, and the login password hash ─────
# Both are generated on first run and persisted in viewer_config.json so
# a restart doesn't invalidate existing sessions or reset the password.

if "sessionSecret" not in _config:
    _config["sessionSecret"] = secrets.token_hex(32)
    _save_config(_config)
SESSION_SECRET = _config["sessionSecret"].encode()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode()).hexdigest()


def _set_password(password: str):
    salt = secrets.token_hex(16)
    _config["passwordSalt"] = salt
    _config["passwordHash"] = _hash_password(password, salt)
    _save_config(_config)


if "passwordHash" not in _config:
    env_password = os.environ.get("VIEWER_PASSWORD")
    if env_password:
        _set_password(env_password)
        print("[info] Viewer password set from VIEWER_PASSWORD environment variable.")
    else:
        generated = secrets.token_urlsafe(9)
        _set_password(generated)
        note_path = APP_DIR / "FIRST_RUN_PASSWORD.txt"
        note_path.write_text(
            "This is the one-time generated login password for the Digital Eye "
            "Viewer.\nShare it with the people who need to view data, then "
            "delete this file -- it is not needed again.\n\n"
            f"Password: {generated}\n"
        )
        print("=" * 70)
        print(f"  Generated viewer login password: {generated}")
        print(f"  Also saved to: {note_path}")
        print("  Share it with viewers, then delete that file.")
        print("  To set your own password instead, run:")
        print("    python viewer.py --set-password")
        print("=" * 70)


def _verify_password(password: str) -> bool:
    salt = _config.get("passwordSalt", "")
    expected = _config.get("passwordHash", "")
    return hmac.compare_digest(_hash_password(password, salt), expected)


# ── Login rate limiting (mirrors receiver.py's approach) ───────────────────

_lock = threading.Lock()
_failed_attempts: dict[str, list[float]] = {}


def _is_locked_out(ip: str) -> bool:
    with _lock:
        attempts = [t for t in _failed_attempts.get(ip, []) if time.time() - t < LOCKOUT_SECONDS]
        _failed_attempts[ip] = attempts
        return len(attempts) >= MAX_FAILED_ATTEMPTS


def _record_failure(ip: str):
    with _lock:
        _failed_attempts.setdefault(ip, []).append(time.time())


def _clear_failures(ip: str):
    with _lock:
        _failed_attempts.pop(ip, None)


# ── Signed session cookie (no extra dependency -- same hand-rolled HMAC
#    style receiver.py already uses for its upload signatures) ─────────────

def _make_session_cookie() -> str:
    expires_at = str(int(time.time()) + SESSION_TTL_SECONDS)
    sig = hmac.new(SESSION_SECRET, expires_at.encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{sig}"


def _session_valid(cookie_value: str | None) -> bool:
    if not cookie_value or "." not in cookie_value:
        return False
    expires_at, _, sig = cookie_value.partition(".")
    if not expires_at.isdigit():
        return False
    expected_sig = hmac.new(SESSION_SECRET, expires_at.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return False
    return int(expires_at) > time.time()


def _require_session(viewer_session: str | None = Cookie(default=None)):
    if not _session_valid(viewer_session):
        raise HTTPException(status_code=401, detail="Login required")


app = FastAPI()


# ── Data helpers (read-only; mirrors receiver.py's own flattening logic so
#    the Excel columns/shape match what people already see from receiver's
#    Vault tab) ───────────────────────────────────────────────────────────

EXCEL_COLUMNS = [
    ("VIN", "vin"),
    ("Model Code", "modelCode"),
    ("Model Name", "modelName"),
    ("Date", "date"),
    ("Time", "time"),
    ("Shift", "shift"),
    ("Shift Date", "shiftDate"),
    ("Task", "taskName"),
    ("Detected", "className"),
    ("Result", "result"),
    ("Device", "device"),
    ("App", "appName"),
    ("Batch", "batch"),
]


def _safe_name(name: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
    return cleaned.replace(" ", "_") or "unknown"


def _resolve_under_data_dir(rel_path: str) -> Path:
    target = (DATA_DIR / rel_path).resolve()
    try:
        target.relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return target


def _list_groups() -> list[dict]:
    """Every device/app folder pair under DATA_DIR, with basic stats.
    Derived purely from the folder layout -- unlike receiver.py's device
    list, this doesn't depend on paired_devices.json, so it still shows
    historical data for a device that has since been un-paired."""
    groups = []
    if not DATA_DIR.exists():
        return groups
    for device_dir in sorted(DATA_DIR.iterdir()):
        if not device_dir.is_dir():
            continue
        for app_dir in sorted(device_dir.iterdir()):
            if not app_dir.is_dir():
                continue
            batch_dirs = sorted([p for p in app_dir.iterdir() if p.is_dir()])
            xlsx_path = app_dir / "data.xlsx"
            groups.append({
                "device": device_dir.name,
                "appName": app_dir.name,
                "batchCount": len(batch_dirs),
                "lastReceivedAt": batch_dirs[-1].name if batch_dirs else None,
                "hasExcel": xlsx_path.exists(),
            })
    groups.sort(key=lambda g: g["lastReceivedAt"] or "", reverse=True)
    return groups


def _flatten_rows(device: str, app_name: str) -> list[dict]:
    device_dir = DATA_DIR / _safe_name(device) / _safe_name(app_name)
    rows = []
    if not device_dir.exists():
        return rows
    for batch_dir in sorted(device_dir.iterdir()):
        if not batch_dir.is_dir():
            continue
        manifest_path = batch_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            inspections = json.loads(manifest_path.read_text())
        except Exception:
            continue

        def _image_url(rel, _batch=batch_dir.name):
            if not rel:
                return None
            return (
                f"/api/image?device={device}&appName={app_name}"
                f"&batch={_batch}&rel={rel}"
            )

        for insp in inspections:
            tasks = insp.get("tasks") or [{}]
            for task in tasks:
                rows.append({
                    "device": device,
                    "appName": app_name,
                    "batch": batch_dir.name,
                    "vin": insp.get("vin"),
                    "modelCode": insp.get("modelCode"),
                    "modelName": insp.get("modelName"),
                    "date": insp.get("date"),
                    "time": insp.get("time"),
                    "shift": insp.get("shift"),
                    "shiftDate": insp.get("shiftDate") or insp.get("date"),
                    "taskName": task.get("taskName"),
                    "className": task.get("className"),
                    "result": "OK" if task.get("success") else "NOT OK",
                    "imageUrl": _image_url(task.get("imagePath")),
                })
    rows.sort(key=lambda r: (r["date"] or "", r["time"] or ""), reverse=True)
    return rows


def _apply_filters(rows: list[dict], q: str, result: str, date_from: str, date_to: str) -> list[dict]:
    if q:
        q_lower = q.lower()
        rows = [r for r in rows if q_lower in (r.get("vin") or "").lower()
                or q_lower in (r.get("modelCode") or "").lower()
                or q_lower in (r.get("modelName") or "").lower()]
    if result in ("OK", "NOT OK"):
        rows = [r for r in rows if r.get("result") == result]
    if date_from:
        rows = [r for r in rows if (r.get("date") or "") >= date_from]
    if date_to:
        rows = [r for r in rows if (r.get("date") or "") <= date_to]
    return rows


def _style_worksheet(ws, n_cols: int):
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(n_cols)}{max(ws.max_row, 1)}"
    for i, (label, _key) in enumerate(EXCEL_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, len(label) + 4)


def _build_workbook(rows: list[dict]) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append([label for label, _key in EXCEL_COLUMNS])
    for row in rows:
        ws.append([row.get(key, "") for _label, key in EXCEL_COLUMNS])
    _style_worksheet(ws, len(EXCEL_COLUMNS))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ── Auth routes ──────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    error_html = f'<div class="error">{error}</div>' if error else ""
    return LOGIN_HTML.replace("__ERROR__", error_html).replace("__FAVICON_B64__", FAVICON_PNG_BASE64)


@app.post("/login")
async def login_submit(request: Request):
    ip = request.client.host if request.client else "unknown"
    if _is_locked_out(ip):
        return RedirectResponse(
            url="/login?error=Too+many+attempts.+Wait+a+minute+and+try+again.", status_code=303)

    form = await request.form()
    password = str(form.get("password", ""))

    if not _verify_password(password):
        _record_failure(ip)
        return RedirectResponse(url="/login?error=Incorrect+password", status_code=303)

    _clear_failures(ip)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        "viewer_session",
        _make_session_cookie(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=bool(TLS_CERT),
    )
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("viewer_session")
    return resp


# ── Read-only data routes (all require a valid session) ─────────────────────

@app.get("/api/groups")
async def api_groups(_: None = Depends(_require_session)):
    return _list_groups()


@app.get("/api/data")
async def api_data(
    device: str,
    appName: str,
    q: str = "",
    result: str = "",
    dateFrom: str = "",
    dateTo: str = "",
    _: None = Depends(_require_session),
):
    rows = _flatten_rows(device, appName)
    rows = _apply_filters(rows, q, result, dateFrom, dateTo)
    truncated = len(rows) > MAX_ROWS_RETURNED
    return {"rows": rows[:MAX_ROWS_RETURNED], "totalCount": len(rows), "truncated": truncated}


@app.get("/api/image")
async def api_image(device: str, appName: str, batch: str, rel: str, _: None = Depends(_require_session)):
    rel_path = f"{_safe_name(device)}/{_safe_name(appName)}/{batch}/{rel}"
    target = _resolve_under_data_dir(rel_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(content=target.read_bytes(), media_type=media_type)


@app.get("/download/excel")
async def download_master_excel(device: str, appName: str, _: None = Depends(_require_session)):
    """The receiver's own running data.xlsx for this device/app -- always
    current as of the last successful upload, downloaded as-is."""
    xlsx_path = DATA_DIR / _safe_name(device) / _safe_name(appName) / "data.xlsx"
    if not xlsx_path.exists():
        raise HTTPException(status_code=404, detail="No data received yet")
    return FileResponse(xlsx_path, filename=f"{_safe_name(device)}_{_safe_name(appName)}.xlsx")


@app.get("/download/excel-filtered")
async def download_filtered_excel(
    device: str,
    appName: str,
    q: str = "",
    result: str = "",
    dateFrom: str = "",
    dateTo: str = "",
    _: None = Depends(_require_session),
):
    rows = _flatten_rows(device, appName)
    rows = _apply_filters(rows, q, result, dateFrom, dateTo)
    buf = _build_workbook(rows)
    filename = f"{_safe_name(device)}_{_safe_name(appName)}_filtered.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/", response_class=HTMLResponse)
async def home(viewer_session: str | None = Cookie(default=None)):
    if not _session_valid(viewer_session):
        return RedirectResponse(url="/login")
    return (
        VIEWER_HTML
        .replace("__FAVICON_B64__", FAVICON_PNG_BASE64)
        .replace("__LOGO_B64__", LOGO_PNG_BASE64)
    )


# ── HTML (kept in the same visual style as receiver.py's dashboard) ────────

LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Digital Eye Viewer -- Login</title>
<link rel="icon" type="image/png" href="data:image/png;base64,__FAVICON_B64__">
<style>
  :root { --crimson: #DC143C; --crimson-dark: #B01030; --navy: #151923; --bg: #f7f7fa; --border: #e6e6ec; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: var(--bg);
         display: flex; align-items: center; justify-content: center; height: 100vh; }
  .card { background: #fff; border: 1px solid var(--border); border-radius: 14px; padding: 32px; width: 320px;
          box-shadow: 0 4px 20px rgba(0,0,0,0.08); }
  h1 { font-size: 16px; margin: 0 0 4px; color: var(--navy); }
  p.sub { margin: 0 0 20px; font-size: 12px; color: #6b7280; }
  input[type=password] { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
                          font-size: 14px; margin-bottom: 14px; }
  button { width: 100%; padding: 11px; border: none; border-radius: 8px; background: var(--crimson); color: #fff;
           font-weight: 700; font-size: 13px; cursor: pointer; }
  button:hover { background: var(--crimson-dark); }
  .error { background: #fdeaea; color: var(--crimson-dark); padding: 8px 10px; border-radius: 6px; font-size: 12px;
           margin-bottom: 14px; }
</style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>Digital Eye Viewer</h1>
    <p class="sub">Read-only. Enter the viewer password to continue.</p>
    __ERROR__
    <input type="password" name="password" placeholder="Password" autofocus required>
    <button type="submit">View Data</button>
  </form>
</body>
</html>
"""

VIEWER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mahindra Digital Eye Vault -- Viewer</title>
<link rel="icon" type="image/png" href="data:image/png;base64,__FAVICON_B64__">
<style>
  :root {
    --crimson: #DC143C; --crimson-dark: #B01030; --navy: #151923; --navy-light: #1f2430;
    --bg: #f7f7fa; --card: #ffffff; --border: #e6e6ec; --text: #1c1f26; --muted: #6b7280; --green: #1f9d55;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: var(--bg); color: var(--text); }
  header { background: var(--navy); color: #fff; padding: 10px 24px; display: flex; align-items: center; gap: 14px;
           box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
  header img { height: 48px; }
  header .titles h1 { margin: 0; font-size: 16px; }
  header .titles p { margin: 2px 0 0; font-size: 11px; color: #9aa0ad; }
  header .spacer { flex: 1; }
  header a { color: #cfd3db; font-size: 12px; text-decoration: none; }
  header a:hover { color: #fff; }

  main { padding: 20px 24px 40px; max-width: 1200px; margin: 0 auto; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; }
  select, input[type=text], input[type=date] {
    padding: 9px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 13px;
  }
  button.primary { background: var(--crimson); color: #fff; border: none; padding: 9px 14px; border-radius: 8px;
                   font-size: 13px; font-weight: 600; cursor: pointer; }
  button.primary:hover { background: var(--crimson-dark); }
  button.secondary { background: #fff; border: 1px solid var(--border); padding: 9px 14px; border-radius: 8px;
                      font-size: 13px; font-weight: 600; cursor: pointer; color: var(--text); }
  button.secondary:hover { background: #fafafc; }

  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  table.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  table.data-table thead th { position: sticky; top: 0; background: #fafafc; border-bottom: 2px solid var(--border);
                               padding: 10px; text-align: left; white-space: nowrap; }
  table.data-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  table.data-table tbody tr:hover { background: #fbfbfd; }
  .pill { padding: 3px 9px; border-radius: 20px; font-size: 11px; font-weight: 700; display: inline-block; }
  .pill.ok { background: #e7f7ee; color: var(--green); }
  .pill.notok { background: #fdeaea; color: var(--crimson-dark); }
  .muted { color: var(--muted); font-size: 12px; }
  .empty { padding: 40px; text-align: center; color: var(--muted); }
  a.thumb-link { color: var(--crimson); text-decoration: none; font-size: 12px; font-weight: 600; }
</style>
</head>
<body>
<header>
  <img src="data:image/png;base64,__LOGO_B64__" alt="logo">
  <div class="titles">
    <h1>Digital Eye Vault -- Viewer</h1>
    <p>Read-only. View inspections and download Excel.</p>
  </div>
  <div class="spacer"></div>
  <a href="/logout">Log out</a>
</header>
<main>
  <div class="toolbar">
    <select id="groupSelect"></select>
    <input type="text" id="q" placeholder="Search VIN / model...">
    <select id="resultFilter">
      <option value="">All results</option>
      <option value="OK">OK only</option>
      <option value="NOT OK">NOT OK only</option>
    </select>
    <input type="date" id="dateFrom">
    <span class="muted">to</span>
    <input type="date" id="dateTo">
    <button class="secondary" id="applyBtn">Apply</button>
    <span style="flex:1"></span>
    <button class="secondary" id="downloadFullBtn">Download Full Excel</button>
    <button class="primary" id="downloadFilteredBtn">Download Filtered Excel</button>
  </div>
  <div class="card">
    <div id="tableWrap"></div>
  </div>
  <p class="muted" id="statusLine" style="margin-top:10px;"></p>
</main>

<script>
let groups = [];

async function loadGroups() {
  const res = await fetch('/api/groups');
  if (res.status === 401) { window.location = '/login'; return; }
  groups = await res.json();
  const sel = document.getElementById('groupSelect');
  sel.innerHTML = groups.map(g =>
    `<option value="${g.device}|${g.appName}">${g.device} / ${g.appName} (${g.batchCount} sends)</option>`
  ).join('');
  if (groups.length === 0) {
    document.getElementById('tableWrap').innerHTML = '<div class="empty">No data received yet.</div>';
    return;
  }
  await loadData();
}

function currentGroup() {
  const [device, appName] = document.getElementById('groupSelect').value.split('|');
  return { device, appName };
}

function filterParams() {
  const { device, appName } = currentGroup();
  const q = document.getElementById('q').value;
  const result = document.getElementById('resultFilter').value;
  const dateFrom = document.getElementById('dateFrom').value;
  const dateTo = document.getElementById('dateTo').value;
  return new URLSearchParams({ device, appName, q, result, dateFrom, dateTo });
}

async function loadData() {
  const params = filterParams();
  const res = await fetch('/api/data?' + params.toString());
  if (res.status === 401) { window.location = '/login'; return; }
  const data = await res.json();
  renderTable(data.rows);
  document.getElementById('statusLine').textContent =
    `${data.totalCount} row(s)` + (data.truncated ? ` -- showing first ${data.rows.length}, narrow your filters to see more` : '');
}

function renderTable(rows) {
  const wrap = document.getElementById('tableWrap');
  if (rows.length === 0) {
    wrap.innerHTML = '<div class="empty">No rows match this filter.</div>';
    return;
  }
  const head = `<tr>
    <th>VIN</th><th>Model Code</th><th>Model Name</th><th>Date</th><th>Time</th>
    <th>Shift</th><th>Task</th><th>Detected</th><th>Result</th><th>Image</th>
  </tr>`;
  const body = rows.map(r => `<tr>
    <td>${r.vin || ''}</td>
    <td>${r.modelCode || ''}</td>
    <td>${r.modelName || ''}</td>
    <td>${r.date || ''}</td>
    <td>${r.time || ''}</td>
    <td>${r.shift || ''}</td>
    <td>${r.taskName || ''}</td>
    <td>${r.className || ''}</td>
    <td><span class="pill ${r.result === 'OK' ? 'ok' : 'notok'}">${r.result}</span></td>
    <td>${r.imageUrl ? `<a class="thumb-link" href="${r.imageUrl}" target="_blank">View</a>` : ''}</td>
  </tr>`).join('');
  wrap.innerHTML = `<table class="data-table"><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

document.getElementById('groupSelect').addEventListener('change', loadData);
document.getElementById('applyBtn').addEventListener('click', loadData);
document.getElementById('downloadFullBtn').addEventListener('click', () => {
  const { device, appName } = currentGroup();
  window.location = '/download/excel?' + new URLSearchParams({ device, appName }).toString();
});
document.getElementById('downloadFilteredBtn').addEventListener('click', () => {
  window.location = '/download/excel-filtered?' + filterParams().toString();
});

loadGroups();
</script>
</body>
</html>
"""


def _cli_set_password():
    import getpass
    pw = getpass.getpass("New viewer password: ")
    if len(pw) < 6:
        print("Password must be at least 6 characters.")
        sys.exit(1)
    confirm = getpass.getpass("Confirm password: ")
    if pw != confirm:
        print("Passwords did not match.")
        sys.exit(1)
    _set_password(pw)
    print("Viewer password updated.")


if __name__ == "__main__":
    if "--set-password" in sys.argv:
        _cli_set_password()
        sys.exit(0)

    local_ip = "0.0.0.0"
    print("Mahindra Digital Eye Vault -- Viewer (read-only)")
    print(f"Data folder: {DATA_DIR}")
    print(f"Listening on: http://{local_ip}:{PORT}  (reachable from the network)")
    if not TLS_CERT:
        print("[warn] Running over plain HTTP. Put this behind HTTPS (reverse proxy, or set")
        print("       VIEWER_TLS_CERT / VIEWER_TLS_KEY) before exposing it beyond a trusted LAN.")

    ssl_kwargs = {}
    if TLS_CERT and TLS_KEY:
        ssl_kwargs = {"ssl_certfile": TLS_CERT, "ssl_keyfile": TLS_KEY}

    uvicorn.run(app, host="0.0.0.0", port=PORT, **ssl_kwargs)
