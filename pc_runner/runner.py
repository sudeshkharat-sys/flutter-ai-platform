"""
runner.py -- PC side of the "Runner Cam" system (phone camera -> PC), like IP Webcam.

The phone app streams JPEG frames to this process over Wi-Fi / hotspot. This
process serves them back in a browser live viewer, announces itself on the
LAN so the phone can find it (scan), and logs everything needed to debug a
bad connection. No model inference yet -- see LIVE_FEED_RUNNER_PLAN.md.

Run:
    python runner.py                 # debug is ON by default while we build this
    python runner.py --port 8090 --no-debug --no-browser

Endpoints (all on --port):
    /                 live viewer + stats + debug log (open in any browser)
    /video            MJPEG stream of the latest frame (works in <img>/VLC)
    /snapshot.jpg     latest frame
    /ping             {"app": "runner-cam", ...}  <- what the phone scan looks for
    /api/status       JSON stats        /api/log   recent log lines
    ws /stream        phone -> PC: binary = one JPEG per message,
                      text = JSON {"type": "hello" | "log", ...}
                      PC -> phone: "ack:<n>" after every accepted frame
UDP broadcast on --beacon-port announces {"app": "runner-cam", ip, port, name}
every 2 s so the phone can discover this PC without typing an IP.

The only dependencies are fastapi + uvicorn (no OpenCV): JPEG validity and
size are checked by parsing the JPEG header directly, which keeps the .exe
small and removes the GUI/threading failures an OpenCV window caused before.
"""

import argparse
import asyncio
import collections
import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

VERSION = "0.2.0"
APP_ID = "runner-cam"
HOSTNAME = socket.gethostname()

# ---------------------------------------------------------------- logging --

LOG_LINES = collections.deque(maxlen=500)
DEBUG = True
_log_file = None


def log(msg, level="INFO", debug_only=False):
    if debug_only and not DEBUG:
        return
    line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} [{level}] {msg}"
    LOG_LINES.append(line)
    try:
        print(line, flush=True)
    except Exception:
        pass
    if _log_file:
        try:
            _log_file.write(line + "\n")
            _log_file.flush()
        except Exception:
            pass


def log_exc(where):
    log(f"{where}: {traceback.format_exc()}", "ERROR")


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- helpers --


