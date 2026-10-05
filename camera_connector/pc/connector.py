"""
connector.py -- PC side of Runner Cam (phone camera as a CCTV-style feed).

The phone app serves an MJPEG stream (like IP Webcam). This program:
  1. SCANS the Wi-Fi/hotspot for phones (HTTP /ping sweep of every local /24
     + UDP beacon on 8092),
  2. reads the chosen phone with plain OpenCV  cv2.VideoCapture(url)  -- the same
     call used for a webcam/CCTV, so detection code can be plugged in later,
  3. shows the feed + stats + debug log in a browser page (http://127.0.0.1:8095),
     optionally also in an OpenCV window (--window).

    python connector.py                 # scan, auto-connect if exactly one phone
    python connector.py --url http://192.168.1.50:8080/video
    python connector.py --window --no-browser

Debug mode is ON by default (--no-debug to quiet). Everything is also written to
connector_debug.log next to the program.
"""
import os

# Must be set before cv2 is imported: low-latency ffmpeg reading, 5 s I/O timeout.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "fflags;nobuffer|flags;low_delay|rw_timeout;3000000")

import argparse
import collections
import json
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2

VERSION = "0.1.0"
PHONE_APP = "runner-cam-phone"
PC_APP = "runner-cam-pc"
BEACON_PORT = 8092
DEBUG = True

LOG = collections.deque(maxlen=500)
_logf = None


def log(msg, level="INFO", debug_only=False):
    if debug_only and not DEBUG:
        return
    line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} [{level}] {msg}"
    LOG.append(line)
    try:
        print(line, flush=True)
    except Exception:
        pass
    if _logf:
        try:
            _logf.write(line + "\n")
            _logf.flush()
        except Exception:
            pass


def app_dir():
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


# ------------------------------------------------------------------ scan --


def local_ips():
    ips = set()
    try:
        for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(i[4][0])
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def probe(ip, port, timeout=0.7):
    try:
        with urllib.request.urlopen(f"http://{ip}:{port}/ping", timeout=timeout) as r:
            j = json.loads(r.read(2000).decode())
        if j.get("app") == PHONE_APP:
            return {"ip": ip, "port": j.get("port", port), "name": j.get("name", ip)}
    except Exception:
        pass
    return None


def listen_beacon(seconds, out):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", BEACON_PORT))
        s.settimeout(0.5)
        end = time.time() + seconds
        while time.time() < end:
            try:
                data, _ = s.recvfrom(1024)
                j = json.loads(data.decode())
                if j.get("app") == PHONE_APP:
                    out.append({"ip": j["ip"], "port": j["port"], "name": j.get("name", j["ip"])})
            except socket.timeout:
                pass
            except Exception:
                pass
    except Exception as e:
        log(f"beacon listen failed (sweep still works): {e}", "WARN")
    finally:
        s.close()


def scan(port=8080, extra_ips=()):
    ips = local_ips()
    log(f"scan: this PC has {ips or 'NO network address'}; sweeping /24 on port {port}")
    if not ips:
        log("scan: connect the PC to the same Wi-Fi as the phone (or to the phone's hotspot)", "ERROR")
    hosts = ["127.0.0.1"] + list(extra_ips)
    bases = []
    for ip in ips:
        a, b, c, _ = ip.split(".")
        for cc in (int(c), int(c) ^ 1):  # also the neighbouring /24: covers /23 networks like 10.3.64.0/23
            if f"{a}.{b}.{cc}" not in bases:
                bases.append(f"{a}.{b}.{cc}")
    for base in bases:
        hosts += [f"{base}.{n}" for n in range(1, 255)]
    found, beacon = {}, []
    t = threading.Thread(target=listen_beacon, args=(3, beacon), daemon=True)
    t.start()
    with ThreadPoolExecutor(64) as ex:
        for r in ex.map(lambda h: probe(h, port), hosts):
            if r:
                found[(r["ip"], r["port"])] = r
    t.join()
    for r in beacon:
        found.setdefault((r["ip"], r["port"]), r)
    if any(ip in ips for ip, _ in found):  # same machine seen as loopback and as its LAN address
        found = {k: v for k, v in found.items() if k[0] != "127.0.0.1"}
    phones = list(found.values())
    log(f"scan done: {len(phones)} phone(s): " + ", ".join(f"{p['name']} {p['ip']}:{p['port']}" for p in phones))
    return phones


# ------------------------------------------------------------- diagnose --


