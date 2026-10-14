"""
inspector.py -- runs the YOLO model on the live frames and decides the case result, then STOPS.

Flow: model loaded -> "waiting" for frames -> first frame starts a scan ("scanning") -> every new frame
is run through the model and fed to case_logic.CaseTracker -> when the case resolves (OK / NOT OK) or
`scan_seconds` pass (last frame decides, possibly NOT FOUND) the state becomes "done": inference stops,
the annotated frame stays on the viewer, the result is saved and a beep sounds. "Next car" (viewer
button, POST /api/restart) -- or `auto_restart_seconds` in the config -- starts the next scan.

The model / OpenCV imports are lazy: without ultralytics installed the runner still works as a plain
video viewer and says so in the log.
"""

import json
import os
import sys
import threading
import time
from datetime import datetime

from case_logic import CaseConfig, CaseTracker, OK, NOT_OK, NOT_FOUND

DEFAULT_CONFIG = {
    "model": "best.pt",
    "imgsz": 640,
    "conf": 0.5,
    "device": "cpu",
    "mandatory": [],
    "ignored": [],
    "fail_ticks_needed": 2,
    "pass_ticks_needed": 1,
    "scan_seconds": 45,
    "auto_restart_seconds": 0,
    "results_dir": "results",
    "save_images": True,
    "beep": True,
    "_help": "mandatory = classes that must ALL be seen for OK (empty = every model class). "
             "ignored = classes that never count. Any other class seen = NOT OK. "
             "model is a path to the .pt file (relative paths are next to runner.exe).",
}

COLOR_OK = (60, 200, 60)        # BGR
COLOR_BAD = (50, 50, 230)
COLOR_IGN = (150, 150, 150)
COLOR_OTHER = (0, 165, 255)


def load_config(path, log):
    """Read runner_config.json; write a default one next to the exe if it is missing."""
    cfg = dict(DEFAULT_CONFIG)
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
            log(f"wrote a default config: {path}  -> set 'model', 'mandatory' and 'ignored', then restart", "WARN")
        except Exception as e:
            log(f"could not write default config {path}: {e}", "WARN")
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
        log(f"config loaded: {path}")
    except Exception as e:
        log(f"could not read config {path}: {e} -- using defaults", "ERROR")
    return cfg


