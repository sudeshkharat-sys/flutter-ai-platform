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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mimetypes

import uvicorn
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from assets import FAVICON_PNG_BASE64, LOGO_PNG_BASE64


def _pause_briefly(seconds: int = 15):
    """Waits for a keypress or `seconds`, whichever comes first, before
    returning -- so someone who double-clicked the exe directly still has
    time to read whatever was just printed, but the process still exits
    on its own if nothing responds. A plain blocking input() would
    otherwise leave the watchdog's auto-restart loop (viewer_run_forever.bat)
    stuck waiting forever for a keypress no unattended process can ever
    provide -- the exe would look "stuck"/"not restarting", when it's
    actually just paused on this exact prompt."""
    print(f"(Closing automatically in {seconds}s -- press Enter to close sooner.)")
    try:
        import msvcrt
        end = time.time() + seconds
        while time.time() < end:
            if msvcrt.kbhit():
                msvcrt.getch()
                return
            time.sleep(0.1)
    except ImportError:
        # Not Windows (e.g. running from source during development) --
        # msvcrt doesn't exist, just wait out the same duration.
        time.sleep(seconds)
    except Exception:
        pass


def _pause_on_crash(exc_type, exc, tb):
    """Installed as sys.excepthook below. Double-clicking the packaged .exe
    opens a console window that Windows closes the instant this process
    exits -- whether that's a clean exit or an unhandled exception. Without
    this, any startup failure (unwritable folder, bad config, an unexpected
    error anywhere in this module, even before main() runs) flashes a
    traceback for a fraction of a second and vanishes, which looks exactly
    like the app "crashing" for no visible reason. Printing the traceback
    and pausing briefly keeps the window open long enough to actually read
    what happened, without blocking forever if this exe is running
    unattended under the auto-restart watchdog."""
    import traceback
    print("\n[fatal] Digital Eye Vault Viewer stopped unexpectedly:\n")
    traceback.print_exception(exc_type, exc, tb)
    _pause_briefly()


sys.excepthook = _pause_on_crash

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


def _ensure_app_dir_writable():
    """Proves APP_DIR (where viewer_config.json / FIRST_RUN_PASSWORD.txt
    live) is actually writable before anything tries to write to it --
    without this, dropping the exe in Program Files or a read-only network
    location made the very first config write throw an unguarded
    PermissionError/OSError at import time, before the app ever got a
    chance to explain what was wrong."""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        probe = APP_DIR / ".pcviewer_write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        # Plain sys.exit(message) would flash and vanish on a double-clicked
        # .exe (Windows closes the console the instant the process exits),
        # so print + pause directly instead of relying on sys.excepthook,
        # which SystemExit deliberately bypasses.
        print(
            f"Could not write to {APP_DIR} ({e})\n"
            f"This app needs to run from a folder this Windows account can write "
            f"to (e.g. Desktop, Documents, or a plain local folder) -- not "
            f"Program Files or a read-only network location."
        )
        _pause_briefly()
        sys.exit(1)


_ensure_app_dir_writable()
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
MAX_ROWS_RETURNED = 30000  # keeps very large datasets from freezing the browser

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

# Incremental cache for flattened rows, keyed by device_dir path -- see
# receiver.py's identical cache for why: batch folders are immutable once
# written, so a batch already parsed never needs re-reading. Without this,
# every /api/data call re-read and re-JSON-parsed every manifest.json on
# disk, which got slow once a plant's data grew into the thousands of sends.
_rows_cache_lock = threading.Lock()
_rows_cache: dict[str, dict] = {}  # str(device_dir) -> {"batches": set[str], "rows": list[dict], "seen_keys": set}


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

# Unlike receiver.py (localhost-only, so no real network bottleneck), this
# app is fetched over actual WiFi -- /api/app-data's uncompressed JSON can
# run several MB on a data-heavy app, and that's genuinely bandwidth-bound
# on WiFi even though it's instant over loopback. JSON compresses extremely
# well (typically 80-90% smaller), so gzip-ing responses directly cuts the
# transfer time that's slow on a remote laptop but fine on the server itself.
app.add_middleware(GZipMiddleware, minimum_size=500)


# ── Data helpers (read-only; mirrors receiver.py's own flattening logic so
#    the Excel columns/shape match what people already see from receiver's
#    Vault tab) ───────────────────────────────────────────────────────────