def diagnose(url, opencv_failed=True):
    """Plain-socket checks that explain WHY a stream cannot be opened (OpenCV only says 'cannot open')."""
    u = urlparse(url)
    host, port = u.hostname, u.port or 80
    log(f"diagnose: PC addresses {local_ips() or 'NONE'}  ->  phone {host}:{port}", "WARN")
    t = time.monotonic()
    try:
        s = socket.create_connection((host, port), timeout=4)
        s.close()
        log(f"diagnose: TCP connect OK ({(time.monotonic() - t) * 1000:.0f} ms) -- the phone is reachable", "WARN")
    except socket.timeout:
        log("diagnose: TCP connect TIMED OUT. Packets are being dropped: PC and phone are on different "
            "networks/VLANs, or the Wi-Fi blocks device-to-device traffic (client isolation), or a firewall. "
            "Try the phone's hotspot.", "ERROR")
        return False, "PC cannot reach the phone: TCP connect timed out (different VLAN / Wi-Fi client isolation / firewall)"
    except ConnectionRefusedError:
        log(f"diagnose: connection REFUSED. The phone answered but nothing listens on port {port}: "
            "is 'Start camera server' pressed, and is the port the same as in the app?", "ERROR")
        return False, f"PC reached the phone but port {port} refused the connection (camera server not started?)"
    except OSError as e:
        log(f"diagnose: cannot reach {host}: [{e.errno}] {e}. Usually 'no route' = different network.", "ERROR")
        return False, f"PC cannot reach the phone: {e}"
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/ping", timeout=4) as r:
            body = r.read(300).decode(errors="replace")
        log(f"diagnose: /ping answered: {body}", "WARN")
    except Exception as e:
        log(f"diagnose: TCP ok but /ping failed: {type(e).__name__}: {e}", "ERROR")
        return False, f"PC reached the phone but /ping failed: {type(e).__name__}: {e}"
    try:
        r = urllib.request.urlopen(f"http://{host}:{port}/video", timeout=6)
        head = r.read(64)
        log(f"diagnose: /video HTTP {r.status}, type={r.headers.get('Content-Type')}, first bytes={head[:24]!r}", "WARN")
        r.close()
        if opencv_failed:
            log("diagnose: network and phone are fine; OpenCV/ffmpeg itself failed to decode -- send this log", "ERROR")
        return True, "PC reached the phone and /video answers"
    except Exception as e:
        log(f"diagnose: /video request failed: {type(e).__name__}: {e} (phone camera may not have started "
            "-- check the phone's DEBUG LOG)", "ERROR")
        return False, f"PC reached the phone but /video failed: {type(e).__name__}: {e}"


# ---------------------------------------------------------------- reader --


class Reader(threading.Thread):
    """Reads the stream with cv2.VideoCapture; keeps only the newest frame."""

    def __init__(self, url, name=None):
        super().__init__(daemon=True)
        self.url = url
        u = urlparse(url)
        self.key = f"{u.hostname}:{u.port or 80}"
        self.name = name or u.hostname
        self.stop_flag = False
        self.lock = threading.Lock()
        self.frame = None
        self.frame_id = 0
        self.frames = 0
        self.fails = 0
        self.reconnects = 0
        self.size = None
        self.fps = 0.0
        self.last_frame_t = 0.0
        self.state = "connecting"
        self.read_ms = 0.0

    def run(self):
        while not self.stop_flag:
            log(f"opening {self.url}", debug_only=True)
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            if not cap.isOpened():
                self.state = "cannot open (retrying)"
                log(f"cannot open {self.url} (attempt {self.reconnects + 1})", "ERROR")
                cap.release()
                if self.reconnects % 10 == 0:
                    diagnose(self.url)
                self.reconnects += 1
                time.sleep(2)
                continue
            self.state = "live"
            log(f"connected to {self.url}")
            n, t0, consecutive = 0, time.monotonic(), 0
            while not self.stop_flag:
                t = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    consecutive += 1
                    self.fails += 1
                    if consecutive >= 20:
                        log(f"stream stalled / closed by phone after {self.frames} frames "
                            f"(last frame {time.time() - self.last_frame_t:.1f}s ago) -> reconnecting", "WARN")
                        diagnose(self.url, opencv_failed=False)  # is the phone still reachable, does /video answer?
                        break
                    time.sleep(0.05)
                    continue
                consecutive = 0
                self.read_ms = (time.monotonic() - t) * 1000
                with self.lock:
                    self.frame = frame
                    self.frame_id += 1
                self.frames += 1
                self.last_frame_t = time.time()
                if self.size is None or self.size != (frame.shape[1], frame.shape[0]):
                    self.size = (frame.shape[1], frame.shape[0])
                    log(f"frame size {self.size[0]}x{self.size[1]}")
                n += 1
                now = time.monotonic()
                if now - t0 >= 1.0:
                    self.fps = round(n / (now - t0), 1)
                    n, t0 = 0, now
                    log(f"fps {self.fps}  total {self.frames}  failed reads {self.fails}", debug_only=True)
            cap.release()
            if not self.stop_flag:
                self.state = "reconnecting"
                self.reconnects += 1
                time.sleep(1)

    def stop(self):
        self.stop_flag = True

    def get(self):
        with self.lock:
            return self.frame, self.frame_id

    def status(self):
        age = round(time.time() - self.last_frame_t, 1) if self.last_frame_t else None
        return {"key": self.key, "name": self.name, "url": self.url, "state": self.state, "fps": self.fps if age is not None and age < 3 else 0,
                "frames": self.frames, "failed_reads": self.fails, "reconnects": self.reconnects,
                "size": f"{self.size[0]}x{self.size[1]}" if self.size else None,
                "seconds_since_frame": age, "read_ms": round(self.read_ms, 1)}


