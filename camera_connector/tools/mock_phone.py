"""Fake phone: serves the same HTTP contract as the Kotlin MjpegServer (for testing the PC side).
    python mock_phone.py [--port 8080] [--fps 25]"""
import argparse, json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import cv2, numpy as np

ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8080); ap.add_argument("--fps", type=int, default=25)
A = ap.parse_args()

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/ping"):
            b = json.dumps({"app": "runner-cam-phone", "version": "mock", "port": A.port, "name": "mock-cam"}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(b); return
        if self.path.startswith("/video"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Connection", "close"); self.end_headers()
            n = 0
            try:
                while True:
                    img = np.zeros((720, 1280, 3), np.uint8); img[:] = (n * 3 % 255, 80, 160)
                    cv2.putText(img, f"mock frame {n}", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 6)
                    j = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(j)).encode() + b"\r\n\r\n" + j + b"\r\n")
                    self.wfile.flush(); n += 1; time.sleep(1 / A.fps)
            except Exception: pass
            return
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()

ThreadingHTTPServer(("0.0.0.0", A.port), H).serve_forever()