def local_ips():
    """All IPv4 addresses of this PC (best effort, no extra deps)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(HOSTNAME, None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:  # the interface used for outbound traffic -- usually the Wi-Fi one
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def jpeg_info(data):
    """Return (width, height) if data is a JPEG, else None. Pure Python."""
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        seg_len = (data[i + 2] << 8) | data[i + 3]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = (data[i + 5] << 8) | data[i + 6]
            w = (data[i + 7] << 8) | data[i + 8]
            return w, h
        i += 2 + seg_len
    return None


class Camera:
    """One connected phone and its latest frame."""

    def __init__(self, name, remote):
        self.name = name
        self.remote = remote
        self.connected_at = time.time()
        self.frames = 0
        self.bad_frames = 0
        self.bytes = 0
        self.last_frame_at = 0.0
        self.latest = None
        self.size = None
        self.fps = 0.0
        self.device = {}
        self._fps_count = 0
        self._fps_t = time.monotonic()
        self.online = True

    def push(self, data, size):
        self.latest = data
        self.size = size
        self.frames += 1
        self.bytes += len(data)
        self.last_frame_at = time.time()
        self._fps_count += 1
        now = time.monotonic()
        if now - self._fps_t >= 1.0:
            self.fps = round(self._fps_count / (now - self._fps_t), 1)
            self._fps_count = 0
            self._fps_t = now

    def status(self):
        return {
            "name": self.name,
            "remote": self.remote,
            "online": self.online,
            "frames": self.frames,
            "bad_frames": self.bad_frames,
            "fps": self.fps if self.online else 0,
            "size": f"{self.size[0]}x{self.size[1]}" if self.size else None,
            "kb_per_frame": round(self.bytes / self.frames / 1024, 1) if self.frames else 0,
            "seconds_since_last_frame": round(time.time() - self.last_frame_at, 1) if self.last_frame_at else None,
            "device": self.device,
        }


CAMERAS = {}  # name -> Camera
FRAME_EVENT = None  # asyncio.Event, set on every new frame (created on loop start)
PORT = 8090
STARTED = time.time()

app = FastAPI(title="Runner Cam PC runner")


@app.on_event("startup")
async def _startup():
    global FRAME_EVENT
    FRAME_EVENT = asyncio.Event()
    log(f"server up on port {PORT}", debug_only=True)


def pick_camera(name=None):
    if name and name in CAMERAS:
        return CAMERAS[name]
    live = [c for c in CAMERAS.values() if c.latest]
    return max(live, key=lambda c: c.last_frame_at) if live else None


# ------------------------------------------------------------- websocket --


@app.websocket("/stream")
async def stream(ws: WebSocket):
    remote = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
    name = ws.query_params.get("name") or f"phone-{ws.client.host if ws.client else 'x'}"
    await ws.accept()
    log(f"phone connected: {name} from {remote}")
    log(f"  headers: {dict(ws.headers)}", debug_only=True)
    cam = Camera(name, remote)
    old = CAMERAS.get(name)
    if old:
        log(f"  replacing stale session for '{name}'", "WARN", debug_only=True)
    CAMERAS[name] = cam
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(msg.get("code", 1000))
            if msg.get("bytes") is not None:
                data = msg["bytes"]
                size = jpeg_info(data)
                if size is None:
                    cam.bad_frames += 1
                    log(f"{name}: {len(data)} bytes is not a valid JPEG (first bytes: {data[:8].hex()})", "WARN")
                    await ws.send_text(f"err:not_jpeg:{len(data)}")
                    continue
                cam.push(data, size)
                if cam.frames == 1:
                    log(f"{name}: first frame {size[0]}x{size[1]}, {len(data) // 1024} KB")
                log(f"{name}: frame {cam.frames} {len(data)} B", debug_only=True) if cam.frames % 30 == 0 else None
                FRAME_EVENT.set()
                FRAME_EVENT.clear()
                await ws.send_text(f"ack:{cam.frames}")
            elif msg.get("text") is not None:
                try:
                    obj = json.loads(msg["text"])
                except Exception:
                    log(f"{name}: text message (not JSON): {msg['text'][:200]}", "WARN")
                    continue
                if obj.get("type") == "hello":
                    cam.device = {k: v for k, v in obj.items() if k != "type"}
                    log(f"{name}: hello {cam.device}")
                elif obj.get("type") == "log":
                    log(f"[phone:{name}] {obj.get('level', 'INFO')}: {obj.get('msg')}", "PHONE")
    except WebSocketDisconnect as e:
        log(f"phone disconnected: {name} after {cam.frames} frames (code {e.code})")
    except Exception:
        log_exc(f"stream error for {name}")
    finally:
        cam.online = False
        FRAME_EVENT.set()
        FRAME_EVENT.clear()


# ------------------------------------------------------------------ http --


@app.get("/ping")
async def ping():
    return {"app": APP_ID, "version": VERSION, "name": HOSTNAME, "port": PORT, "debug": DEBUG}


@app.get("/api/status")
async def api_status():
    return {
        "app": APP_ID,
        "version": VERSION,
        "host": HOSTNAME,
        "port": PORT,
        "ips": local_ips(),
        "uptime_s": round(time.time() - STARTED),
        "debug": DEBUG,
        "cameras": [c.status() for c in CAMERAS.values()],
    }


@app.get("/api/log")
async def api_log(n: int = 200):
    return JSONResponse(list(LOG_LINES)[-n:])


@app.get("/snapshot.jpg")
async def snapshot(cam: str = None):
    c = pick_camera(cam)
    if not c:
        return Response("no camera has sent a frame yet", status_code=404)
    return Response(c.latest, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/video")
async def video(cam: str = None):
    async def gen():
        last = None
        while True:
            c = pick_camera(cam)
            if c and c.latest is not None and c.latest is not last:
                last = c.latest
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(last)).encode() + b"\r\n\r\n" + last + b"\r\n")
            try:
                await asyncio.wait_for(FRAME_EVENT.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


VIEWER_HTML = """<!doctype html><meta charset=utf-8><title>Runner Cam</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
body{margin:0;font:14px system-ui,sans-serif;background:#111;color:#ddd}
header{padding:10px 16px;background:#0b6fa4;color:#fff;font-weight:600}
main{display:flex;flex-wrap:wrap;gap:16px;padding:16px}
#feed{flex:2 1 480px;min-width:300px}
#feed img{width:100%;background:#000;min-height:200px;border-radius:6px}
#side{flex:1 1 320px;min-width:280px}
pre{background:#000;color:#9f9;padding:8px;border-radius:6px;overflow:auto;max-height:340px;font-size:12px;margin:0}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #333;padding:3px 6px;text-align:left}
.ok{color:#6f6}.bad{color:#f66}button{padding:6px 10px}
</style>
<header>Runner Cam <span id=ver></span></header>
<main>
<div id=feed><img id=v src="/video"><div id=nofeed class=bad></div></div>
<div id=side>
<h3>Connect the phone to</h3><div id=ips></div>
<h3>Cameras</h3><table id=cams></table>
<h3>Debug log <button onclick="document.getElementById('log').textContent=''">clear view</button></h3>
<pre id=log></pre>
</div></main>
<script>
async function tick(){
 try{
  const s=await (await fetch('/api/status')).json();
  ver.textContent='v'+s.version+(s.debug?' (debug)':'');
  ips.innerHTML=s.ips.map(i=>'<code>'+i+':'+s.port+'</code>').join(' &nbsp; ')||'<span class=bad>no network found</span>';
  cams.innerHTML='<tr><th>name<th>fps<th>size<th>frames<th>bad<th>state</tr>'+s.cameras.map(c=>
   '<tr><td>'+c.name+'<td>'+c.fps+'<td>'+(c.size||'-')+'<td>'+c.frames+'<td>'+c.bad_frames+
   '<td class='+(c.online?'ok':'bad')+'>'+(c.online?'live':'offline')).join('');
  nofeed.textContent=s.cameras.length?'':'Waiting for a phone... open Runner Cam on the phone and tap Scan.';
  const l=await (await fetch('/api/log?n=200')).json();
  const atEnd=log.scrollTop+log.clientHeight>=log.scrollHeight-20;
  log.textContent=l.join('\\n'); if(atEnd)log.scrollTop=log.scrollHeight;
 }catch(e){nofeed.textContent='lost connection to runner: '+e}
}
setInterval(tick,1000);tick();
</script>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return VIEWER_HTML


# --------------------------------------------------------------- beacon --


def beacon_loop(port, beacon_port):
    """UDP-broadcast our address so the phone can find us without typing it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    log(f"beacon: broadcasting on UDP {beacon_port}", debug_only=True)
    sent = 0
    while True:
        for ip in local_ips():
            payload = json.dumps({"app": APP_ID, "ip": ip, "port": port, "name": HOSTNAME}).encode()
            targets = ["255.255.255.255", ip.rsplit(".", 1)[0] + ".255"]
            for t in targets:
                try:
                    sock.sendto(payload, (t, beacon_port))
                except Exception as e:
                    if sent == 0:
                        log(f"beacon send to {t} failed: {e}", "WARN", debug_only=True)
        sent += 1
        time.sleep(2)


def free_port(start):
    for p in range(start, start + 10):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", p))
            return p
        except OSError:
            log(f"port {p} is busy, trying next", "WARN")
        finally:
            s.close()
    return None


def main():
    global DEBUG, PORT, _log_file
    ap = argparse.ArgumentParser(description="Runner Cam PC runner")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--beacon-port", type=int, default=8091)
    ap.add_argument("--no-debug", action="store_true", help="quieter logs")
    ap.add_argument("--no-browser", action="store_true", help="do not open the viewer")
    args = ap.parse_args()
    DEBUG = not args.no_debug

    try:
        _log_file = open(os.path.join(app_dir(), "runner_debug.log"), "a", encoding="utf-8")
    except Exception as e:
        print(f"could not open log file: {e}")

    log(f"Runner Cam PC runner v{VERSION}  host={HOSTNAME}  debug={DEBUG}")
    log(f"python {sys.version.split()[0]} on {sys.platform}; log file: {_log_file.name if _log_file else 'none'}", debug_only=True)

    port = free_port(args.port)
    if port is None:
        log(f"no free port in {args.port}-{args.port + 9}. Close the other program or pass --port.", "ERROR")
        return 1
    PORT = port

    ips = local_ips()
    log("=" * 56)
    if ips:
        for ip in ips:
            log(f"  phone address:  {ip}:{port}      viewer: http://{ip}:{port}/")
    else:
        log("  NO NETWORK ADDRESS FOUND -- connect this PC to Wi-Fi / the phone hotspot", "ERROR")
    log("  Windows firewall popup? Click 'Allow' (private networks).")
    log("=" * 56)

    threading.Thread(target=beacon_loop, args=(port, args.beacon_port), daemon=True).start()
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()

    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info" if DEBUG else "warning")
    except Exception:
        log_exc("server crashed")
        return 1
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        log_exc("fatal")
        code = 1
    if code and getattr(sys, "frozen", False):
        input("\nRunner stopped with an error (see above / runner_debug.log). Press Enter to close...")
    sys.exit(code)