# ------------------------------------------------------------------ http --

STATE = {"readers": {}, "phones": [], "scanning": False, "port": 8080, "ui_port": 8095}
STATE_LOCK = threading.Lock()


def connect(url, name=None):
    """Start (or restart) reading one phone. Many phones can be connected at once."""
    r = Reader(url, name)
    with STATE_LOCK:
        old = STATE["readers"].get(r.key)
        if old:
            old.stop()
        STATE["readers"][r.key] = r
    r.start()
    log(f"connecting to {url} as '{r.name}' ({len(STATE['readers'])} phone(s) connected)")


def disconnect(key):
    with STATE_LOCK:
        r = STATE["readers"].pop(key, None)
    if r:
        r.stop()
        log(f"disconnected {key}")


def first_reader(key=None):
    rs = STATE["readers"]
    if key and key in rs:
        return rs[key]
    return next(iter(rs.values()), None)


def do_scan():
    if STATE["scanning"]:
        return
    STATE["scanning"] = True
    try:
        STATE["phones"] = scan(STATE["port"])
    except Exception:
        log(f"scan crashed: {traceback.format_exc()}", "ERROR")
    finally:
        STATE["scanning"] = False


# ---- QR code the phone scans to find this PC (no network scan needed) ----


def qr_payload():
    return json.dumps({"app": PC_APP, "ips": local_ips(), "port": STATE["ui_port"],
                       "name": socket.gethostname()}, separators=(",", ":"))


def qr_matrix(text):
    import qrcode
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    q.add_data(text)
    q.make(fit=True)
    return q.get_matrix()


def qr_svg(text):
    m = qr_matrix(text)
    n, b = len(m), 3
    rects = "".join(f'<rect x="{x + b}" y="{y + b}" width="1" height="1"/>'
                    for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n + 2 * b} {n + 2 * b}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
            f'<g fill="#000">{rects}</g></svg>')


def print_qr_console(text):
    try:
        m = qr_matrix(text)
        pad = [[False] * (len(m) + 4) for _ in range(2)]
        rows = pad + [[False, False] + r + [False, False] for r in m] + pad
        print("\nScan this QR with the Runner Cam phone app ('Scan QR'):\n")
        for y in range(0, len(rows), 2):
            line = ""
            for x in range(len(rows[0])):
                top = rows[y][x]
                bot = rows[y + 1][x] if y + 1 < len(rows) else False
                line += "\u2588" if top and bot else "\u2580" if top else "\u2584" if bot else " "
            print(line)
        print()
    except Exception as e:  # console encoding / missing lib -- the viewer page still shows the QR
        log(f"could not print QR in console ({type(e).__name__}); open the viewer page instead", "WARN", debug_only=True)


