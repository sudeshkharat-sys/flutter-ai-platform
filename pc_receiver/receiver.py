"""
PC Receiver -- companion app for the "Send to PC" feature in generated
Flutter AI Studio apps.

Run this on the PC. It:
  1. Advertises itself on the local WiFi/hotspot via mDNS so the phone can
     find it by name instead of typing an IP.
  2. Lets you generate a one-time pairing QR code from the console. The
     phone scans it once; after that the PC only accepts uploads that carry
     a valid HMAC signature derived from the secret exchanged during that
     pairing -- any other device on the same WiFi is rejected with 401,
     even though it can see this PC's IP.
  3. Receives the zipped inspection data and saves it under ./received_data.

No installation needed when packaged with PyInstaller (see README.md) --
just double-click the resulting .exe.
"""

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import qrcode
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from zeroconf import ServiceInfo, Zeroconf

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
_paired_devices: dict[str, dict] = {}      # deviceId -> {secret, deviceName, pairedAt}
_pending_token: dict | None = None          # {token, expiresAt}
_failed_attempts: dict[str, list[float]] = {}  # client ip -> [failure timestamps]


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


# ── Pairing ──────────────────────────────────────────────────────────────

def generate_pairing_qr():
    """Print a QR code + payload for one-time pairing, valid for 5 minutes."""
    global _pending_token
    token = secrets.token_urlsafe(24)
    with _lock:
        _pending_token = {"token": token, "expiresAt": time.time() + PAIRING_TOKEN_TTL}

    payload = {"name": PC_NAME, "ip": _local_ip(), "port": PORT, "token": token}
    payload_json = json.dumps(payload)

    print("\nScan this QR code from the app's \"Send to PC\" > \"Pair New PC\" screen.")
    print(f"(Valid for {PAIRING_TOKEN_TTL // 60} minutes)\n")
    qr = qrcode.QRCode(border=1)
    qr.add_data(payload_json)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print(f"\nRaw payload (fallback if QR can't be scanned): {payload_json}\n")


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


# ── Console command loop ────────────────────────────────────────────────

def console_loop():
    print(f"\nPC Receiver running as '{PC_NAME}' on {_local_ip()}:{PORT}")
    print("Commands: [p]air new phone   [l]ist paired devices   [q]uit\n")
    while True:
        try:
            cmd = input("> ").strip().lower()
        except EOFError:
            break
        if cmd in ("p", "pair"):
            generate_pairing_qr()
        elif cmd in ("l", "list"):
            with _lock:
                if not _paired_devices:
                    print("No devices paired yet.")
                for did, d in _paired_devices.items():
                    print(f"  {d['deviceName']} / {d.get('appName', 'app')}  (paired {d['pairedAt']})  id={did}")
        elif cmd in ("q", "quit", "exit"):
            print("Shutting down...")
            import os
            os._exit(0)
        else:
            print("Unknown command. Use p / l / q.")


def main():
    _load_devices()
    zeroconf = start_mdns()

    server_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning"),
        daemon=True,
    )
    server_thread.start()

    try:
        console_loop()
    finally:
        zeroconf.close()


if __name__ == "__main__":
    main()
