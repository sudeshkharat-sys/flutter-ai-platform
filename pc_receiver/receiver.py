"""
PC Receiver -- "Mahindra Digital Eye — Storage Bank"

Companion app for the "Send to PC" feature in generated Flutter AI Studio
apps. Run this on the PC. It:

  1. Serves a local web dashboard (opened automatically in your browser) for
     pairing new phones (QR code) and browsing received data -- the
     "Storage Bank" -- per phone and per app.
  2. Advertises itself on the local WiFi/hotspot via mDNS so phones can find
     it by name instead of typing an IP.
  3. Accepts uploads only from phones that completed the QR pairing
     handshake, verified via an HMAC signature -- any other device on the
     same WiFi is rejected with 401, even though it can see this PC.

The dashboard itself (and every /api/* route) only answers requests from
this PC (127.0.0.1) -- it is not exposed to the rest of the WiFi. Only
/pair and /upload, which phones need to reach and which are already
protected by the pairing handshake, listen on the LAN.

No installation needed when packaged with PyInstaller (see README.md) --
just double-click the resulting .exe.
"""

import base64
import hashlib
import hmac
import io
import ipaddress
import json
import secrets
import socket
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

import mimetypes

import qrcode
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from zeroconf import ServiceInfo, Zeroconf

from assets import LOGO_PNG_BASE64

# ── Paths (work both as a plain script and a PyInstaller --onefile exe) ────

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
DATA_DIR = APP_DIR / "received_data"
DEVICES_FILE = APP_DIR / "paired_devices.json"
DATA_DIR.mkdir(exist_ok=True)

PORT = 8765
SERVICE_TYPE = "_flutteraisync._tcp.local."
PAIRING_TOKEN_TTL = 300  # seconds
SIGNATURE_WINDOW = 300   # seconds -- rejects replayed requests older than this
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60

PC_NAME = socket.gethostname()

app = FastAPI()

_lock = threading.Lock()
_paired_devices: dict[str, dict] = {}          # deviceId -> {secret, deviceName, appName, pairedAt}
_pending_token: dict | None = None              # {token, expiresAt}
_pairing_results: dict[str, dict] = {}          # token -> {deviceId, deviceName, appName} once paired
_failed_attempts: dict[str, list[float]] = {}   # client ip -> [failure timestamps]
_recent_events: list[dict] = []                 # recent uploads, for the dashboard's live activity feed
_MAX_RECENT_EVENTS = 50


# ── Persistence ──────────────────────────────────────────────────────────

def _load_devices():
    global _paired_devices
    if DEVICES_FILE.exists():
        try:
            _paired_devices = json.loads(DEVICES_FILE.read_text())
        except Exception:
            _paired_devices = {}


def _save_devices():
    DEVICES_FILE.write_text(json.dumps(_paired_devices, indent=2))


def _safe_name(name: str) -> str:
    """Sanitize a phone/app-supplied name for use as a folder path segment."""
    cleaned = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
    cleaned = cleaned.replace(" ", "_")
    return cleaned or "unknown"


# ── Local IP discovery (for the QR payload / mDNS registration) ────────────

def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ── Access control: the dashboard/API is for this PC only ──────────────────

def _require_local(request: Request):
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Dashboard is only accessible from this PC")


# ── Pairing ──────────────────────────────────────────────────────────────