PAGE = """<!doctype html><meta charset=utf-8><title>Runner Cam connector</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>body{margin:0;font:14px system-ui,sans-serif;background:#111;color:#ddd}
header{padding:10px 16px;background:#0b6fa4;color:#fff;font-weight:600}
main{display:flex;flex-wrap:wrap;gap:16px;padding:16px}#feeds{flex:2 1 480px;display:grid;gap:10px;
grid-template-columns:repeat(auto-fit,minmax(300px,1fr));align-content:start}
.tile{background:#000;border-radius:6px;overflow:hidden}.tile img{width:100%;display:block;min-height:160px}
.tile div{padding:4px 8px;font-size:12px;display:flex;justify-content:space-between}
#side{flex:1 1 320px}#qr{background:#fff;padding:6px;border-radius:8px;width:230px}
pre{background:#000;color:#9f9;padding:8px;border-radius:6px;overflow:auto;max-height:300px;font-size:12px;margin:0}
button{padding:5px 10px;margin:2px}td{padding:2px 8px 2px 0}.bad{color:#f66}.ok{color:#6f6}</style>
<header>Runner Cam connector <span id=ver></span></header><main>
<div id=feeds><i id=nofeed>No phone connected yet. On the phone: tap <b>Scan QR</b> and scan the code on the right.</i></div>
<div id=side>
<h3>Connect a phone</h3>
<img id=qr src="/qr.svg" alt="QR"><div id=addr></div>
<small>Phone app: Scan QR (many phones can connect to this PC).</small>
<h3>Phones found by scan <button onclick="fetch('/api/scan',{method:'POST'})">Scan</button><span id=scanning></span></h3>
<div id=phones></div>
<h3>Connections</h3><table id=st></table>
<h3>Manual URL</h3><input id=u size=30 placeholder="http://192.168.1.50:8080/video">
<button onclick="go(u.value)">Connect</button>
<h3>Debug log</h3><pre id=log></pre></div></main>
<script>
function go(url){fetch('/api/connect?url='+encodeURIComponent(url),{method:'POST'})}
function drop(k){fetch('/api/disconnect?cam='+encodeURIComponent(k),{method:'POST'})}
let shown='';
async function tick(){try{
 const s=await (await fetch('/api/status')).json();
 ver.textContent='v'+s.version+(s.debug?' (debug)':'');
 addr.textContent=s.ips.map(i=>i+':'+s.ui_port).join('   ')||'no network address';
 scanning.textContent=s.scanning?' scanning...':'';
 phones.innerHTML=s.phones.map(p=>'<div>'+p.name+' '+p.ip+':'+p.port+
  ' <button onclick="go(\\'http://'+p.ip+':'+p.port+'/video\\')">Connect</button></div>').join('')||'<i>none found yet</i>';
 const keys=s.readers.map(r=>r.key).join(',');
 if(keys!==shown){shown=keys;
  feeds.innerHTML=s.readers.length?s.readers.map(r=>'<div class=tile><img src="/video?cam='+encodeURIComponent(r.key)+
   '"><div><span>'+r.name+' ('+r.key+')</span><button onclick="drop(\\''+r.key+'\\')">disconnect</button></div></div>').join(''):
   '<i>No phone connected yet. On the phone: tap <b>Scan QR</b> and scan the code on the right.</i>';}
 st.innerHTML='<tr><th>name<th>state<th>fps<th>size<th>frames<th>reconn</tr>'+s.readers.map(r=>'<tr><td>'+r.name+
  '<td class='+(r.state=='live'?'ok':'bad')+'>'+r.state+'<td>'+r.fps+'<td>'+(r.size||'-')+'<td>'+r.frames+'<td>'+r.reconnects).join('');
 const l=await (await fetch('/api/log')).json();
 const end=log.scrollTop+log.clientHeight>=log.scrollHeight-20;
 log.textContent=l.join('\\n');if(end)log.scrollTop=log.scrollHeight;
}catch(e){}}
setInterval(tick,1000);tick();</script>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        line = fmt % a
        if "/api/" not in line and "/video" not in line:
            log("http " + line, debug_only=True)

    def _send(self, body, ctype="application/json", code=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            self._send(PAGE, "text/html")
        elif u.path == "/ping":
            self._send(json.dumps({"app": PC_APP, "version": VERSION, "name": socket.gethostname(),
                                   "port": STATE.get("ui_port")}))
        elif u.path == "/qr.svg":
            try:
                self._send(qr_svg(qr_payload()), "image/svg+xml")
            except Exception as e:
                log(f"QR failed: {type(e).__name__}: {e}", "ERROR")
                self._send("QR unavailable", "text/plain", 500)
        elif u.path == "/api/status":
            self._send(json.dumps({"version": VERSION, "debug": DEBUG, "scanning": STATE["scanning"],
                                   "phones": STATE["phones"], "ips": local_ips(), "ui_port": STATE["ui_port"],
                                   "readers": [r.status() for r in STATE["readers"].values()]}))
        elif u.path == "/api/log":
            self._send(json.dumps(list(LOG)[-200:]))
        elif u.path == "/snapshot.jpg":
            r = first_reader(q.get("cam", [None])[0])
            f = r.get()[0] if r else None
            if f is None:
                return self._send("no frame yet", "text/plain", 404)
            self._send(cv2.imencode(".jpg", f)[1].tobytes(), "image/jpeg")
        elif u.path == "/video":
            self._video(q.get("cam", [None])[0])
        else:
            self._send("not found", "text/plain", 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/scan":
            threading.Thread(target=do_scan, daemon=True).start()
            self._send("{}")
        elif u.path == "/api/register":
            # the phone scanned our QR (or found us) and tells us where its stream is
            phone = q.get("phone", [""])[0]
            name = q.get("name", [""])[0] or None
            log(f"phone {self.client_address[0]} registered itself: {phone} name={name}")
            if not phone:
                return self._send(json.dumps({"ok": False, "message": "no phone address given"}))
            url = f"http://{phone}/video"
            ok, msg = diagnose(url, opencv_failed=False)
            if ok:
                connect(url, name)
                msg += " -- PC is now reading the stream"
            self._send(json.dumps({"ok": ok, "message": msg}))
        elif u.path == "/api/connect":
            url = q.get("url", [""])[0]
            if url.startswith("http"):
                connect(url)
            self._send("{}")
        elif u.path == "/api/disconnect":
            disconnect(q.get("cam", [""])[0])
            self._send("{}")
        else:
            self._send("not found", "text/plain", 404)

    def _video(self, cam):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last = -1
        try:
            while True:
                r = first_reader(cam)
                f, fid = r.get() if r else (None, -1)
                if f is None or fid == last:
                    time.sleep(0.01)
                    continue
                last = fid
                jpg = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


# ------------------------------------------------------------------ main --


def main():
    global DEBUG, _logf
    ap = argparse.ArgumentParser(description="Runner Cam PC connector")
    ap.add_argument("--url", help="skip scan, read this stream, e.g. http://192.168.1.50:8080/video")
    ap.add_argument("--phone-port", type=int, default=8080, help="port the phone app serves on")
    ap.add_argument("--ui-port", type=int, default=8095, help="port of this program's viewer page (and QR)")
    ap.add_argument("--no-debug", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-scan", action="store_true", help="do not scan at start; wait for phones to scan the QR")
    ap.add_argument("--window", action="store_true", help="also show an OpenCV window (first phone)")
    ap.add_argument("--scan-only", action="store_true")
    a = ap.parse_args()
    DEBUG = not a.no_debug
    STATE["port"] = a.phone_port
    STATE["ui_port"] = a.ui_port
    try:
        _logf = open(os.path.join(app_dir(), "connector_debug.log"), "a", encoding="utf-8")
    except Exception as e:
        print("cannot open log file:", e)

    log(f"Runner Cam PC connector v{VERSION}  opencv {cv2.__version__}  debug={DEBUG}")
    if a.scan_only:
        do_scan()
        return 0

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", a.ui_port), Handler)
    except OSError as e:
        log(f"cannot open viewer port {a.ui_port}: {e} (use --ui-port)", "ERROR")
        return 1
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"viewer + QR page: http://127.0.0.1:{a.ui_port}/   (PC addresses: {local_ips() or 'NONE'})")
    log("Windows firewall popup -> Allow (Private AND Public), so phones can reach this PC.")
    print_qr_console(qr_payload())
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{a.ui_port}/")).start()

    if a.url:
        connect(a.url)
    elif not a.no_scan:
        do_scan()
        if len(STATE["phones"]) == 1:
            p = STATE["phones"][0]
            connect(f"http://{p['ip']}:{p['port']}/video", p["name"])
        elif not STATE["phones"]:
            log("no phone found by scanning. That is fine: scan the QR above with the phone app, "
                "or press Scan on the viewer page.", "WARN")
        else:
            log("several phones found -- connect them from the viewer page")

    try:
        while True:
            if a.window:  # imshow must run on the main thread
                r = first_reader()
                f = r.get()[0] if r else None
                if f is not None:
                    cv2.imshow("Runner Cam", f)
                if cv2.waitKey(10) == 27:
                    break
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        log(f"fatal: {traceback.format_exc()}", "ERROR")
        code = 1
    if code and getattr(sys, "frozen", False):
        input("\nStopped with an error (see above / connector_debug.log). Press Enter to close...")
    sys.exit(code)
