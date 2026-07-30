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
                    "inspectionId": insp.get("inspectionId"),
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
async def api_data(device: str, appName: str, _: None = Depends(_require_session)):
    """Returns every row for this device/app, unfiltered -- same pattern
    receiver.py's own dashboard uses (fetch once per device, then filter,
    chart, and group entirely client-side), so filtering/charting/expand-
    collapse all react instantly without a round trip per keystroke."""
    rows = _flatten_rows(device, appName)
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


@app.post("/download/excel-filtered")
async def download_filtered_excel(request: Request, _: None = Depends(_require_session)):
    """Takes the exact rows the browser currently has filtered/displayed
    (sent in the request body) and turns them straight into a workbook --
    guarantees the download always matches what's on screen, instead of
    re-deriving a filter server-side that could drift out of sync with
    whatever filter combination the client-side table actually applied."""
    body = await request.json()
    rows = body.get("rows") or []
    device = _safe_name(body.get("device", "data"))
    app_name = _safe_name(body.get("appName", "app"))
    buf = _build_workbook(rows)
    filename = f"{device}_{app_name}_filtered.xlsx"
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
  /* Deliberately NOT a fixed-height/flex "app shell" layout -- this page
     flows top-to-bottom normally and lets the browser's own document
     scrollbar handle everything. A fixed-viewport layout with the table in
     a flex:1 internal-scroll box (which is what receiver.py's own
     dashboard does) squeezes the table into a sliver whenever the charts
     panel above it grows or the window is short -- on a small screen it
     can end up showing a single row. Scrolling the whole page avoids that
     failure mode entirely, at the cost of the table header no longer
     staying stuck to the top while you scroll (an acceptable trade). */
  html, body { margin: 0; }
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: var(--bg); color: var(--text); }
  header { background: var(--navy); color: #fff; padding: 10px 24px; display: flex; align-items: center; gap: 14px;
           box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
  header img { height: 48px; }
  header .titles h1 { margin: 0; font-size: 16px; }
  header .titles p { margin: 2px 0 0; font-size: 11px; color: #9aa0ad; }
  header .spacer { flex: 1; }
  header a { color: #cfd3db; font-size: 12px; text-decoration: none; }
  header a:hover { color: #fff; }

  main { padding: 20px 24px 60px; max-width: 1300px; margin: 0 auto; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 14px; }
  select, input[type=text], input[type=date] {
    padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 12px;
  }
  .filters input, .filters select { width: 130px; }
  .filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; background: var(--card);
             border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
  .filter-count { font-size: 11px; color: var(--muted); margin-left: auto; white-space: nowrap; }
  button.primary { background: var(--crimson); color: #fff; border: none; padding: 9px 14px; border-radius: 8px;
                   font-size: 13px; font-weight: 600; cursor: pointer; }
  button.primary:hover { background: var(--crimson-dark); }
  button.secondary { background: #fff; border: 1px solid var(--border); padding: 8px 12px; border-radius: 8px;
                      font-size: 12px; font-weight: 600; cursor: pointer; color: var(--text); }
  button.secondary:hover { background: #fafafc; }

  /* Charts panel -- kept as its own small scrollable widget (a fixed max-
     height here is fine, it's a compact area of its own, not the thing
     that starves the table below it of space). */
  .charts-panel { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
                  padding: 4px 16px; margin-bottom: 14px; max-height: 300px; overflow-y: auto; }
  /* Centered as a group -- only wraps to the next line when the sections
     actually run out of horizontal room, instead of hugging the left
     edge or each section claiming a full-width row regardless of size. */
  .charts-flow { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: center; gap: 22px; }
  .chart-row { margin: 10px 0; }
  /* Only sections marked as the start of a new group (task-level Overall
     charts vs. VIN-level charts vs. the By VIN/Day/Month extras) get a
     divider -- not every section, so "Overall Result" and "Overall Result
     by Shift" sit together with no line between them, then one divider,
     then "VIN Result (Pass/Fail)" and "VIN Result by Shift" together. */
  .chart-row.group-start { border-left: 1px dashed var(--border); padding-left: 22px; }
  .chart-row-title { font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 6px;
                      text-transform: uppercase; letter-spacing: 0.4px; display: flex; align-items: center; gap: 6px; }
  .chart-close { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 12px; padding: 0 2px; }
  .chart-close:hover { color: var(--crimson); }
  .chart-cards { display: flex; gap: 14px; flex-wrap: wrap; }
  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px;
                text-align: center; width: 112px; }
  /* Headline totals (Overall Result, VIN Result) read bigger than their
     per-shift breakdowns, so the "main" number is visually distinct from
     the supporting detail underneath it. */
  .chart-card.chart-card-lg { width: 132px; padding: 14px; }
  .chart-card.chart-card-lg .chart-label { font-size: 13px; }
  .chart-card.chart-card-lg .chart-meta { font-size: 11px; }
  .chart-card.chart-card-sm { width: 84px; padding: 6px; }
  .chart-card.chart-card-sm .chart-label { font-size: 10px; }
  .chart-card.chart-card-sm .chart-meta { font-size: 9px; }
  .chart-card.clickable-chart { cursor: pointer; transition: box-shadow 0.15s, border-color 0.15s; }
  .chart-card.clickable-chart:hover { border-color: var(--crimson); box-shadow: 0 2px 8px rgba(220,20,60,0.15); }
  .chart-card .chart-label { font-size: 11px; font-weight: 700; margin-top: 4px; white-space: nowrap;
                              overflow: hidden; text-overflow: ellipsis; }
  .chart-card .chart-meta { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .chart-note { font-size: 12px; color: var(--muted); font-style: italic; max-width: 280px; }
  .chart-hidden-bar { font-size: 11px; color: var(--muted); margin: 8px 0; display: flex; align-items: center; gap: 10px; }
  .chart-hidden-bar b { color: var(--text); }

  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  table.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  table.data-table thead th { background: #fafafc; border-bottom: 2px solid var(--border);
                               padding: 10px; text-align: left; white-space: nowrap; }
  table.data-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  tr.group-row { cursor: pointer; }
  tr.group-row:hover { background: #fbfbfd; }
  tr.group-row .chevron { display: inline-block; transition: transform 0.15s; color: var(--muted); }
  tr.group-row.expanded .chevron { transform: rotate(90deg); }
  tr.detail-row { display: none; background: #fafafc; }
  tr.detail-row.open { display: table-row; }
  tr.detail-row td { padding: 10px 10px 14px 34px !important; }
  table.mini-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  table.mini-table th { text-align: left; padding: 4px 8px; color: var(--muted); font-weight: 600;
                         border-bottom: 1px solid var(--border); }
  table.mini-table td { padding: 5px 8px; border-bottom: 1px solid #f0f0f3; }
  .task-count-ok, .badge-ok { color: var(--green); font-weight: 700; }
  .task-count-fail, .badge-fail { color: var(--crimson-dark); font-weight: 700; }
  .img-link { color: var(--crimson); cursor: pointer; text-decoration: underline; font-size: 11px;
              background: none; border: none; padding: 0; }
  .img-link:disabled { color: var(--muted); text-decoration: none; cursor: default; }
  .muted { color: var(--muted); font-size: 12px; }
  .empty { padding: 40px; text-align: center; color: var(--muted); }

  .lightbox { position: fixed; inset: 0; background: rgba(10,12,18,0.85); display: none;
              align-items: center; justify-content: center; z-index: 70; }
  .lightbox.open { display: flex; }
  .lightbox img { max-width: 90vw; max-height: 85vh; border-radius: 8px; }
  .lightbox .lb-close { position: absolute; top: 20px; right: 28px; color: #fff; font-size: 28px;
                          cursor: pointer; background: none; border: none; }
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
    <span style="flex:1"></span>
    <button class="secondary" id="downloadFullBtn">Download Full Excel</button>
    <button class="primary" id="downloadFilteredBtn">Download Filtered Excel</button>
  </div>

  <div class="filters">
    <input id="fVin" placeholder="Filter VIN..." oninput="renderTable()">
    <input id="fModel" placeholder="Filter Model Code..." oninput="renderTable()">
    <input id="fModelName" placeholder="Filter Model Name..." oninput="renderTable()">
    <input id="fDate" type="date" title="Filter Date" onchange="renderTable()">
    <select id="fYear" onchange="renderTable()"><option value="">All Years</option></select>
    <select id="fMonth" onchange="renderTable()">
      <option value="">All Months</option>
      <option value="01">Jan</option><option value="02">Feb</option><option value="03">Mar</option>
      <option value="04">Apr</option><option value="05">May</option><option value="06">Jun</option>
      <option value="07">Jul</option><option value="08">Aug</option><option value="09">Sep</option>
      <option value="10">Oct</option><option value="11">Nov</option><option value="12">Dec</option>
    </select>
    <select id="fShift" onchange="renderTable()">
      <option value="">All Shifts</option>
      <option value="A">Shift A</option><option value="B">Shift B</option><option value="C">Shift C</option>
    </select>
    <input id="fTask" placeholder="Filter Task..." oninput="renderTable()">
    <input id="fClass" placeholder="Filter Detected..." oninput="renderTable()">
    <select id="fResult" onchange="renderTable()">
      <option value="">All Results</option>
      <option value="OK">OK</option>
      <option value="NOT OK">NOT OK</option>
    </select>
    <input id="fBatch" placeholder="Filter Send/Batch..." oninput="renderTable()">
    <button class="secondary" onclick="clearFilters()">Clear Filters</button>
    <div class="filter-count" id="filterCount"></div>
  </div>

  <div class="charts-panel" id="chartsPanel"></div>

  <div class="card">
    <table class="data-table">
      <thead>
        <tr>
          <th></th><th>VIN</th><th>Model Code</th><th>Model Name</th><th>Date</th><th>Time</th>
          <th>Shift</th><th>Tasks</th><th>Result</th><th>Batch</th>
        </tr>
      </thead>
      <tbody id="viewerRows"></tbody>
    </table>
  </div>
</main>

<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <button class="lb-close" onclick="closeLightbox()">&#10005;</button>
  <img id="lightboxImg" src="" alt="Inspection image">
</div>

<script>
let groups = [];
let viewerRows = [];
let currentDevice = '', currentAppName = '';

async function loadGroups() {
  const res = await fetch('/api/groups');
  if (res.status === 401) { window.location = '/login'; return; }
  groups = await res.json();
  const sel = document.getElementById('groupSelect');
  sel.innerHTML = groups.map(g =>
    `<option value="${g.device}|${g.appName}">${g.device} / ${g.appName} (${g.batchCount} sends)</option>`
  ).join('');
  if (groups.length === 0) {
    document.getElementById('viewerRows').innerHTML =
      '<tr><td colspan="10" class="empty">No data received yet.</td></tr>';
    return;
  }
  await loadData();
}

async function loadData() {
  const [device, appName] = document.getElementById('groupSelect').value.split('|');
  currentDevice = device; currentAppName = appName;
  const res = await fetch('/api/data?' + new URLSearchParams({ device, appName }));
  if (res.status === 401) { window.location = '/login'; return; }
  const data = await res.json();
  viewerRows = data.rows;
  populateYearOptions();
  renderTable();
}

function populateYearOptions() {
  const years = [...new Set(viewerRows.map(r => (r.date || '').slice(0, 4)).filter(Boolean))].sort().reverse();
  const sel = document.getElementById('fYear');
  const current = sel.value;
  sel.innerHTML = '<option value="">All Years</option>' + years.map(y => `<option value="${y}">${y}</option>`).join('');
  sel.value = current;
}

function clearFilters() {
  ['fVin','fModel','fModelName','fTask','fClass','fBatch'].forEach(id => document.getElementById(id).value = '');
  ['fDate','fYear','fMonth','fShift','fResult'].forEach(id => document.getElementById(id).value = '');
  pinnedDay = null;
  renderTable();
}

// ── Filtering (client-side, mirrors receiver.py's own dashboard so every
//    filter reacts instantly against the rows already fetched) ──────────
function getFilteredRows() {
  const vin = document.getElementById('fVin').value.toLowerCase();
  const model = document.getElementById('fModel').value.toLowerCase();
  const modelName = document.getElementById('fModelName').value.toLowerCase();
  const date = document.getElementById('fDate').value;
  const year = document.getElementById('fYear').value;
  const month = document.getElementById('fMonth').value;
  const shift = document.getElementById('fShift').value;
  const task = document.getElementById('fTask').value.toLowerCase();
  const cls = document.getElementById('fClass').value.toLowerCase();
  const result = document.getElementById('fResult').value;
  const batch = document.getElementById('fBatch').value.toLowerCase();

  return viewerRows.filter(row => {
    const rowYear = (row.date || '').slice(0, 4);
    const rowMonth = (row.date || '').slice(5, 7);
    return (!vin || (row.vin || '').toLowerCase().includes(vin)) &&
      (!model || (row.modelCode || '').toLowerCase().includes(model)) &&
      (!modelName || (row.modelName || '').toLowerCase().includes(modelName)) &&
      (!date || row.date === date) &&
      (!year || rowYear === year) &&
      (!month || rowMonth === month) &&
      (!pinnedDay || (row.shiftDate || row.date) === pinnedDay) &&
      (!shift || row.shift === shift) &&
      (!task || (row.taskName || '').toLowerCase().includes(task)) &&
      (!cls || (row.className || '').toLowerCase().includes(cls)) &&
      (!result || row.result === result) &&
      (!batch || (row.batch || '').toLowerCase().includes(batch));
  });
}

// One row per VIN scan (inspection), tasks nested underneath -- matches
// receiver.py's grouping so a multi-task VIN shows as one expandable row.
function groupRowsByInspection(rows) {
  const groups = {}; const order = [];
  rows.forEach(r => {
    const key = `${r.batch}|${r.inspectionId ?? ''}|${r.vin}|${r.date}|${r.time}`;
    if (!groups[key]) {
      groups[key] = { key, vin: r.vin, modelCode: r.modelCode, modelName: r.modelName,
                      date: r.date, time: r.time, shift: r.shift, shiftDate: r.shiftDate, batch: r.batch, tasks: [] };
      order.push(key);
    }
    groups[key].tasks.push(r);
  });
  return order.map(k => groups[k]);
}

const _expandedGroups = new Set();
function toggleGroup(key) {
  if (_expandedGroups.has(key)) _expandedGroups.delete(key); else _expandedGroups.add(key);
  renderTable();
}

function escapeAttr(s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;'); }

function renderTable() {
  const filtered = getFilteredRows();
  document.getElementById('filterCount').textContent = `${filtered.length} of ${viewerRows.length} row(s)`;
  renderCharts(filtered);

  const tbody = document.getElementById('viewerRows');
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">No rows match these filters.</td></tr>';
    return;
  }

  const groups = groupRowsByInspection(filtered);
  tbody.innerHTML = groups.map(g => {
    const okCount = g.tasks.filter(t => t.result === 'OK').length;
    const failCount = g.tasks.length - okCount;
    const expanded = _expandedGroups.has(g.key);
    const taskSummary = g.tasks.length === 1
      ? `<span class="${okCount ? 'task-count-ok' : 'task-count-fail'}">${g.tasks[0].result}</span>`
      : `<span class="task-count-ok">${okCount} OK</span>${failCount ? ` / <span class="task-count-fail">${failCount} NOT OK</span>` : ''} (${g.tasks.length} tasks)`;
    const vinResult = failCount === 0 ? 'PASS' : 'FAIL';

    const detailRows = g.tasks.map(t => `
      <tr>
        <td>${t.taskName || ''}</td>
        <td>${t.className || ''}</td>
        <td class="${t.result === 'OK' ? 'badge-ok' : 'badge-fail'}">${t.result || ''}</td>
        <td>${t.imageUrl ? `<button class="img-link" onclick="openLightbox('${t.imageUrl}')">View</button>` : '<button class="img-link" disabled>-</button>'}</td>
      </tr>`).join('');

    return `
      <tr class="group-row ${expanded ? 'expanded' : ''}" onclick="toggleGroup('${g.key.replace(/'/g, "\\\\'")}')">
        <td><span class="chevron">&#9656;</span></td>
        <td>${g.vin || ''}</td><td>${g.modelCode || ''}</td><td>${g.modelName || ''}</td>
        <td>${g.date || ''}</td><td>${g.time || ''}</td><td>${g.shift || ''}</td>
        <td>${taskSummary}</td>
        <td class="${vinResult === 'PASS' ? 'badge-ok' : 'badge-fail'}">${vinResult}</td>
        <td>${g.batch || ''}</td>
      </tr>
      <tr class="detail-row ${expanded ? 'open' : ''}" data-key="${escapeAttr(g.key)}">
        <td colspan="10">
          <table class="mini-table">
            <thead><tr><th>Task</th><th>Detected</th><th>Result</th><th>Image</th></tr></thead>
            <tbody>${detailRows}</tbody>
          </table>
        </td>
      </tr>`;
  }).join('');
}

// ── Pie charts (hand-drawn SVG, ported from receiver.py's dashboard) ────
function pieSvg(ok, fail, size) {
  size = size || 82;
  const total = ok + fail;
  const r = size / 2 - 4, cx = size / 2, cy = size / 2;
  if (total === 0) return `<svg width="${size}" height="${size}"><circle cx="${cx}" cy="${cy}" r="${r}" fill="#eee"/></svg>`;
  const p = ok / total;
  let slices;
  if (p >= 0.999) {
    slices = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#1f9d55"/>`;
  } else if (p <= 0.001) {
    slices = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#DC143C"/>`;
  } else {
    const toXY = (deg) => { const rad = (deg - 90) * Math.PI / 180; return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]; };
    const angle = p * 360;
    const [sx, sy] = toXY(0); const [ex, ey] = toXY(angle);
    const greenPath = `M${cx},${cy} L${sx},${sy} A${r},${r} 0 ${angle > 180 ? 1 : 0} 1 ${ex},${ey} Z`;
    const [sx2, sy2] = toXY(angle); const [ex2, ey2] = toXY(360);
    const redPath = `M${cx},${cy} L${sx2},${sy2} A${r},${r} 0 ${(360 - angle) > 180 ? 1 : 0} 1 ${ex2},${ey2} Z`;
    slices = `<path d="${greenPath}" fill="#1f9d55"/><path d="${redPath}" fill="#DC143C"/>`;
  }
  const pct = Math.round(p * 100);
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${slices}
    <circle cx="${cx}" cy="${cy}" r="${r * 0.55}" fill="white"/>
    <text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="12" font-weight="700" fill="#1c1f26">${pct}%</text></svg>`;
}

// sizeClass: 'lg' for the single headline totals (Overall Result, VIN
// Result), 'sm' for their per-shift breakdowns -- same information density,
// but visually smaller so the headline number reads as the "main" figure
// and the per-shift ones read as supporting detail underneath it.
const CHART_PIE_PX = { lg: 100, sm: 62, '': 82 };

function chartCard(label, ok, fail, section, value, okLabel, failLabel, sizeClass) {
  okLabel = okLabel || 'OK'; failLabel = failLabel || 'NOT OK';
  sizeClass = sizeClass || '';
  const sizeClassAttr = sizeClass ? ` chart-card-${sizeClass}` : '';
  const attrs = section
    ? ` data-section="${section}" data-value="${escapeAttr(value)}" class="chart-card${sizeClassAttr} clickable-chart" title="Click to filter to ${escapeAttr(label)}"`
    : ` class="chart-card${sizeClassAttr}"`;
  return `<div${attrs}>${pieSvg(ok, fail, CHART_PIE_PX[sizeClass])}
    <div class="chart-label" title="${label}">${label}</div>
    <div class="chart-meta">${ok} ${okLabel} / ${fail} ${failLabel}</div></div>`;
}

function bucketize(rows, keyFn) {
  const buckets = {};
  for (const r of rows) {
    const k = keyFn(r);
    if (k === null || k === undefined || k === '') continue;
    if (!buckets[k]) buckets[k] = { ok: 0, fail: 0 };
    if (r.result === 'OK') buckets[k].ok++; else buckets[k].fail++;
  }
  return buckets;
}

const MAX_PIE_BUCKETS = 12;
let pinnedDay = null;
let hiddenChartSections = new Set(JSON.parse(localStorage.getItem('viewerHiddenChartSections') || '[]'));
const CORE_CHART_TITLES = new Set(['Overall (current filters)', 'Overall Result by Shift', 'VIN Result (Pass/Fail)', 'VIN Result by Shift']);
let extrasVisible = localStorage.getItem('viewerChartExtrasVisible') === '1';

function hideChartSection(title) {
  hiddenChartSections.add(title);
  localStorage.setItem('viewerHiddenChartSections', JSON.stringify([...hiddenChartSections]));
  renderTable();
}
function showAllChartSections() {
  hiddenChartSections.clear();
  localStorage.setItem('viewerHiddenChartSections', '[]');
  extrasVisible = true;
  localStorage.setItem('viewerChartExtrasVisible', '1');
  renderTable();
}
function hideExtraChartSections() {
  extrasVisible = false;
  localStorage.setItem('viewerChartExtrasVisible', '0');
  renderTable();
}
function clearPinnedDay() { pinnedDay = null; renderTable(); }

// Which visual group a chart section belongs to -- used to draw one
// divider between groups (Overall | VIN Result | extras), not one before
// every single section.
function sectionFamily(title) {
  if (title.startsWith('Overall')) return 'overall';
  if (title.startsWith('VIN Result')) return 'vinresult';
  return 'extras'; // This VIN / By VIN / By Day / By Month
}

function renderCharts(filtered) {
  const el = document.getElementById('chartsPanel');
  if (!filtered.length) { el.innerHTML = ''; return; }

  const overallOk = filtered.filter(r => r.result === 'OK').length;
  const overallFail = filtered.length - overallOk;
  const sections = [];
  sections.push({ title: 'Overall (current filters)', cards: [chartCard('All Results', overallOk, overallFail, null, null, null, null, 'lg')] });

  const byShift = bucketize(filtered, r => r.shift);
  const shiftKeys = Object.keys(byShift).sort();
  if (shiftKeys.length) {
    sections.push({ title: 'Overall Result by Shift', cards: shiftKeys.map(k => chartCard('Shift ' + k, byShift[k].ok, byShift[k].fail, 'shift', k, null, null, 'sm')) });
  }

  const inspectionGroups = groupRowsByInspection(filtered);
  let vinPass = 0, vinFail = 0;
  inspectionGroups.forEach(g => { if (g.tasks.some(t => t.result !== 'OK')) vinFail++; else vinPass++; });
  sections.push({ title: 'VIN Result (Pass/Fail)', cards: [chartCard('All VIN Scans', vinPass, vinFail, null, null, 'Pass', 'Fail', 'lg')] });

  // Same VIN Pass/Fail-per-scan count as above, broken out per shift --
  // how many VINs passed/failed within Shift A vs. B vs. C.
  const vinResultByShift = {};
  inspectionGroups.forEach(g => {
    const shiftKey = g.shift || 'Unknown';
    if (!vinResultByShift[shiftKey]) vinResultByShift[shiftKey] = { pass: 0, fail: 0 };
    if (g.tasks.some(t => t.result !== 'OK')) vinResultByShift[shiftKey].fail++; else vinResultByShift[shiftKey].pass++;
  });
  const vinShiftKeys = Object.keys(vinResultByShift).sort();
  if (vinShiftKeys.length) {
    sections.push({
      title: 'VIN Result by Shift',
      cards: vinShiftKeys.map(k => chartCard('Shift ' + k, vinResultByShift[k].pass, vinResultByShift[k].fail, 'shift', k, 'Pass', 'Fail', 'sm')),
    });
  }

  const byVin = bucketize(filtered, r => r.vin);
  const vinKeys = Object.keys(byVin);
  if (vinKeys.length === 1) {
    sections.push({ title: 'This VIN', cards: [chartCard(vinKeys[0], byVin[vinKeys[0]].ok, byVin[vinKeys[0]].fail)] });
  } else if (vinKeys.length > 1 && vinKeys.length <= MAX_PIE_BUCKETS) {
    sections.push({ title: 'By VIN', cards: vinKeys.map(k => chartCard(k, byVin[k].ok, byVin[k].fail, 'vin', k)) });
  } else if (vinKeys.length > MAX_PIE_BUCKETS) {
    sections.push({ title: 'By VIN', note: `${vinKeys.length} VINs in view -- filter down to ${MAX_PIE_BUCKETS} or fewer to see per-VIN pies.` });
  }

  const byDay = bucketize(filtered, r => r.shiftDate || r.date);
  const dayKeys = Object.keys(byDay).sort();
  if (dayKeys.length && dayKeys.length <= MAX_PIE_BUCKETS) {
    sections.push({ title: 'By Day (shift-day)', cards: dayKeys.map(k => chartCard(k, byDay[k].ok, byDay[k].fail, 'day', k)) });
  } else if (dayKeys.length > MAX_PIE_BUCKETS) {
    sections.push({ title: 'By Day (shift-day)', note: `${dayKeys.length} days in view -- pick a Month filter to see day-wise pies.` });
  }

  const byMonth = bucketize(filtered, r => (r.date || '').slice(0, 7));
  const monthKeys = Object.keys(byMonth).sort();
  if (monthKeys.length && monthKeys.length <= MAX_PIE_BUCKETS) {
    sections.push({ title: 'By Month', cards: monthKeys.map(k => chartCard(k, byMonth[k].ok, byMonth[k].fail, 'month', k)) });
  } else if (monthKeys.length > MAX_PIE_BUCKETS) {
    sections.push({ title: 'By Month', note: `${monthKeys.length} months in view -- pick a Year filter to see month-wise pies.` });
  }

  const inPlay = extrasVisible ? sections : sections.filter(s => CORE_CHART_TITLES.has(s.title));
  const foldedCount = extrasVisible ? 0 : sections.length - inPlay.length;
  const visible = inPlay.filter(s => !hiddenChartSections.has(s.title));
  const hidden = inPlay.filter(s => hiddenChartSections.has(s.title));

  let html = '';
  if (hidden.length) {
    html += `<div class="chart-hidden-bar">Hidden: ${hidden.map(s => s.title).join(', ')}
      <button class="secondary" onclick="showAllChartSections()">Show All</button></div>`;
  }
  if (!extrasVisible && foldedCount > 0) {
    html += `<div class="chart-hidden-bar">${foldedCount} more chart${foldedCount === 1 ? '' : 's'} available (By VIN, By Day, By Month...)
      <button class="secondary" onclick="showAllChartSections()">Show All Charts</button></div>`;
  } else if (extrasVisible) {
    html += `<div class="chart-hidden-bar"><button class="secondary" onclick="hideExtraChartSections()">Show Fewer</button></div>`;
  }
  if (pinnedDay) {
    html += `<div class="chart-hidden-bar">Pinned to day: <b>${pinnedDay}</b> <button class="secondary" onclick="clearPinnedDay()">Clear</button></div>`;
  }
  html += '<div class="charts-flow">' + visible.map((s, i) => {
    const family = sectionFamily(s.title);
    const prevFamily = i > 0 ? sectionFamily(visible[i - 1].title) : null;
    const groupStart = i > 0 && family !== prevFamily;
    return `
    <div class="chart-row${groupStart ? ' group-start' : ''}">
      <div class="chart-row-title">${s.title}
        <button class="chart-close" title="Hide this chart" onclick="hideChartSection('${s.title.replace(/'/g, "\\\\'")}')">&#10005;</button>
      </div>
      ${s.note ? `<div class="chart-note">${s.note}</div>` : `<div class="chart-cards">${s.cards.join('')}</div>`}
    </div>`;
  }).join('') + '</div>';
  el.innerHTML = html;

  el.querySelectorAll('.chart-card.clickable-chart').forEach(card => {
    card.addEventListener('click', () => {
      const section = card.dataset.section, value = card.dataset.value;
      if (section === 'shift') document.getElementById('fShift').value = value;
      else if (section === 'vin') document.getElementById('fVin').value = value;
      else if (section === 'month') { const [y, m] = value.split('-'); document.getElementById('fYear').value = y; document.getElementById('fMonth').value = m; }
      else if (section === 'day') pinnedDay = value;
      renderTable();
    });
  });
}

function openLightbox(url) {
  document.getElementById('lightboxImg').src = url;
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
  document.getElementById('lightboxImg').src = '';
}

async function downloadBlob(url, body, filename) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (res.status === 401) { window.location = '/login'; return; }
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

document.getElementById('groupSelect').addEventListener('change', loadData);
document.getElementById('downloadFullBtn').addEventListener('click', () => {
  window.location = '/download/excel?' + new URLSearchParams({ device: currentDevice, appName: currentAppName });
});
document.getElementById('downloadFilteredBtn').addEventListener('click', () => {
  const rows = getFilteredRows();
  downloadBlob('/download/excel-filtered', { rows, device: currentDevice, appName: currentAppName },
    `${currentDevice}_${currentAppName}_filtered.xlsx`);
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


def _pick_folder_dialog() -> str | None:
    """Opens a native OS folder-browser window on top of everything else --
    same approach receiver.py already uses for its own data-folder Settings.
    Typing a path by hand risks a typo that silently points the viewer at
    the wrong (or a nonexistent) folder; browsing to it removes that risk.
    Returns None if the user cancels."""
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askdirectory(title="Choose the receiver's data folder (e.g. its 'received_data' or S:\\... folder)")
    finally:
        root.destroy()
    return chosen or None


def _cli_set_data_dir(path_str: str | None):
    """Admin-only, one-time: permanently points this viewer at the
    receiver's data folder. Saved in viewer_config.json, so once set it's
    used on every future launch -- no env var needed, and nothing about
    this path is ever reachable through the web UI/shared link, since no
    route exists there to read or change it.

    Can be re-run any time to point at a different folder -- it always
    overwrites the previous value."""
    if path_str is None:
        path_str = _pick_folder_dialog()
        if not path_str:
            print("Cancelled -- data folder not changed.")
            sys.exit(0)
    path = Path(path_str)
    if not path.exists():
        print(f"'{path}' does not exist. Double-check the path (it should match "
              f"the data folder shown in the receiver's Settings screen).")
        sys.exit(1)
    if not path.is_dir():
        print(f"'{path}' is not a folder.")
        sys.exit(1)
    _config["dataDir"] = str(path.resolve())
    _save_config(_config)
    print(f"Viewer data folder set to: {path.resolve()}")
    print("This is saved permanently -- restart the viewer normally (no env var needed) to use it.")


if __name__ == "__main__":
    if "--set-password" in sys.argv:
        _cli_set_password()
        sys.exit(0)

    if "--set-data-dir" in sys.argv:
        idx = sys.argv.index("--set-data-dir")
        # With a path argument: use it directly (scripted setups).
        # Without one: open a native folder-browse window instead, so
        # there's no path to mistype.
        has_path_arg = idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("--")
        _cli_set_data_dir(sys.argv[idx + 1] if has_path_arg else None)
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
