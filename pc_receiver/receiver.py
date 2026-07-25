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

import qrcode
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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
    return {"status": "ok", "savedTo": str(extract_dir)}


# ── Dashboard API (localhost only) ──────────────────────────────────────

@app.get("/api/status")
async def api_status(_: None = Depends(_require_local)):
    return {"pcName": PC_NAME, "ip": _local_ip(), "port": PORT}


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

  .empty { text-align: center; padding: 48px 12px; color: var(--muted); font-size: 13px; }

  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; display: flex; align-items: center; gap: 14px; }
  .card .avatar { width: 42px; height: 42px; border-radius: 50%; background: #fdeaea; color: var(--crimson); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 15px; flex-shrink: 0; }
  .card .info { flex: 1; min-width: 0; }
  .card .info .name { font-weight: 700; font-size: 14px; }
  .card .info .meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .card .stats { display: flex; gap: 18px; font-size: 11px; color: var(--muted); text-align: center; }
  .card .stats b { display: block; font-size: 14px; color: var(--text); }

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

async function loadDevices() {
  const el = document.getElementById('devicesList');
  try {
    const r = await fetch('/api/devices');
    const devices = await r.json();
    if (!devices.length) {
      el.innerHTML = '<div class="empty">No phones paired yet. Tap "+ Add New Device" to pair one.</div>';
      return;
    }
    el.innerHTML = devices.map(d => `
      <div class="card">
        <div class="avatar">${initials(d.deviceName)}</div>
        <div class="info">
          <div class="name">${d.deviceName} <span style="color:var(--muted);font-weight:400;">— ${d.appName}</span></div>
          <div class="meta">Paired ${new Date(d.pairedAt).toLocaleString()}${d.lastReceivedAt ? ' • Last received ' + d.lastReceivedAt : ' • No data received yet'}</div>
        </div>
        <div class="stats">
          <div><b>${d.batchCount}</b>sends</div>
          <div><b>${fmtBytes(d.totalBytes)}</b>size</div>
        </div>
        <button class="ghost" onclick="removeDevice('${d.deviceId}', '${d.deviceName}')">Remove</button>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty">Could not load devices.</div>';
  }
}

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

loadStatus();
loadDevices();
setInterval(loadDevices, 15000);
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