EXCEL_COLUMNS = [
    ("VIN", "vin"),
    ("Model Code", "modelCode"),
    ("Model Name", "modelName"),
    ("Model Variant", "modelVariant"),
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


def _app_master_dir(app_name: str) -> Path:
    """Where an app's aggregated master workbook lives -- must match
    receiver.py's _app_master_dir exactly, since this viewer reads the
    same DATA_DIR receiver.py writes to (see this module's docstring).
    Kept under a folder prefix that can't collide with a real phone name."""
    return DATA_DIR / "_master" / _safe_name(app_name)


def _list_groups() -> list[dict]:
    """Every device/app folder pair under DATA_DIR, with basic stats.
    Derived purely from the folder layout -- unlike receiver.py's device
    list, this doesn't depend on paired_devices.json, so it still shows
    historical data for a device that has since been un-paired.

    "hasExcel" reflects the app-wide master workbook (shared across every
    device paired under that app -- see receiver.py's _app_master_dir),
    not a per-device file: receiver.py stopped writing a separate
    data.xlsx per device once every phone under the same app started
    sharing one continuous workbook, so checking the old per-device path
    here would report "no data" for anything received after that change."""
    groups = []
    if not DATA_DIR.exists():
        return groups
    for device_dir in sorted(DATA_DIR.iterdir()):
        if not device_dir.is_dir() or device_dir.name == "_master":
            continue
        for app_dir in sorted(device_dir.iterdir()):
            if not app_dir.is_dir():
                continue
            batch_dirs = sorted([p for p in app_dir.iterdir() if p.is_dir()])
            xlsx_path = _app_master_dir(app_dir.name) / "data.xlsx"
            _, total_bytes = _dir_stats(app_dir)
            groups.append({
                "device": device_dir.name,
                "appName": app_dir.name,
                "batchCount": len(batch_dirs),
                "totalBytes": total_bytes,
                "lastReceivedAt": batch_dirs[-1].name if batch_dirs else None,
                "hasExcel": xlsx_path.exists(),
            })
    groups.sort(key=lambda g: g["lastReceivedAt"] or "", reverse=True)
    return groups


def _dedup_key(row: dict) -> tuple:
    """A physical scan event's natural identity -- stable across re-sends
    of the same data, unlike the phone-local inspection id (which resets
    to 1 after an app reinstall, so it can collide with an unrelated
    inspection from a different install)."""
    return (row.get("vin"), row.get("date"), row.get("time"), row.get("taskName"))


def _flatten_rows(device: str, app_name: str) -> list[dict]:
    device_dir = DATA_DIR / _safe_name(device) / _safe_name(app_name)
    if not device_dir.exists():
        return []

    cache_key = str(device_dir)
    batch_dirs = sorted(p for p in device_dir.iterdir() if p.is_dir())

    with _rows_cache_lock:
        entry = _rows_cache.get(cache_key)
        if entry is None:
            entry = {"batches": set(), "rows": [], "seen_keys": set()}
            _rows_cache[cache_key] = entry

        new_batch_dirs = [b for b in batch_dirs if b.name not in entry["batches"]]
        if not new_batch_dirs:
            return list(entry["rows"])

        for batch_dir in new_batch_dirs:
            manifest_path = batch_dir / "manifest.json"
            entry["batches"].add(batch_dir.name)
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
                    row = {
                        "device": device,
                        "appName": app_name,
                        "batch": batch_dir.name,
                        "inspectionId": insp.get("inspectionId"),
                        "vin": insp.get("vin"),
                        "modelCode": insp.get("modelCode"),
                        "modelName": insp.get("modelName"),
                        "modelVariant": insp.get("modelVariant"),
                        "date": insp.get("date"),
                        "time": insp.get("time"),
                        "shift": insp.get("shift"),
                        "shiftDate": insp.get("shiftDate") or insp.get("date"),
                        "taskName": task.get("taskName"),
                        "className": task.get("className"),
                        "result": "OK" if task.get("success") else "NOT OK",
                        "imageUrl": _image_url(task.get("imagePath")),
                    }
                    # A phone can legitimately re-send data it already sent
                    # before (a forced "Resync All", a retried upload,
                    # re-pairing after a reinstall) -- each send lands in its
                    # own batch folder, so without this the same inspection
                    # would show up once per batch it was sent in.
                    key = _dedup_key(row)
                    if key in entry["seen_keys"]:
                        continue
                    entry["seen_keys"].add(key)
                    entry["rows"].append(row)

        entry["rows"].sort(key=lambda r: (r["date"] or "", r["time"] or ""), reverse=True)
        return list(entry["rows"])


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


def _device_dirs_for_app(app_name_safe: str) -> list[str]:
    """Every device's safe folder name that has data for this exact app
    name. Unlike receiver.py's equivalent, this has no alias/merge concept
    of its own -- it's derived purely from the folder layout (same as
    _list_groups), so it always reflects whatever receiver.py's own Apps
    tab has already merged on disk structure, nothing more."""
    devices = []
    if not DATA_DIR.exists():
        return devices
    for device_dir in DATA_DIR.iterdir():
        if not device_dir.is_dir() or device_dir.name == "_master":
            continue
        if (device_dir / app_name_safe).exists():
            devices.append(device_dir.name)
    return devices


@app.get("/api/apps")
async def api_apps(_: None = Depends(_require_session)):
    """Same idea as /api/groups, but grouped by app instead of by device --
    lets the Apps tab show one continuous dataset per app instead of
    switching between each device paired under it."""
    by_app: dict[str, dict] = {}
    for g in _list_groups():
        safe = _safe_name(g["appName"])
        bucket = by_app.setdefault(safe, {
            "appName": g["appName"], "appNameSafe": safe,
            "deviceCount": 0, "batchCount": 0, "totalBytes": 0, "lastReceivedAt": None, "hasExcel": g["hasExcel"],
        })
        bucket["deviceCount"] += 1
        bucket["batchCount"] += g["batchCount"]
        bucket["totalBytes"] += g["totalBytes"]
        bucket["hasExcel"] = bucket["hasExcel"] or g["hasExcel"]
        if g["lastReceivedAt"] and (not bucket["lastReceivedAt"] or g["lastReceivedAt"] > bucket["lastReceivedAt"]):
            bucket["lastReceivedAt"] = g["lastReceivedAt"]
    result = list(by_app.values())
    result.sort(key=lambda a: a["lastReceivedAt"] or "", reverse=True)
    return result


def _dir_stats(path: Path):
    if not path.exists():
        return 0, 0
    files = [f for f in path.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


@app.get("/api/storage")
async def api_storage(_: None = Depends(_require_session)):
    """Read-only size summary -- how much data each app/device has on disk,
    mirroring receiver.py's Vault tab but with no delete capability, since
    this dashboard is view-only."""
    tree = {}
    if not DATA_DIR.exists():
        return tree
    for device_dir in sorted(DATA_DIR.iterdir()):
        if not device_dir.is_dir() or device_dir.name == "_master":
            continue
        apps = {}
        for app_dir in sorted(device_dir.iterdir()):
            if not app_dir.is_dir():
                continue
            batches = []
            for batch_dir in sorted(app_dir.iterdir(), reverse=True):
                if not batch_dir.is_dir():
                    continue
                file_count, size = _dir_stats(batch_dir)
                batches.append({"batch": batch_dir.name, "fileCount": file_count, "sizeBytes": size})
            if batches:
                apps[app_dir.name] = batches
        if apps:
            tree[device_dir.name] = apps
    return tree


@app.get("/api/app-data")
async def api_app_data(appName: str, startDate: str = "", endDate: str = "", _: None = Depends(_require_session)):
    """Every row for this app, merged across every device that has data
    for it -- the Apps-tab equivalent of /api/data.

    startDate/endDate (both "YYYY-MM-DD", inclusive) let the caller scope
    this to a date range instead of pulling the app's entire history every
    time. The landing view asks for just today+yesterday by default (fast
    regardless of how much history exists); picking an older date in the
    UI re-requests this with that date as both bounds."""
    app_name_safe = _safe_name(appName)
    device_dirs = _device_dirs_for_app(app_name_safe)
    rows: list[dict] = []
    # Each device's manifest.json files are read from disk independently, so
    # fanning the (I/O-bound, uncached-batches-only thanks to _rows_cache)
    # reads out across a small thread pool cuts wall-clock time roughly
    # proportional to device count instead of paying for every device's
    # read serially -- the main source of the "takes too long to load"
    # first-open delay when an app has many paired devices.
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(device_dirs)))) as pool:
        for device_rows in pool.map(lambda d: _flatten_rows(d, app_name_safe), device_dirs):
            rows.extend(device_rows)
    if startDate or endDate:
        rows = [
            r for r in rows
            if (not startDate or (r["date"] or "") >= startDate)
            and (not endDate or (r["date"] or "") <= endDate)
        ]
    rows.sort(key=lambda r: (r["date"] or "", r["time"] or ""), reverse=True)
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
async def download_master_excel(appName: str, device: str = "", _: None = Depends(_require_session)):
    """The receiver's own running data.xlsx for this app -- always current
    as of the last successful upload from any device paired under it,
    downloaded as-is. [device] is accepted (unused for the file lookup) so
    the existing per-device "Download Excel" links/buttons keep working
    without needing their own change -- see receiver.py's equivalent
    /api/export/master-excel route for why this is now app-wide rather
    than per-device."""
    xlsx_path = _app_master_dir(appName) / "data.xlsx"
    if not xlsx_path.exists():
        raise HTTPException(status_code=404, detail="No data received yet")
    return FileResponse(xlsx_path, filename=f"{_safe_name(appName)}.xlsx")