class Inspector:
    def __init__(self, cfg, base_dir, log, get_camera):
        self.cfg = cfg
        self.base_dir = base_dir
        self.log = log
        self.get_camera = get_camera
        self.case_cfg = CaseConfig(
            mandatory=cfg.get("mandatory"), ignored=cfg.get("ignored"), conf=cfg.get("conf", 0.5),
            fail_ticks_needed=cfg.get("fail_ticks_needed", 2), pass_ticks_needed=cfg.get("pass_ticks_needed", 1),
            scan_seconds=cfg.get("scan_seconds", 45))
        self.tracker = None
        self.state = "loading"      # loading | no_model | waiting | scanning | done
        self.message = "loading model..."
        self.result = None
        self.result_defects = []
        self.display = None         # JPEG bytes: last annotated frame
        self.fps = 0.0
        self.infer_ms = 0
        self.dets = []
        self.started_at = 0.0
        self.done_at = 0.0
        self._restart = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ api
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def restart(self):
        """Next car: clear the result and start a fresh scan."""
        self._restart.set()

    def status(self):
        left = None
        if self.state == "scanning":
            left = max(0, round(self.case_cfg.scan_seconds - (time.time() - self.started_at)))
        return {
            "state": self.state,
            "message": self.message,
            "result": self.result,
            "defects": self.result_defects,
            "seconds_left": left,
            "fps": self.fps,
            "infer_ms": self.infer_ms,
            "detections": [{"name": n, "conf": round(c, 2)} for n, c in self.dets],
            "mandatory": self.case_cfg.mandatory,
            "ignored": self.case_cfg.ignored,
            "fail_count": self.tracker.fail_count if self.tracker else 0,
            "fail_ticks_needed": self.case_cfg.fail_ticks_needed,
        }

    # ----------------------------------------------------------------- loop
    def _run(self):
        try:
            import numpy as np
            import cv2
            from ultralytics import YOLO
        except Exception as e:
            self.state = "no_model"
            self.message = f"ultralytics / opencv not installed ({e}) -- video only"
            self.log(self.message, "WARN")
            return

        model_path = self.cfg.get("model") or ""
        if not os.path.isabs(model_path):
            model_path = os.path.join(self.base_dir, model_path)
        if not os.path.exists(model_path):
            self.state = "no_model"
            self.message = f"model file not found: {model_path} -- set 'model' in runner_config.json"
            self.log(self.message, "ERROR")
            return

        imgsz = int(self.cfg.get("imgsz", 640))
        device = self.cfg.get("device", "cpu")
        try:
            t0 = time.time()
            model = YOLO(model_path)
            names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
            model.predict(np.zeros((imgsz, imgsz, 3), np.uint8), imgsz=imgsz, device=device, verbose=False)
            self.log(f"model loaded + warmed up in {time.time() - t0:.1f}s: {model_path} (imgsz {imgsz}, {device})")
            self.log(f"model classes: {list(names.values())}")
        except Exception as e:
            self.state = "no_model"
            self.message = f"could not load model: {e}"
            self.log(self.message, "ERROR")
            return

        all_classes = list(names.values())
        missing = [c for c in self.case_cfg.mandatory + self.case_cfg.ignored
                   if c.lower().strip() not in {n.lower().strip() for n in all_classes}]
        if missing:
            self.log(f"config names classes the model does not have: {missing}", "WARN")
        self.tracker = CaseTracker(self.case_cfg, all_classes)
        self.state = "waiting"
        self.message = "waiting for the phone camera..."
        self.log("inspector ready -- waiting for frames")

        last_frame_no = -1
        fps_t, fps_n = time.monotonic(), 0
        while True:
            try:
                if self._restart.is_set():
                    self._restart.clear()
                    self._reset_scan()
                    last_frame_no = -1
                if self.state == "done":
                    auto = float(self.cfg.get("auto_restart_seconds", 0) or 0)
                    if auto > 0 and time.time() - self.done_at >= auto:
                        self._reset_scan()
                    time.sleep(0.1)
                    continue
                cam = self.get_camera()
                if not cam or cam.latest is None or cam.frames == last_frame_no:
                    time.sleep(0.01)
                    continue
                last_frame_no = cam.frames
                data = cam.latest
                if self.state == "waiting":
                    self.state = "scanning"
                    self.message = "scanning"
                    self.started_at = time.time()
                    self.tracker.reset()
                    self.result = None
                    self.log("scan started")

                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                t0 = time.perf_counter()
                res = model.predict(img, imgsz=imgsz, conf=self.case_cfg.conf, device=device, verbose=False)[0]
                self.infer_ms = round((time.perf_counter() - t0) * 1000)
                boxes = []
                dets = []
                if res.boxes is not None and len(res.boxes):
                    for cls, p, xyxy in zip(res.boxes.cls.tolist(), res.boxes.conf.tolist(), res.boxes.xyxy.tolist()):
                        name = names.get(int(cls), str(int(cls)))
                        dets.append((name, float(p)))
                        boxes.append((name, float(p), xyxy))
                self.dets = dets

                fps_n += 1
                now_m = time.monotonic()
                if now_m - fps_t >= 2.0:
                    self.fps = round(fps_n / (now_m - fps_t), 1)
                    fps_n, fps_t = 0, now_m

                result = self.tracker.update(dets)
                self.log(f"tick {self.infer_ms}ms | {', '.join(f'{n} {int(p * 100)}%' for n, p in dets) or 'none'} "
                         f"| ok {self.tracker.ok_count}/{self.case_cfg.pass_ticks_needed} "
                         f"fail {self.tracker.fail_count}/{self.case_cfg.fail_ticks_needed}", debug_only=True)
                if result is None and time.time() - self.started_at >= self.case_cfg.scan_seconds:
                    result = self.tracker.final(dets)
                    self.log(f"scan time ({self.case_cfg.scan_seconds:.0f}s) elapsed -- last frame decides: {result}")

                annotated = self._draw(cv2, img, boxes)
                if result:
                    self._finish(cv2, result, img, annotated, dets)
                else:
                    self._set_display(cv2, annotated)
            except Exception:
                import traceback
                self.log(f"inspector error: {traceback.format_exc()}", "ERROR")
                time.sleep(0.5)

    # -------------------------------------------------------------- helpers
    def _reset_scan(self):
        self.state = "waiting"
        self.message = "waiting for the phone camera..."
        self.result = None
        self.result_defects = []
        self.dets = []
        self.display = None
        if self.tracker:
            self.tracker.reset()
        self.log("next car -- ready for a new scan")

    def _color(self, name):
        n = name.lower().strip()
        if n in {c.lower().strip() for c in self.case_cfg.ignored}:
            return COLOR_IGN
        mand = {c.lower().strip() for c in self.case_cfg.mandatory}
        if not mand or n in mand:
            return COLOR_OK
        return COLOR_BAD

    def _draw(self, cv2, img, boxes):
        out = img.copy()
        for name, p, (x1, y1, x2, y2) in boxes:
            col = self._color(name)
            cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), col, 2)
            label = f"{name} {int(p * 100)}%"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(out, (int(x1), int(y1) - th - 8), (int(x1) + tw + 6, int(y1)), col, -1)
            cv2.putText(out, label, (int(x1) + 3, int(y1) - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        left = max(0, int(self.case_cfg.scan_seconds - (time.time() - self.started_at)))
        hud = f"scanning {left}s | {self.infer_ms} ms | fail {self.tracker.fail_count}/{self.case_cfg.fail_ticks_needed}"
        cv2.putText(out, hud, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
        cv2.putText(out, hud, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        return out

    def _set_display(self, cv2, frame):
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self.display = buf.tobytes()

    def _finish(self, cv2, result, clean, annotated, dets):
        from case_logic import defect_classes
        self.result = result
        self.result_defects = defect_classes(dets, self.case_cfg, [])
        self.state = "done"
        self.message = result
        self.done_at = time.time()
        col = {OK: COLOR_OK, NOT_OK: COLOR_BAD}.get(result, COLOR_OTHER)
        h, w = annotated.shape[:2]
        cv2.rectangle(annotated, (0, h - 70), (w, h), col, -1)
        cv2.putText(annotated, result, (16, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 4)
        self._set_display(cv2, annotated)
        self.log(f"RESULT: {result}" + (f"  (fail classes: {self.result_defects})" if self.result_defects else ""))
        try:
            self._save(cv2, result, clean, annotated, dets)
        except Exception as e:
            self.log(f"could not save result: {e}", "WARN")
        if self.cfg.get("beep", True):
            threading.Thread(target=self._beep, args=(result,), daemon=True).start()

    def _save(self, cv2, result, clean, annotated, dets):
        out_dir = self.cfg.get("results_dir") or "results"
        if not os.path.isabs(out_dir):
            out_dir = os.path.join(self.base_dir, out_dir)
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = result.replace(" ", "_")
        with open(os.path.join(out_dir, f"{stamp}_{tag}.json"), "w", encoding="utf-8") as f:
            json.dump({"result": result, "time": stamp, "defects": self.result_defects,
                       "detections": [{"name": n, "conf": round(c, 3)} for n, c in dets],
                       "mandatory": self.case_cfg.mandatory, "ignored": self.case_cfg.ignored}, f, indent=2)
        if self.cfg.get("save_images", True):
            cv2.imwrite(os.path.join(out_dir, f"{stamp}_{tag}.jpg"), annotated)
            cv2.imwrite(os.path.join(out_dir, f"{stamp}_{tag}_clean.jpg"), clean)  # unannotated, for retraining
        self.log(f"saved to {out_dir}")

    @staticmethod
    def _beep(result):
        """OK = one long beep, NOT OK = three short, NOT FOUND = two medium (Windows; bell elsewhere)."""
        pattern = {OK: [(1000, 700)], NOT_OK: [(800, 200)] * 3, NOT_FOUND: [(600, 350)] * 2}.get(result, [(700, 300)])
        try:
            import winsound
            for freq, ms in pattern:
                winsound.Beep(freq, ms)
                time.sleep(0.08)
        except Exception:
            try:
                sys.stdout.write("\a")
                sys.stdout.flush()
            except Exception:
                pass