def _make_qr_base64(payload: dict) -> str:
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(json.dumps(payload))
    qr.make(fit=True)
    img = qr.make_image(fill_color="#151923", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@app.post("/api/pair/start")
async def api_pair_start(_: None = Depends(_require_local)):
    global _pending_token
    token = secrets.token_urlsafe(24)
    expires_at = time.time() + PAIRING_TOKEN_TTL
    with _lock:
        _pending_token = {"token": token, "expiresAt": expires_at}
    payload = {"name": PC_NAME, "ip": _local_ip(), "port": PORT, "token": token}
    return {"token": token, "qrImage": _make_qr_base64(payload), "expiresAt": expires_at}


@app.get("/api/pair/status")
async def api_pair_status(token: str, _: None = Depends(_require_local)):
    with _lock:
        result = _pairing_results.get(token)
    if result:
        return {"paired": True, **result}
    return {"paired": False}


@app.post("/pair")
async def pair(request: Request):
    global _pending_token
    body = await request.json()
    token = body.get("token")
    device_name = body.get("deviceName", "unknown-device")
    app_name = body.get("appName", "app")

    with _lock:
        pending = _pending_token
        if not pending or pending["token"] != token:
            raise HTTPException(status_code=400, detail="Invalid or already-used pairing token")
        if time.time() > pending["expiresAt"]:
            _pending_token = None
            raise HTTPException(status_code=400, detail="Pairing token expired -- generate a new QR code")

        # One-shot: consume the token so a screenshot can't be reused.
        _pending_token = None

        device_id = str(uuid.uuid4())
        secret = secrets.token_hex(32)
        _paired_devices[device_id] = {
            "secret": secret,
            "deviceName": device_name,
            "appName": app_name,
            "pairedAt": datetime.now().isoformat(),
        }
        _save_devices()
        _pairing_results[token] = {"deviceId": device_id, "deviceName": device_name, "appName": app_name}

    print(f"[paired] New device paired: {device_name} / {app_name} ({device_id})")
    return {"deviceId": device_id, "secret": secret}


# ── Upload ───────────────────────────────────────────────────────────────

def _client_locked_out(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < LOCKOUT_SECONDS]
    _failed_attempts[ip] = attempts
    return len(attempts) >= MAX_FAILED_ATTEMPTS


def _record_failure(ip: str):
    _failed_attempts.setdefault(ip, []).append(time.time())


@app.post("/upload")
async def upload(
    request: Request,
    x_device_id: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...),
):
    client_ip = request.client.host if request.client else "unknown"

    if _client_locked_out(client_ip):
        raise HTTPException(status_code=429, detail="Too many failed attempts, try again later")

    device = _paired_devices.get(x_device_id)
    if not device:
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Unknown device -- not paired with this PC")

    # Anti-replay: reject stale timestamps.
    try:
        ts = int(x_timestamp) / 1000.0
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad timestamp")
    if abs(time.time() - ts) > SIGNATURE_WINDOW:
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Request expired")

    form = await request.form()
    upload_file = form.get("data")
    if upload_file is None:
        raise HTTPException(status_code=400, detail="Missing file field 'data'")
    body_bytes = await upload_file.read()

    body_hash = hashlib.sha256(body_bytes).hexdigest()
    expected_payload = f"{x_device_id}:{x_timestamp}:{body_hash}"
    expected_sig = hmac.new(device["secret"].encode(), expected_payload.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_sig, x_signature):
        _record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Invalid signature")

    device_name = _safe_name(device["deviceName"])
    app_name = _safe_name(device.get("appName", "app"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # received_data/<phone name>/<app name>/<timestamp>/ -- keeps every
    # phone's data, and every generated app's data, in its own folder even
    # when many phones send to the same PC.
    device_dir = DATA_DIR / device_name / app_name
    device_dir.mkdir(parents=True, exist_ok=True)

    out_zip = device_dir / f"{stamp}.zip"
    out_zip.write_bytes(body_bytes)

    extract_dir = device_dir / stamp
    try:
        with zipfile.ZipFile(out_zip) as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Corrupt upload")

    print(f"[received] {len(body_bytes)} bytes from '{device['deviceName']}' / '{app_name}' -> {extract_dir}")

    with _lock:
        _recent_events.append({
            "deviceId": x_device_id,
            "deviceName": device["deviceName"],
            "appName": device.get("appName", "app"),
            "bytes": len(body_bytes),
            "receivedAtMs": int(time.time() * 1000),
        })
        del _recent_events[:-_MAX_RECENT_EVENTS]

    return {"status": "ok", "savedTo": str(extract_dir)}


# ── Dashboard API (localhost only) ──────────────────────────────────────

@app.get("/api/status")
async def api_status(_: None = Depends(_require_local)):
    return {"pcName": PC_NAME, "ip": _local_ip(), "port": PORT, "serverTimeMs": int(time.time() * 1000)}


@app.get("/api/events")
async def api_events(since: int = 0, _: None = Depends(_require_local)):
    """Uploads received after [since] (epoch ms) -- polled by the dashboard
    to trigger the "new data received" toast/animation without needing a
    full devices/storage refresh on every tick."""
    with _lock:
        events = [e for e in _recent_events if e["receivedAtMs"] > since]
    return {"events": events, "serverTimeMs": int(time.time() * 1000)}


def _dir_stats(path: Path):
    if not path.exists():
        return 0, 0
    files = [f for f in path.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


@app.get("/api/devices")
async def api_devices(_: None = Depends(_require_local)):
    result = []
    with _lock:
        items = list(_paired_devices.items())
    for device_id, d in items:
        device_dir = DATA_DIR / _safe_name(d["deviceName"]) / _safe_name(d.get("appName", "app"))
        batch_dirs = sorted([p for p in device_dir.iterdir() if p.is_dir()]) if device_dir.exists() else []
        file_count, total_bytes = _dir_stats(device_dir)
        result.append({
            "deviceId": device_id,
            "deviceName": d["deviceName"],
            "appName": d.get("appName", "app"),
            "pairedAt": d["pairedAt"],
            "batchCount": len(batch_dirs),
            "fileCount": file_count,
            "totalBytes": total_bytes,
            "lastReceivedAt": batch_dirs[-1].name if batch_dirs else None,
        })
    result.sort(key=lambda d: d["pairedAt"], reverse=True)
    return result


@app.delete("/api/devices/{device_id}")
async def api_remove_device(device_id: str, _: None = Depends(_require_local)):
    with _lock:
        if device_id in _paired_devices:
            del _paired_devices[device_id]
            _save_devices()
    return {"status": "ok"}


@app.get("/api/storage")
async def api_storage(_: None = Depends(_require_local)):
    tree = {}
    if not DATA_DIR.exists():
        return tree
    for device_dir in sorted(DATA_DIR.iterdir()):
        if not device_dir.is_dir():
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
                zip_path = app_dir / f"{batch_dir.name}.zip"
                batches.append({
                    "batch": batch_dir.name,
                    "fileCount": file_count,
                    "sizeBytes": size,
                    "downloadPath": f"{device_dir.name}/{app_dir.name}/{batch_dir.name}.zip" if zip_path.exists() else None,
                })
            if batches:
                apps[app_dir.name] = batches
        if apps:
            tree[device_dir.name] = apps
    return tree


@app.get("/api/storage/download")
async def api_storage_download(path: str, _: None = Depends(_require_local)):
    target = (DATA_DIR / path).resolve()
    try:
        target.relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target, filename=target.name)


def _resolve_under_data_dir(rel_path: str) -> Path:
    """Resolves a phone/app-relative path safely under DATA_DIR, or raises 404."""
    target = (DATA_DIR / rel_path).resolve()
    try:
        target.relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return target


@app.get("/api/device-data")
async def api_device_data(deviceId: str, _: None = Depends(_require_local)):
    """Every task-result row this device has ever sent, flattened for the
    data-viewer table -- one row per inspection task, with the image path
    already rewritten into a URL the browser can load directly."""
    with _lock:
        device = _paired_devices.get(deviceId)
    if not device:
        raise HTTPException(status_code=404, detail="Unknown device")

    device_name = _safe_name(device["deviceName"])
    app_name = _safe_name(device.get("appName", "app"))
    device_dir = DATA_DIR / device_name / app_name

    rows = []
    if device_dir.exists():
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

            def _image_url(rel):
                if not rel:
                    return None
                return f"/api/storage/image?path={device_name}/{app_name}/{batch_dir.name}/{rel}"

            for insp in inspections:
                tasks = insp.get("tasks") or []
                if not tasks:
                    # Inspection with no task rows (shouldn't normally happen) --
                    # still surface it as one row so it's not silently dropped.
                    tasks = [{}]
                for task in tasks:
                    rows.append({
                        "batch": batch_dir.name,
                        "vin": insp.get("vin"),
                        "modelCode": insp.get("modelCode"),
                        "date": insp.get("date"),
                        "time": insp.get("time"),
                        "shift": insp.get("shift"),
                        "taskName": task.get("taskName"),
                        "className": task.get("className"),
                        "result": "OK" if task.get("success") else "NOT OK",
                        "imageUrl": _image_url(task.get("imagePath")),
                        "backupImageUrl": _image_url(task.get("backupImagePath")),
                    })

    rows.sort(key=lambda r: (r["date"] or "", r["time"] or ""), reverse=True)
    return {"deviceName": device["deviceName"], "appName": device.get("appName", "app"), "rows": rows}


@app.get("/api/storage/image")
async def api_storage_image(path: str, _: None = Depends(_require_local)):
    target = _resolve_under_data_dir(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(content=target.read_bytes(), media_type=media_type)


@app.post("/api/export/excel")
async def api_export_excel(request: Request, _: None = Depends(_require_local)):
    body = await request.json()
    rows = body.get("rows") or []
    title = _safe_name(body.get("title", "storage_bank_export"))

    columns = [
        ("VIN", "vin"),
        ("Model Code", "modelCode"),
        ("Date", "date"),
        ("Time", "time"),
        ("Shift", "shift"),
        ("Task", "taskName"),
        ("Detected", "className"),
        ("Result", "result"),
        ("Batch", "batch"),
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append([label for label, _key in columns])
    for row in rows:
        ws.append([row.get(key, "") for _label, key in columns])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(len(rows) + 1, 1)}"
    for i, (label, _key) in enumerate(columns, start=1):
        width = max(12, len(label) + 4)
        ws.column_dimensions[get_column_letter(i)].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{title}.xlsx"'},
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(_: None = Depends(_require_local)):
    return DASHBOARD_HTML


# ── mDNS advertisement ──────────────────────────────────────────────────

def start_mdns():
    zeroconf = Zeroconf()
    ip = _local_ip()
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{PC_NAME}.{SERVICE_TYPE}",
        addresses=[ipaddress.IPv4Address(ip).packed],
        port=PORT,
        properties={"name": PC_NAME},
    )
    zeroconf.register_service(info)
    return zeroconf


# ── Dashboard HTML (self-contained, no external assets/CDN) ────────────────

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mahindra Digital Eye — Storage Bank</title>
<style>
  :root {
    --crimson: #DC143C;
    --crimson-dark: #B01030;
    --navy: #151923;
    --navy-light: #1f2430;
    --bg: #f7f7fa;
    --card: #ffffff;
    --border: #e6e6ec;
    --text: #1c1f26;
    --muted: #6b7280;
    --orange: #e0821e;
    --green: #1f9d55;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: var(--bg); color: var(--text); }
  header { background: var(--navy); color: #fff; padding: 14px 24px; display: flex; align-items: center; gap: 14px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
  header img { height: 42px; }
  header .titles h1 { margin: 0; font-size: 16px; letter-spacing: 0.5px; }
  header .titles p { margin: 2px 0 0; font-size: 11px; color: #9aa0ad; }
  header .status { margin-left: auto; text-align: right; font-size: 11px; color: #c9ccd4; }
  header .status b { color: #fff; }

  nav { display: flex; gap: 4px; padding: 12px 24px 0; background: var(--bg); }
  nav button { border: none; background: transparent; padding: 10px 18px; font-size: 13px; font-weight: 600; color: var(--muted); cursor: pointer; border-bottom: 3px solid transparent; }
  nav button.active { color: var(--crimson); border-bottom-color: var(--crimson); }

  main { padding: 20px 24px 40px; max-width: 980px; margin: 0 auto; }
  .tab { display: none; }
  .tab.active { display: block; }

  .toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .toolbar h2 { font-size: 15px; margin: 0; }
  button.primary { background: var(--crimson); color: #fff; border: none; padding: 10px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; }
  button.primary:hover { background: var(--crimson-dark); }
  button.ghost { background: transparent; border: 1px solid var(--border); color: var(--muted); padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; }
  button.ghost:hover { border-color: var(--crimson); color: var(--crimson); }
  button.icon-btn { background: transparent; border: 1px solid var(--border); color: var(--muted); width: 30px; height: 30px; border-radius: 50%; cursor: pointer; font-size: 13px; line-height: 1; }
  button.icon-btn:hover { border-color: var(--crimson); color: var(--crimson); }

  .empty { text-align: center; padding: 48px 12px; color: var(--muted); font-size: 13px; }

  /* Data viewer */
  .viewer-page { position: fixed; inset: 0; background: var(--bg); z-index: 40; display: none; flex-direction: column; }
  .viewer-page.open { display: flex; }
  .viewer-header { background: var(--navy); color: #fff; padding: 14px 24px; display: flex; align-items: center; gap: 14px; }
  .viewer-header h2 { margin: 0; font-size: 15px; }
  .viewer-header p { margin: 2px 0 0; font-size: 11px; color: #9aa0ad; }
  .viewer-header .spacer { flex: 1; }
  .filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 24px; background: var(--card); border-bottom: 1px solid var(--border); }
  .filters input, .filters select { padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }
  .filters input { width: 130px; }
  .filter-count { font-size: 11px; color: var(--muted); margin-left: auto; align-self: center; white-space: nowrap; }
  .table-wrap { flex: 1; overflow: auto; padding: 0 24px 24px; }
  table.data-table { width: 100%; border-collapse: collapse; background: var(--card); font-size: 12px; }
  table.data-table thead th { position: sticky; top: 0; background: #fafafc; border-bottom: 2px solid var(--border); padding: 10px 10px; text-align: left; white-space: nowrap; z-index: 2; }
  table.data-table tbody td { padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  table.data-table tbody tr:hover { background: #fbfbfd; }
  .badge-ok { color: var(--green); font-weight: 700; }
  .badge-fail { color: var(--crimson); font-weight: 700; }
  .img-link { color: var(--crimson); cursor: pointer; text-decoration: underline; font-size: 11px; background: none; border: none; padding: 0; }
  .img-link:disabled { color: var(--muted); text-decoration: none; cursor: default; }

  .lightbox { position: fixed; inset: 0; background: rgba(10,12,18,0.85); display: none; align-items: center; justify-content: center; z-index: 70; }
  .lightbox.open { display: flex; }
  .lightbox img { max-width: 90vw; max-height: 85vh; border-radius: 8px; }
  .lightbox .lb-close { position: absolute; top: 20px; right: 28px; color: #fff; font-size: 28px; cursor: pointer; background: none; border: none; }

  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; display: flex; align-items: center; gap: 14px; }
  .card .avatar { width: 42px; height: 42px; border-radius: 50%; background: #fdeaea; color: var(--crimson); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 15px; flex-shrink: 0; }
  .card .info { flex: 1; min-width: 0; }
  .card .info .name { font-weight: 700; font-size: 14px; }
  .card .info .meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .card .stats { display: flex; gap: 18px; font-size: 11px; color: var(--muted); text-align: center; }
  .card .stats b { display: block; font-size: 14px; color: var(--text); }

  /* Devices grouped by phone, each with one or more paired apps */
  .device-group { background: var(--card); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; overflow: hidden; }
  .device-group .dg-head { padding: 14px 16px; display: flex; align-items: center; gap: 14px; background: #fafafc; border-bottom: 1px solid var(--border); }
  .device-group .dg-head .name { font-weight: 700; font-size: 14px; }
  .device-group .dg-head .meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .device-group .dg-apps { padding: 10px 16px; }
  .app-row { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; transition: background 0.4s; }
  .app-row:last-child { margin-bottom: 0; }
  .app-row .a-name { font-weight: 600; font-size: 13px; }
  .app-row .a-meta { font-size: 11px; color: var(--muted); margin-top: 1px; }
  .app-row .a-info { flex: 1; min-width: 0; }
  .app-row .a-stats { display: flex; gap: 16px; font-size: 11px; color: var(--muted); text-align: center; }
  .app-row .a-stats b { display: block; font-size: 13px; color: var(--text); }
  .app-row.pulse { animation: rowPulse 1.8s ease-out; }
  @keyframes rowPulse {
    0%   { background: #ffeef0; box-shadow: 0 0 0 0 rgba(220,20,60,0.35); }
    100% { background: var(--card); box-shadow: 0 0 0 16px rgba(220,20,60,0); }
  }

  /* Header "receiving" pulse, flashed briefly on new uploads */
  .live-indicator { display: none; align-items: center; gap: 6px; font-size: 11px; color: #ffb4c2; margin-left: 18px; }
  .live-indicator.show { display: flex; }
  .live-indicator .dot { width: 8px; height: 8px; border-radius: 50%; background: #ff4d6d; animation: dotPulse 1s infinite; }
  @keyframes dotPulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.4); } }

  .accordion { background: var(--card); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; overflow: hidden; }
  .accordion > .head { padding: 12px 16px; font-weight: 700; font-size: 13px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; background: #fafafc; }
  .accordion > .body { padding: 4px 16px 12px; }
  .app-group { margin-top: 8px; }
  .app-group .app-name { font-size: 12px; font-weight: 700; color: var(--crimson); margin: 8px 0 6px; }
  .batch-row { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 6px; font-size: 12px; }
  .batch-row .b-name { font-weight: 600; flex: 1; }
  .batch-row .b-meta { color: var(--muted); }

  /* Modal */
  .overlay { position: fixed; inset: 0; background: rgba(10,12,18,0.55); display: none; align-items: center; justify-content: center; z-index: 50; }
  .overlay.open { display: flex; }
  .modal { background: #fff; border-radius: 14px; padding: 24px; width: 340px; text-align: center; }
  .modal h3 { margin: 0 0 6px; font-size: 15px; }
  .modal p { font-size: 12px; color: var(--muted); margin: 0 0 14px; }
  .modal img { width: 220px; height: 220px; border: 1px solid var(--border); border-radius: 8px; }
  .modal .waiting { margin-top: 14px; font-size: 12px; color: var(--muted); display: flex; align-items: center; justify-content: center; gap: 8px; }
  .spinner { width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: var(--crimson); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .modal .success { color: var(--green); font-weight: 700; margin-top: 14px; }
  .modal .close-btn { margin-top: 16px; }

  .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: var(--navy); color: #fff; padding: 10px 18px; border-radius: 8px; font-size: 12px; opacity: 0; pointer-events: none; transition: opacity 0.2s; z-index: 60; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<header>
  <img src="data:image/png;base64,__LOGO_B64__" alt="Mahindra Digital Eye">
  <div class="titles">
    <h1>DIGITAL EYE — STORAGE BANK</h1>
    <p>Receives inspection data from paired phones on this WiFi/hotspot</p>
  </div>
  <div class="live-indicator" id="liveIndicator"><span class="dot"></span>Receiving…</div>
  <div class="status" id="pcStatus">Loading…</div>
</header>

<nav>
  <button class="tab-btn active" data-tab="devices">Devices</button>
  <button class="tab-btn" data-tab="storage">Storage Bank</button>
</nav>

<main>
  <section id="tab-devices" class="tab active">
    <div class="toolbar">
      <h2>Paired Devices</h2>
      <button class="primary" onclick="openPairModal()">+ Add New Device</button>
    </div>
    <div id="devicesList"><div class="empty">Loading…</div></div>
  </section>

  <section id="tab-storage" class="tab">
    <div class="toolbar">
      <h2>Storage Bank</h2>
      <button class="ghost" onclick="loadStorage()">Refresh</button>
    </div>
    <div id="storageList"><div class="empty">Loading…</div></div>
  </section>
</main>

<div class="overlay" id="pairOverlay">
  <div class="modal">
    <h3>Pair a New Phone</h3>
    <p>Open the app → Send to PC → Pair New PC, and scan this code.</p>
    <img id="pairQrImg" src="" alt="QR code">
    <div class="waiting" id="pairWaiting"><div class="spinner"></div> Waiting for phone to scan…</div>
    <div class="success" id="pairSuccess" style="display:none;"></div>
    <div class="close-btn"><button class="ghost" onclick="closePairModal()">Close</button></div>
  </div>
</div>

<div class="viewer-page" id="viewerPage">
  <div class="viewer-header">
    <button class="ghost" style="border-color:#3a4152;color:#c9ccd4;" onclick="closeDataViewer()">← Back</button>
    <div>
      <h2 id="viewerTitle">Device Data</h2>
      <p id="viewerSubtitle"></p>
    </div>
    <div class="spacer"></div>
    <button class="primary" onclick="downloadExcel()">Download Excel (filtered)</button>
  </div>
  <div class="filters">
    <input id="fVin" placeholder="Filter VIN…" oninput="renderTable()">
    <input id="fModel" placeholder="Filter Model Code…" oninput="renderTable()">
    <input id="fDate" placeholder="Filter Date…" oninput="renderTable()">
    <select id="fShift" onchange="renderTable()">
      <option value="">All Shifts</option>
      <option value="A">Shift A</option>
      <option value="B">Shift B</option>
      <option value="C">Shift C</option>
    </select>
    <input id="fTask" placeholder="Filter Task…" oninput="renderTable()">
    <input id="fClass" placeholder="Filter Detected…" oninput="renderTable()">
    <select id="fResult" onchange="renderTable()">
      <option value="">All Results</option>
      <option value="OK">OK</option>
      <option value="NOT OK">NOT OK</option>
    </select>
    <input id="fBatch" placeholder="Filter Send/Batch…" oninput="renderTable()">
    <button class="ghost" onclick="clearFilters()">Clear Filters</button>
    <div class="filter-count" id="filterCount"></div>
  </div>
  <div class="table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th>VIN</th><th>Model Code</th><th>Date</th><th>Time</th><th>Shift</th>
          <th>Task</th><th>Detected</th><th>Result</th><th>Batch</th><th>Image</th>
        </tr>
      </thead>
      <tbody id="viewerRows"></tbody>
    </table>
  </div>
</div>

<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <button class="lb-close" onclick="closeLightbox()">✕</button>
  <img id="lightboxImg" src="" alt="Inspection image">
</div>

<div class="toast" id="toast"></div>

<script>
let pairPollTimer = null;
let currentToken = null;

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'storage') loadStorage();
  });
});

async function loadStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    document.getElementById('pcStatus').innerHTML = `<b>${d.pcName}</b><br>${d.ip}:${d.port}`;
  } catch (e) {}
}

function fmtBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

function initials(name) {
  return (name || '?').trim().slice(0, 2).toUpperCase();
}

// Batch folder names are "YYYYMMDD_HHMMSS" -- parse into a real Date so we
// can show/live-tick a relative "x minutes ago" instead of a raw timestamp.
function parseBatchTimestamp(name) {
  if (!name || name.length < 15) return null;
  const y = +name.slice(0, 4), mo = +name.slice(4, 6) - 1, d = +name.slice(6, 8);
  const h = +name.slice(9, 11), mi = +name.slice(11, 13), s = +name.slice(13, 15);
  const dt = new Date(y, mo, d, h, mi, s);
  return isNaN(dt.getTime()) ? null : dt;
}

function timeAgo(date) {
  if (!date) return 'no data received yet';
  const secs = Math.floor((Date.now() - date.getTime()) / 1000);
  if (secs < 5) return 'just now';
  if (secs < 60) return secs + 's ago';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ago';
  return Math.floor(hrs / 24) + 'd ago';
}

let lastDevicesData = [];

async function loadDevices() {
  try {
    const r = await fetch('/api/devices');
    lastDevicesData = await r.json();
    renderDevices();
  } catch (e) {
    document.getElementById('devicesList').innerHTML = '<div class="empty">Could not load devices.</div>';
  }
}

function renderDevices() {
  const el = document.getElementById('devicesList');
  if (!lastDevicesData.length) {
    el.innerHTML = '<div class="empty">No phones paired yet. Tap "+ Add New Device" to pair one.</div>';
    return;
  }

  // One phone can have several different generated apps paired separately
  // (e.g. an Engine Inspection app and a per-model app) -- group by phone
  // name so each physical device shows once, with its apps listed inside.
  const byPhone = {};
  for (const d of lastDevicesData) {
    (byPhone[d.deviceName] = byPhone[d.deviceName] || []).push(d);
  }

  el.innerHTML = Object.keys(byPhone).sort().map(phone => {
    const apps = byPhone[phone];
    const totalSends = apps.reduce((a, d) => a + d.batchCount, 0);
    const totalBytes = apps.reduce((a, d) => a + d.totalBytes, 0);
    return `
      <div class="device-group">
        <div class="dg-head">
          <div class="avatar">${initials(phone)}</div>
          <div class="info">
            <div class="name">${phone}</div>
            <div class="meta">${apps.length} app${apps.length === 1 ? '' : 's'} paired • ${totalSends} total send${totalSends === 1 ? '' : 's'} • ${fmtBytes(totalBytes)}</div>
          </div>
        </div>
        <div class="dg-apps">
          ${apps.map(d => `
            <div class="app-row" data-device-id="${d.deviceId}">
              <div class="a-info">
                <div class="a-name">${d.appName}</div>
                <div class="a-meta" data-last-received="${d.lastReceivedAt || ''}">Paired ${new Date(d.pairedAt).toLocaleDateString()} • Last received ${timeAgo(parseBatchTimestamp(d.lastReceivedAt))}</div>
              </div>
              <div class="a-stats">
                <div><b>${d.batchCount}</b>sends</div>
                <div><b>${fmtBytes(d.totalBytes)}</b>size</div>
              </div>
              <button class="primary" onclick="openDataViewer('${d.deviceId}')">View Data</button>
              <button class="icon-btn" title="Remove pairing" onclick="removeDevice('${d.deviceId}', '${phone} — ${d.appName}')">✕</button>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }).join('');
}

// Re-render every 20s from the already-fetched data so "Last received: Xm
// ago" keeps ticking without hitting the server again.
setInterval(renderDevices, 20000);

async function removeDevice(id, name) {
  if (!confirm(`Remove pairing for "${name}"? The phone will need to scan a new QR code to send data again.`)) return;
  await fetch('/api/devices/' + id, { method: 'DELETE' });
  showToast('Removed ' + name);
  loadDevices();
}

async function loadStorage() {
  const el = document.getElementById('storageList');
  try {
    const r = await fetch('/api/storage');
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
                  <span class="b-meta">${b.fileCount} files • ${fmtBytes(b.sizeBytes)}</span>
                  ${b.downloadPath ? `<button class="ghost" onclick="window.location='/api/storage/download?path=${encodeURIComponent(b.downloadPath)}'">Download</button>` : ''}
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

async function openPairModal() {
  document.getElementById('pairOverlay').classList.add('open');
  document.getElementById('pairWaiting').style.display = 'flex';
  document.getElementById('pairSuccess').style.display = 'none';
  document.getElementById('pairQrImg').src = '';
  try {
    const r = await fetch('/api/pair/start', { method: 'POST' });
    const d = await r.json();
    currentToken = d.token;
    document.getElementById('pairQrImg').src = 'data:image/png;base64,' + d.qrImage;
    pollPairStatus();
  } catch (e) {
    showToast('Could not start pairing');
  }
}

function pollPairStatus() {
  clearInterval(pairPollTimer);
  pairPollTimer = setInterval(async () => {
    if (!currentToken) return;
    const r = await fetch('/api/pair/status?token=' + encodeURIComponent(currentToken));
    const d = await r.json();
    if (d.paired) {
      clearInterval(pairPollTimer);
      document.getElementById('pairWaiting').style.display = 'none';
      const s = document.getElementById('pairSuccess');
      s.style.display = 'block';
      s.textContent = `✓ Paired with ${d.deviceName} (${d.appName})`;
      loadDevices();
    }
  }, 2000);
}

function closePairModal() {
  document.getElementById('pairOverlay').classList.remove('open');
  clearInterval(pairPollTimer);
  currentToken = null;
}

// ── Data viewer ──────────────────────────────────────────────────────────
let viewerRows = [];
let viewerDeviceLabel = '';

async function openDataViewer(deviceId) {
  document.getElementById('viewerPage').classList.add('open');
  document.getElementById('viewerTitle').textContent = 'Loading…';
  document.getElementById('viewerRows').innerHTML = '';
  try {
    const r = await fetch('/api/device-data?deviceId=' + encodeURIComponent(deviceId));
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    viewerRows = d.rows;
    viewerDeviceLabel = `${d.deviceName} — ${d.appName}`;
    document.getElementById('viewerTitle').textContent = viewerDeviceLabel;
    document.getElementById('viewerSubtitle').textContent = `${viewerRows.length} task result(s) across all sends`;
    clearFilters();
  } catch (e) {
    document.getElementById('viewerTitle').textContent = 'Could not load data';
  }
}

function closeDataViewer() {
  document.getElementById('viewerPage').classList.remove('open');
}

function clearFilters() {
  ['fVin', 'fModel', 'fDate', 'fTask', 'fClass', 'fBatch'].forEach(id => document.getElementById(id).value = '');
  ['fShift', 'fResult'].forEach(id => document.getElementById(id).value = '');
  renderTable();
}

function getFilteredRows() {
  const vin = document.getElementById('fVin').value.toLowerCase();
  const model = document.getElementById('fModel').value.toLowerCase();
  const date = document.getElementById('fDate').value.toLowerCase();
  const shift = document.getElementById('fShift').value;
  const task = document.getElementById('fTask').value.toLowerCase();
  const cls = document.getElementById('fClass').value.toLowerCase();
  const result = document.getElementById('fResult').value;
  const batch = document.getElementById('fBatch').value.toLowerCase();

  return viewerRows.filter(row =>
    (!vin || (row.vin || '').toLowerCase().includes(vin)) &&
    (!model || (row.modelCode || '').toLowerCase().includes(model)) &&
    (!date || (row.date || '').toLowerCase().includes(date)) &&
    (!shift || row.shift === shift) &&
    (!task || (row.taskName || '').toLowerCase().includes(task)) &&
    (!cls || (row.className || '').toLowerCase().includes(cls)) &&
    (!result || row.result === result) &&
    (!batch || (row.batch || '').toLowerCase().includes(batch))
  );
}

function renderTable() {
  const filtered = getFilteredRows();
  document.getElementById('filterCount').textContent = `${filtered.length} of ${viewerRows.length} row(s)`;
  const tbody = document.getElementById('viewerRows');
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:24px;">No rows match these filters.</td></tr>';
    return;
  }
  tbody.innerHTML = filtered.map(row => `
    <tr>
      <td>${row.vin || ''}</td>
      <td>${row.modelCode || ''}</td>
      <td>${row.date || ''}</td>
      <td>${row.time || ''}</td>
      <td>${row.shift || ''}</td>
      <td>${row.taskName || ''}</td>
      <td>${row.className || ''}</td>
      <td class="${row.result === 'OK' ? 'badge-ok' : 'badge-fail'}">${row.result || ''}</td>
      <td>${row.batch || ''}</td>
      <td>${row.imageUrl ? `<button class="img-link" onclick="openLightbox('${row.imageUrl}')">View</button>` : '<button class="img-link" disabled>—</button>'}</td>
    </tr>
  `).join('');
}

function openLightbox(url) {
  document.getElementById('lightboxImg').src = url;
  document.getElementById('lightbox').classList.add('open');
}

function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
  document.getElementById('lightboxImg').src = '';
}

async function downloadExcel() {
  const filtered = getFilteredRows();
  if (!filtered.length) { showToast('No rows to export'); return; }
  const r = await fetch('/api/export/excel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows: filtered, title: viewerDeviceLabel.replace(/[^a-zA-Z0-9]+/g, '_') }),
  });
  if (!r.ok) { showToast('Export failed'); return; }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = viewerDeviceLabel.replace(/[^a-zA-Z0-9]+/g, '_') + '.xlsx';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ── Live "receiving" activity feed ──────────────────────────────────────
// Polls for uploads that landed since the page opened, then flashes the
// header indicator, toasts, glows the affected app row, and refreshes the
// device list/storage bank so counts and "last received" update live --
// no manual refresh needed while data comes in.
let eventsCursor = Date.now();

async function pollEvents() {
  try {
    const r = await fetch('/api/events?since=' + eventsCursor);
    const d = await r.json();
    eventsCursor = d.serverTimeMs;
    if (d.events && d.events.length) {
      handleNewEvents(d.events);
    }
  } catch (e) {}
}

function handleNewEvents(events) {
  const indicator = document.getElementById('liveIndicator');
  indicator.classList.add('show');
  setTimeout(() => indicator.classList.remove('show'), 2500);

  for (const ev of events) {
    showToast(`📥 New data from ${ev.deviceName} — ${ev.appName}`);
  }

  loadDevices().then(() => {
    // Pulse the row(s) that just received data.
    const ids = new Set(events.map(e => e.deviceId));
    ids.forEach(id => {
      const row = document.querySelector(`.app-row[data-device-id="${id}"]`);
      if (row) {
        row.classList.add('pulse');
        setTimeout(() => row.classList.remove('pulse'), 1800);
      }
    });
  });

  if (document.getElementById('tab-storage').classList.contains('active')) {
    loadStorage();
  }
}

loadStatus();
loadDevices();
setInterval(loadDevices, 15000);
setInterval(pollEvents, 3000);
</script>
</body>
</html>
""".replace("__LOGO_B64__", LOGO_PNG_BASE64.replace("\n", ""))


def main():
    _load_devices()
    zeroconf = start_mdns()

    ip = _local_ip()
    url = f"http://127.0.0.1:{PORT}"
    print("\nMahindra Digital Eye — Storage Bank")
    print(f"Dashboard (this PC only): {url}")
    print(f"Phones on this WiFi/hotspot send to: {ip}:{PORT}")
    print("Leave this window open while receiving data. Press Ctrl+C to quit.\n")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
    finally:
        zeroconf.close()


if __name__ == "__main__":
    main()