@app.get("/download/excel-month")
async def download_month_excel(appName: str, year: str, month: str, _: None = Depends(_require_session)):
    """Same rows as /download/excel (the app's whole history) but scoped to
    one calendar month -- offered alongside the full download so a
    data-heavy app doesn't force everyone into downloading its entire
    history just to check one month. Built fresh from the flattened rows
    (not MAX_ROWS_RETURNED-limited -- this is an explicit narrower export,
    not the on-screen table) rather than reading data.xlsx, since that file
    is the unfiltered whole-history export."""
    if not (len(year) == 4 and year.isdigit() and len(month) == 2 and month.isdigit()):
        raise HTTPException(status_code=422, detail="year must be YYYY and month must be MM")
    app_name_safe = _safe_name(appName)
    device_dirs = _device_dirs_for_app(app_name_safe)
    prefix = f"{year}-{month}"
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(device_dirs)))) as pool:
        for device_rows in pool.map(lambda d: _flatten_rows(d, app_name_safe), device_dirs):
            rows.extend(r for r in device_rows if (r["date"] or "").startswith(prefix))
    rows.sort(key=lambda r: (r["date"] or "", r["time"] or ""), reverse=True)
    buf = _build_workbook(rows)
    filename = f"{app_name_safe}_{year}-{month}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
  header { background: var(--navy); color: #fff; padding: 6px 24px; display: flex; align-items: center; gap: 14px;
           box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
  header img { height: 68px; }
  /* Bright chrome/silver text -- sharp white-to-white bands with a single
     dark "reflection" line through the middle, not a flat grey wash, so it
     actually reads as shiny metal instead of dull grey on the dark bar. */
  header .titles h1 {
    margin: 0; font-size: 18px; font-weight: 800;
    background: linear-gradient(180deg, #ffffff 0%, #ffffff 32%, #9a9a9a 47%, #6b6b6b 52%, #d0d0d0 62%, #ffffff 78%, #ffffff 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent; color: transparent;
    filter: drop-shadow(0 1px 2px rgba(0,0,0,0.6));
  }
  header .titles p { margin: 2px 0 0; font-size: 11px; color: #9aa0ad; }
  header .spacer { flex: 1; }
  header a { color: #cfd3db; font-size: 12px; text-decoration: none; }
  header a:hover { color: #fff; }

  /* Full width, edge to edge -- matches receiver.py's own data-viewer
     overlay, which has no max-width at all. */
  main { padding: 20px 24px 60px; width: 100%; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 14px; }
  #truncatedBanner { background: #fff4e5; border: 1px solid #f0c987; color: #8a5a00; font-size: 12px; font-weight: 600;
                      padding: 8px 14px; border-radius: 8px; margin-bottom: 12px; }
  #rangeBanner { background: #eef4ff; border: 1px solid #c7d9f7; color: #2a4d8f; font-size: 12px; font-weight: 600;
                 padding: 8px 14px; border-radius: 8px; margin-bottom: 12px; }
  #sizeWarnBanner { background: #fff4e5; border: 1px solid #f0c987; color: #8a5a00; font-size: 12px; font-weight: 600;
                     padding: 8px 14px; border-radius: 8px; margin-bottom: 12px; }

  /* Vault: read-only size summary, mirrors receiver.py's accordion -- no
     delete action anywhere here, this dashboard only ever views data.
     Not linked from the UI yet (kept dormant for now). */
  .accordion { border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; overflow: hidden; }
  .accordion .head { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px;
                      background: #fff; cursor: pointer; font-weight: 700; }
  .accordion .body { display: none; padding: 4px 16px 14px; background: #fbfbfd; }
  .app-group { padding: 8px 0; border-top: 1px solid var(--border); }
  .app-group:first-child { border-top: none; }
  .app-name { font-weight: 700; font-size: 13px; margin-bottom: 6px; }
  .batch-row { display: flex; align-items: center; gap: 12px; padding: 4px 0; font-size: 12.5px; color: var(--muted); }
  .batch-row .b-name { color: var(--text); font-weight: 600; min-width: 140px; }
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

  /* App list -- the landing view, matching receiver.py's own Apps tab:
     just app name + stats + a "View Data" button, nothing about devices. */
  .app-row { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: 1px solid var(--border);
             border-radius: 8px; margin-bottom: 8px; background: var(--card); }
  .app-row:last-child { margin-bottom: 0; }
  .app-row .a-info { flex: 1; min-width: 0; }
  .app-row .a-name { font-weight: 600; font-size: 13px; }
  .app-row .a-meta { font-size: 11px; color: var(--muted); margin-top: 1px; }
  .app-row .a-stats { display: flex; gap: 16px; font-size: 11px; color: var(--muted); text-align: center; }
  .app-row .a-stats b { display: block; font-size: 13px; color: var(--text); }

  /* Data view header -- matches receiver.py's viewer-page header (Back
     button + title + download actions) instead of a toolbar bolted onto
     the app-shell layout. */
  .data-header { display: flex; align-items: center; gap: 16px; margin-bottom: 16px; }
  .data-header h2 { margin: 0; font-size: 17px; }
  .data-header p { margin: 2px 0 0; font-size: 12px; color: var(--muted); }

  /* Blinking-logo loader shown while a data fetch is in flight, in place of
     a generic spinner -- the actual app logo (same asset as the header),
     centered dead-center on screen and "blinking" (open/close eye) while
     data loads. */
  .eye-loader-overlay { display: none; position: fixed; inset: 0; align-items: center;
                         justify-content: center; flex-direction: column; gap: 16px;
                         background: var(--bg); z-index: 50; }
  .eye-loader-overlay.show { display: flex; }
  .eye-loader-overlay .msg { font-size: 13px; color: var(--muted); font-weight: 600; }
  .eye-loader-overlay img { width: 84px; height: 84px; object-fit: contain;
                             animation: eyeBlink 1.6s ease-in-out infinite; }
  @keyframes eyeBlink {
    0%, 35% { transform: scaleY(1); }
    50% { transform: scaleY(0.08); }
    65%, 100% { transform: scaleY(1); }
  }
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
  <div id="appsListView">
    <div class="toolbar">
      <h2 style="margin:0;">Apps</h2>
    </div>
    <div id="appsList"><div class="empty">Loading...</div></div>
  </div>

  <div id="dataView" style="display:none;">
  <div class="data-header">
    <button class="secondary" onclick="closeDataViewer()">&larr; Back</button>
    <div>
      <h2 id="dataViewTitle">App Data</h2>
      <p id="dataViewSubtitle"></p>
    </div>
    <span style="flex:1"></span>
    <select id="dlYear"></select>
    <select id="dlMonth">
      <option value="01">Jan</option><option value="02">Feb</option><option value="03">Mar</option>
      <option value="04">Apr</option><option value="05">May</option><option value="06">Jun</option>
      <option value="07">Jul</option><option value="08">Aug</option><option value="09">Sep</option>
      <option value="10">Oct</option><option value="11">Nov</option><option value="12">Dec</option>
    </select>
    <button class="secondary" id="downloadMonthBtn">Download Month</button>
    <button class="secondary" id="downloadFullBtn">Download Full Excel</button>
    <button class="primary" id="downloadFilteredBtn">Download Filtered Excel</button>
  </div>
  <div id="sizeWarnBanner" style="display:none;"></div>
  <div id="truncatedBanner" style="display:none;"></div>
  <div id="rangeBanner" style="display:none;"></div>
  <div id="eyeLoader" class="eye-loader-overlay"><img src="data:image/png;base64,__LOGO_B64__" alt="loading"><div class="msg">Loading data...</div></div>

  <div id="dataContent">
  <div class="filters">
    <input id="fVin" placeholder="Filter VIN..." oninput="renderTable()">
    <input id="fModel" placeholder="Filter Model Code..." oninput="renderTable()">
    <input id="fModelName" placeholder="Filter Model Name..." oninput="renderTable()">
    <input id="fModelVariant" placeholder="Filter Variant..." oninput="renderTable()">
    <input id="fDate" type="date" title="Pick a date to load that day's data (today + yesterday load by default)" onchange="onDateFilterChange()">
    <button class="secondary" onclick="showRecentData()" title="Back to today + yesterday">Show Recent</button>
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
    <select id="fDevice" onchange="renderTable()">
      <option value="">All Devices</option>
    </select>
    <button class="secondary" onclick="clearFilters()">Clear Filters</button>
    <div class="filter-count" id="filterCount"></div>
  </div>

  <div class="charts-panel" id="chartsPanel"></div>

  <div class="card">
    <table class="data-table">
      <thead>
        <tr>
          <th></th><th>VIN</th><th>Model Code</th><th>Model Name</th><th>Variant</th><th>Date</th><th>Time</th>
          <th>Shift</th><th>Tasks</th><th>Result</th><th>Batch</th><th>Device</th>
        </tr>
      </thead>
      <tbody id="viewerRows"></tbody>
    </table>
  </div>
  </div>
  </div>

  <div id="vaultView" style="display:none;">
    <div class="toolbar">
      <h2 style="margin:0;">Vault</h2>
      <span style="flex:1"></span>
      <button class="secondary" onclick="loadStorage()">Refresh</button>
    </div>
    <div id="storageList"><div class="empty">Loading...</div></div>
  </div>
</main>

<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <button class="lb-close" onclick="closeLightbox()">&#10005;</button>
  <img id="lightboxImg" src="" alt="Inspection image">
</div>

<script>
let apps = [];
let viewerRows = [];
let currentDevice = '', currentAppName = '';
let currentAppTotalBytes = 0;
// Past this, "Download Full Excel" gets a heads-up nudging people toward
// "Download Month" instead -- a data-heavy app's full history export can
// itself become the same slow-download problem in a different shape.
const FULL_DOWNLOAD_WARN_BYTES = 50 * 1024 * 1024;

// Landing view: just the app list (name, device count, sends, size, a "View
// Data" button) -- same shape as receiver.py's own Apps tab. No device
// picker, no per-device browsing here; the table's own Device column is
// what tells you which phone a row came from.
async function loadApps() {
  try {
    const res = await fetch('/api/apps');
    if (res.status === 401) { window.location = '/login'; return; }
    apps = await res.json();
    renderApps();
  } catch (e) {
    document.getElementById('appsList').innerHTML = '<div class="empty">Could not load apps.</div>';
  }
}

function renderApps() {
  const el = document.getElementById('appsList');
  if (!apps.length) {
    el.innerHTML = '<div class="empty">No data received yet.</div>';
    return;
  }
  el.innerHTML = apps.map(a => `
    <div class="app-row">
      <div class="a-info">
        <div class="a-name">${a.appName}</div>
        <div class="a-meta">${a.deviceCount} device${a.deviceCount === 1 ? '' : 's'} paired</div>
      </div>
      <div class="a-stats">
        <div><b>${a.batchCount}</b>sends</div>
        <div><b>${fmtBytes(a.totalBytes)}</b>size</div>
      </div>
      <button class="primary" onclick="openAppDataViewer('${a.appNameSafe.replace(/'/g, "\\'")}', '${a.appName.replace(/'/g, "\\'")}')">View Data</button>
    </div>
  `).join('');
}

// Bumped on every openAppDataViewer call so a slow, superseded fetch can
// tell it's no longer the latest request and drop its response instead of
// overwriting the screen with stale data from an app the user already
// clicked away from (this is what caused the "old data" flashes -- two
// fetches in flight, and whichever happened to resolve last used to win
// regardless of which one the user actually asked for last).
let dataRequestSeq = 0;

// Local (not UTC) YYYY-MM-DD, matching the format row.date already comes
// in as (see shiftGroupDate below) and what <input type="date"> uses.
function localISO(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function todayISO() { return localISO(new Date()); }
function yesterdayISO() { const d = new Date(); d.setDate(d.getDate() - 1); return localISO(d); }

function populateDownloadMonthYears() {
  const now = new Date();
  const sel = document.getElementById('dlYear');
  const thisYear = now.getFullYear();
  const years = [thisYear, thisYear - 1, thisYear - 2, thisYear - 3, thisYear - 4];
  sel.innerHTML = years.map(y => `<option value="${y}">${y}</option>`).join('');
  sel.value = String(thisYear);
  document.getElementById('dlMonth').value = String(now.getMonth() + 1).padStart(2, '0');
}

async function openAppDataViewer(appNameSafe, appNameDisplay) {
  currentDevice = 'AllDevices';
  currentAppName = appNameDisplay;
  currentAppTotalBytes = (apps.find(a => a.appName === appNameDisplay) || {}).totalBytes || 0;
  document.getElementById('appsListView').style.display = 'none';
  document.getElementById('dataView').style.display = '';
  document.getElementById('dataViewTitle').textContent = appNameDisplay;
  document.getElementById('fDate').value = '';
  populateDownloadMonthYears();
  const warnBanner = document.getElementById('sizeWarnBanner');
  if (currentAppTotalBytes > FULL_DOWNLOAD_WARN_BYTES) {
    warnBanner.style.display = 'block';
    warnBanner.textContent = `This app has ${fmtBytes(currentAppTotalBytes)} of data -- "Download Full Excel" may take a while. Prefer "Download Month" for a specific month where possible.`;
  } else {
    warnBanner.style.display = 'none';
  }
  // Landing view only pulls today + yesterday -- fast regardless of how
  // much history the app has piled up. Pick an older date (below) to load
  // that day specifically; "Show Recent" comes back to this.
  await loadAppData(yesterdayISO(), todayISO(), 'today and yesterday');
}

// [startDate, endDate] are "YYYY-MM-DD", inclusive, or '' for "no bound"
// (used by onDateFilterChange's "load everything up to this date" case is
// intentionally not offered -- every load here is bounded on both ends so
// a single date pick can never silently re-trigger a full-history fetch).
async function loadAppData(startDate, endDate, rangeLabel) {
  const seq = ++dataRequestSeq;
  pinnedDay = null;
  document.getElementById('dataViewSubtitle').textContent = 'Loading...';
  document.getElementById('viewerRows').innerHTML = '';
  document.getElementById('truncatedBanner').style.display = 'none';
  document.getElementById('dataContent').style.visibility = 'hidden';
  document.getElementById('eyeLoader').classList.add('show');
  let res, data;
  try {
    res = await fetch('/api/app-data?' + new URLSearchParams({ appName: currentAppName, startDate, endDate }));
    if (res.status === 401) { window.location = '/login'; return; }
    data = await res.json();
  } finally {
    if (seq === dataRequestSeq) document.getElementById('eyeLoader').classList.remove('show');
  }
  if (seq !== dataRequestSeq) return; // a newer request superseded this one while it was in flight
  document.getElementById('dataContent').style.visibility = '';
  viewerRows = data.rows;
  document.getElementById('dataViewSubtitle').textContent =
    `${viewerRows.length.toLocaleString()} task result(s) merged across every device paired under this app`;
  const truncBanner = document.getElementById('truncatedBanner');
  if (data.truncated) {
    truncBanner.style.display = 'block';
    truncBanner.textContent = `Showing the newest ${data.rows.length.toLocaleString()} of ${data.totalCount.toLocaleString()} total rows in this range -- narrow the filters, or download the full/monthly Excel below to get everything.`;
  } else {
    truncBanner.style.display = 'none';
  }
  const rangeBanner = document.getElementById('rangeBanner');
  rangeBanner.style.display = 'block';
  rangeBanner.textContent = `Showing ${rangeLabel} (${viewerRows.length.toLocaleString()} row(s)). Pick a date above to load an older day, or "Show Recent" to come back.`;
  populateYearOptions();
  clearFilters();
}

function onDateFilterChange() {
  const picked = document.getElementById('fDate').value;
  if (!picked) { showRecentData(); return; }
  loadAppData(picked, picked, `${picked}`);
}

function showRecentData() {
  document.getElementById('fDate').value = '';
  loadAppData(yesterdayISO(), todayISO(), 'today and yesterday');
}

function closeDataViewer() {
  document.getElementById('dataView').style.display = 'none';
  document.getElementById('appsListView').style.display = '';
}

function fmtBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

// Vault tab: how much is stored per device/app, read-only -- no delete
// action anywhere here, matching this dashboard being view-only.
async function loadStorage() {
  const el = document.getElementById('storageList');
  try {
    const r = await fetch('/api/storage');
    if (r.status === 401) { window.location = '/login'; return; }
    const tree = await r.json();
    const deviceNames = Object.keys(tree);
    if (!deviceNames.length) {
      el.innerHTML = '<div class="empty">No data received yet.</div>';
      return;
    }
    el.innerHTML = deviceNames.map(dev => `
      <div class="accordion">
        <div class="head" onclick="this.nextElementSibling.style.display = this.nextElementSibling.style.display === 'none' ? 'block' : 'none'">
          <span>${dev}</span>
          <span style="color:var(--muted);font-weight:400;">${Object.keys(tree[dev]).length} app(s)</span>
        </div>
        <div class="body">
          ${Object.keys(tree[dev]).map(appName => `
            <div class="app-group">
              <div class="app-name">${appName}</div>
              ${tree[dev][appName].map(b => `
                <div class="batch-row">
                  <span class="b-name">${b.batch}</span>
                  <span class="b-meta">${b.fileCount} files - ${fmtBytes(b.sizeBytes)}</span>
                </div>
              `).join('')}
            </div>
          `).join('')}
        </div>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty">Could not load storage.</div>';
  }
}

function populateYearOptions() {
  const years = [...new Set(viewerRows.map(r => (r.date || '').slice(0, 4)).filter(Boolean))].sort().reverse();
  const sel = document.getElementById('fYear');
  const current = sel.value;
  sel.innerHTML = '<option value="">All Years</option>' + years.map(y => `<option value="${y}">${y}</option>`).join('');
  sel.value = current;

  // Device dropdown: only meaningfully different from "All Devices" in the
  // merged Apps view, but harmless (a single option) in the per-device
  // Devices view too -- so it's always populated the same way.
  const devices = [...new Set(viewerRows.map(r => r.device).filter(Boolean))].sort();
  const deviceSel = document.getElementById('fDevice');
  const currentDeviceFilter = deviceSel.value;
  deviceSel.innerHTML = '<option value="">All Devices</option>' + devices.map(d => `<option value="${d}">${d}</option>`).join('');
  deviceSel.value = devices.includes(currentDeviceFilter) ? currentDeviceFilter : '';
}

function clearFilters() {
  // fDate is deliberately left alone here -- it doubles as "which day is
  // currently loaded" (see loadAppData/onDateFilterChange), so clearing
  // filters after a date-scoped load shouldn't blank out what's showing.
  ['fVin','fModel','fModelName','fModelVariant','fTask','fClass','fBatch'].forEach(id => document.getElementById(id).value = '');
  ['fYear','fMonth','fShift','fResult','fDevice'].forEach(id => document.getElementById(id).value = '');
  pinnedDay = null;
  renderTable();
}

// ── Filtering (client-side, mirrors receiver.py's own dashboard so every
//    filter reacts instantly against the rows already fetched) ──────────
// Date/Shift Date columns always show the row's real, unmodified date --
// but for grouping/filtering "by day", Shift C (00:00-07:00) is really the
// tail end of the previous day's shift (which started with that day's A/B),
// so it's counted against the previous day here without changing anything
// that's actually displayed or stored.
function shiftGroupDate(row) {
  if (row.shift !== 'C' || !row.date) return row.date;
  const [y, m, d] = row.date.split('-').map(Number);
  if (!y || !m || !d) return row.date;
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() - 1);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

function getFilteredRows() {
  const vin = document.getElementById('fVin').value.toLowerCase();
  const model = document.getElementById('fModel').value.toLowerCase();
  const modelName = document.getElementById('fModelName').value.toLowerCase();
  const modelVariant = document.getElementById('fModelVariant').value.toLowerCase();
  const date = document.getElementById('fDate').value;
  const year = document.getElementById('fYear').value;
  const month = document.getElementById('fMonth').value;
  const shift = document.getElementById('fShift').value;
  const task = document.getElementById('fTask').value.toLowerCase();
  const cls = document.getElementById('fClass').value.toLowerCase();
  const result = document.getElementById('fResult').value;
  const batch = document.getElementById('fBatch').value.toLowerCase();
  const device = document.getElementById('fDevice').value;

  return viewerRows.filter(row => {
    const rowGroupDate = shiftGroupDate(row) || '';
    const rowYear = rowGroupDate.slice(0, 4);
    const rowMonth = rowGroupDate.slice(5, 7);
    return (!vin || (row.vin || '').toLowerCase().includes(vin)) &&
      (!model || (row.modelCode || '').toLowerCase().includes(model)) &&
      (!modelName || (row.modelName || '').toLowerCase().includes(modelName)) &&
      (!modelVariant || (row.modelVariant || '').toLowerCase().includes(modelVariant)) &&
      (!date || shiftGroupDate(row) === date) &&
      (!year || rowYear === year) &&
      (!month || rowMonth === month) &&
      (!pinnedDay || shiftGroupDate(row) === pinnedDay) &&
      (!shift || row.shift === shift) &&
      (!task || (row.taskName || '').toLowerCase().includes(task)) &&
      (!cls || (row.className || '').toLowerCase().includes(cls)) &&
      (!result || row.result === result) &&
      (!batch || (row.batch || '').toLowerCase().includes(batch)) &&
      (!device || row.device === device);
  });
}

// One row per VIN scan (inspection), tasks nested underneath -- matches
// receiver.py's grouping so a multi-task VIN shows as one expandable row.
function groupRowsByInspection(rows) {
  const groups = {}; const order = [];
  rows.forEach(r => {
    const key = `${r.batch}|${r.inspectionId ?? ''}|${r.vin}|${r.date}|${r.time}`;
    if (!groups[key]) {
      groups[key] = { key, vin: r.vin, modelCode: r.modelCode, modelName: r.modelName, modelVariant: r.modelVariant,
                      date: r.date, time: r.time, shift: r.shift, shiftDate: r.shiftDate, batch: r.batch, device: r.device, tasks: [] };
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
    tbody.innerHTML = '<tr><td colspan="12" class="empty">No rows match these filters.</td></tr>';
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

    // Building this markup is the expensive part of a render (it's the
    // per-task breakdown, so its cost scales with total row count, not
    // group count) -- on first load and after every filter keystroke,
    // zero groups start expanded, so computing it for a collapsed group
    // is pure waste. Skip it until the group is actually opened.
    const detailRows = expanded ? g.tasks.map(t => `
      <tr>
        <td>${t.taskName || ''}</td>
        <td>${t.className || ''}</td>
        <td class="${t.result === 'OK' ? 'badge-ok' : 'badge-fail'}">${t.result || ''}</td>
        <td>${t.imageUrl ? `<button class="img-link" onclick="openLightbox('${t.imageUrl}')">View</button>` : '<button class="img-link" disabled>-</button>'}</td>
      </tr>`).join('') : '';

    return `
      <tr class="group-row ${expanded ? 'expanded' : ''}" onclick="toggleGroup('${g.key.replace(/'/g, "\\\\'")}')">
        <td><span class="chevron">&#9656;</span></td>
        <td>${g.vin || ''}</td><td>${g.modelCode || ''}</td><td>${g.modelName || ''}</td><td>${g.modelVariant || ''}</td>
        <td>${g.date || ''}</td><td>${g.time || ''}</td><td>${g.shift || ''}</td>
        <td>${taskSummary}</td>
        <td class="${vinResult === 'PASS' ? 'badge-ok' : 'badge-fail'}">${vinResult}</td>
        <td>${g.batch || ''}</td>
        <td>${g.device || ''}</td>
      </tr>
      <tr class="detail-row ${expanded ? 'open' : ''}" data-key="${escapeAttr(g.key)}">
        <td colspan="12">
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
  // Rounding to a whole percent can display "100%" (or "0%") even when
  // real failures (or passes) exist, once one side is a small enough
  // fraction of a large total -- e.g. 7891 OK / 18 NOT OK rounds to 100%.
  // Clamp away from the misleading extremes whenever the other side is
  // genuinely non-zero, so "100%" always means zero failures.
  let pct = Math.round(p * 100);
  if (fail > 0 && pct >= 100) pct = 99;
  if (ok > 0 && pct <= 0) pct = 1;
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
  // Exact percentage (one decimal), shown as text alongside the raw counts
  // -- the donut itself always shows a whole number clamped away from a
  // misleading 100%/0%, so this is where the real, unrounded figure lives.
  const total = ok + fail;
  const exactPct = total > 0 ? (ok / total * 100).toFixed(1) : '0.0';
  return `<div${attrs}>${pieSvg(ok, fail, CHART_PIE_PX[sizeClass])}
    <div class="chart-label" title="${label}">${label}</div>
    <div class="chart-meta">${ok} ${okLabel} / ${fail} ${failLabel} (${exactPct}%)</div></div>`;
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

  const byDay = bucketize(filtered, shiftGroupDate);
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

document.getElementById('downloadFullBtn').addEventListener('click', () => {
  window.location = '/download/excel?' + new URLSearchParams({ device: currentDevice, appName: currentAppName });
});
document.getElementById('downloadMonthBtn').addEventListener('click', () => {
  const year = document.getElementById('dlYear').value;
  const month = document.getElementById('dlMonth').value;
  window.location = '/download/excel-month?' + new URLSearchParams({ appName: currentAppName, year, month });
});
document.getElementById('downloadFilteredBtn').addEventListener('click', () => {
  const rows = getFilteredRows();
  downloadBlob('/download/excel-filtered', { rows, device: currentDevice, appName: currentAppName },
    `${currentDevice}_${currentAppName}_filtered.xlsx`);
});

loadApps();
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

    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, **ssl_kwargs)
    except OSError as e:
        # Most commonly a second copy of this exe already running and
        # holding the port -- without this, that crashed with a raw
        # traceback and (launched by double-click) the window closed
        # before anyone could read it, looking like the app just silently
        # refused to start at all.
        print(f"[error] Could not start on port {PORT}: {e}")
        print("        Check Task Manager for another DigitalEyeViewer.exe already running,")
        print(f"        or set VIEWER_PORT to a different port if something else on this PC uses {PORT}.")
        _pause_briefly()
        sys.exit(1)
