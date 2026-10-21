"""
inspector.py -- runs the YOLO model on the chosen phone's frames and decides the case result, then STOPS.

Flow: model loaded -> "waiting" for frames -> first frame starts a scan ("scanning") -> every new frame
is run through the model and fed to case_logic.CaseTracker -> when the case resolves (OK / NOT OK) or
`scan_seconds` pass (last frame decides, possibly NOT FOUND) the state becomes "done": inference stops,
the annotated frame stays on the viewer, the result is saved and a beep sounds. "Next car" (viewer
button, POST /api/restart) -- or `auto_restart_seconds` in the config -- starts the next scan.

VIN / task mode (when tasks.json exists next to the exe): the inspector idles in state "idle" until a
VIN arrives -- read from a QR / barcode in the phone's frames, typed in the viewer, or POST /api/vin. The
VIN picks a task (its own .pt + rules), the scan runs, the result is saved with the VIN, then after
`next_after_seconds` the inspector goes back to "idle" for the next VIN. Without tasks.json the original
single-model behaviour is unchanged.

The model / OpenCV imports are lazy: without ultralytics installed the runner still works as a plain
video viewer and says so in the log.
"""

import fnmatch
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


def load_tasks(path, log):
    """Read tasks.json -> list of task dicts ([] if the file is missing). Each task: name, vins (fnmatch
    patterns on the VIN, e.g. "MA1*"; "*" matches all), model (.pt) and any config key to override
    (mandatory, ignored, conf, imgsz, scan_seconds, ...)."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tasks = data["tasks"] if isinstance(data, dict) else data
        tasks = [t for t in tasks if isinstance(t, dict) and t.get("model")]
        log(f"tasks loaded: {path} -> {[t.get('name', t['model']) for t in tasks]}")
        return tasks
    except Exception as e:
        log(f"could not read {path}: {e} -- running in single-model mode", "ERROR")
        return []


def match_task(tasks, vin):
    v = (vin or "").strip().lower()
    for t in tasks:
        pats = t.get("vins") or ["*"]
        if isinstance(pats, str):
            pats = [pats]
        if any(fnmatch.fnmatch(v, str(p).lower()) for p in pats):
            return t
    return None


def read_codes(cv2, img, detectors):
    """QR (and barcode when this OpenCV has it) text values found in img."""
    out = []
    try:
        if "qr" not in detectors:
            detectors["qr"] = cv2.QRCodeDetector()
        txt, _, _ = detectors["qr"].detectAndDecode(img)
        if txt:
            out.append(txt)
    except Exception:
        pass
    try:
        if "bar" not in detectors:
            detectors["bar"] = cv2.barcode.BarcodeDetector() if hasattr(cv2, "barcode") else False
        if detectors["bar"]:
            ok, infos, _, _ = detectors["bar"].detectAndDecode(img)
            if ok:
                out += [i for i in infos if i]
    except Exception:
        pass
    return out


class Inspector:
    def __init__(self, cfg, base_dir, log, get_frame, tasks=None):
        self.base_cfg = cfg
        self.cfg = cfg
        self.base_dir = base_dir
        self.log = log
        self.get_frame = get_frame   # () -> (BGR ndarray or None, frame id) of the phone being inspected
        self.preview = bool(cfg.get('preview_only', False))  # boxes only: never decide, never stop
        self.tasks = tasks or []
        self.vin_mode = bool(self.tasks)
        self.vin = None
        self.task_name = None
        self._pending_vin = None
        self._pending_model = None
        self._last_vin = None
        self._models = {}           # resolved path -> (model, names)
        self._codes = {}            # cached cv2 detectors
        self._code_t = 0.0
        self.case_cfg = self._make_case_cfg(cfg)
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

    @staticmethod
    def _make_case_cfg(cfg):
        return CaseConfig(
            mandatory=cfg.get("mandatory"), ignored=cfg.get("ignored"), conf=cfg.get("conf", 0.5),
            fail_ticks_needed=cfg.get("fail_ticks_needed", 2), pass_ticks_needed=cfg.get("pass_ticks_needed", 1),
            scan_seconds=cfg.get("scan_seconds", 45))

    # ------------------------------------------------------------------ api
    def list_models(self):
        """*.pt files next to the exe and in its models/ folder (paths relative to it)."""
        out = []
        for sub in ("", "models"):
            d = os.path.join(self.base_dir, sub)
            if os.path.isdir(d):
                out += sorted(os.path.join(sub, f) if sub else f for f in os.listdir(d) if f.lower().endswith(".pt"))
        return out

    def select_model(self, name):
        if name not in self.list_models():
            return False
        self._pending_model = name
        return True

    def _save_model_choice(self, name):
        """Remember the choice in runner_config.json so the next start uses it."""
        path = os.path.join(self.base_dir, "runner_config.json")
        try:
            data = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["model"] = name
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.log(f"could not remember the model choice: {e}", "WARN")

    def set_preview(self, on):
        """Boxes-only mode: keeps detecting and drawing, never decides OK / NOT OK."""
        self.preview = bool(on)
        if self.state == "done":
            self._restart.set()
        self.log(f"preview (boxes only) {'ON' if self.preview else 'OFF'}")

    def set_vin(self, vin):
        """Start the task for this VIN (viewer box, POST /api/vin, or a QR read from the frames)."""
        vin = (vin or "").strip()
        if not vin:
            return False
        self._pending_vin = vin
        return True
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
            "vin": self.vin,
            "task": self.task_name,
            "model": self.cfg.get("model"),
            "preview": self.preview,
            "vin_mode": self.vin_mode,
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

        self.np, self.cv2, self.YOLO = np, cv2, YOLO
        if self.vin_mode:
            self.state = "idle"
            self.message = "scan the VIN"
            self.log("VIN mode -- waiting for a VIN (QR in the camera view, typed in the viewer, or POST /api/vin)")
        elif self._activate(self.base_cfg, None):
            self.log("inspector ready -- waiting for frames")
        else:
            self.state = "no_model"
            self.message += " -- pick a model in the viewer"

        last_frame_no = -1
        fps_t, fps_n = time.monotonic(), 0
        while True:
            try:
                if self._pending_model:
                    name, self._pending_model = self._pending_model, None
                    cfg = dict(self.base_cfg, model=name)
                    if self._activate(cfg, os.path.basename(name)):
                        self.base_cfg = cfg
                        self.message = f"{os.path.basename(name)} -- waiting for the phone camera..."
                        self.log(f"model selected: {name}")
                        self._save_model_choice(name)
                    else:
                        self.state = "no_model"
                    last_frame_no = -1
                if self._restart.is_set():
                    self._restart.clear()
                    self._reset_scan()
                    last_frame_no = -1
                frame, fid = self.get_frame()
                if self.vin_mode and self.state in ("idle", "done"):
                    vin = self._poll_vin(frame)
                    if vin:
                        self._start_vin(vin)
                        last_frame_no = -1
                        continue
                    if self.state == "done" and time.time() - self.done_at >= float(self.base_cfg.get("next_after_seconds", 4)):
                        self._reset_scan()
                    time.sleep(0.05)
                    continue
                if self.state == "done":
                    auto = float(self.cfg.get("auto_restart_seconds", 0) or 0)
                    if auto > 0 and time.time() - self.done_at >= auto:
                        self._reset_scan()
                    time.sleep(0.1)
                    continue
                if self.state not in ("waiting", "scanning"):
                    time.sleep(0.05)
                    continue
                if frame is None or fid == last_frame_no:
                    time.sleep(0.01)
                    continue
                last_frame_no = fid
                if self.state == "waiting":
                    self.state = "scanning"
                    self.message = "scanning"
                    self.started_at = time.time()
                    self.tracker.reset()
                    self.result = None
                    self.log("scan started")

                img = frame
                t0 = time.perf_counter()
                res = self.model.predict(img, imgsz=self.imgsz, conf=self.case_cfg.conf, device=self.device, verbose=False)[0]
                self.infer_ms = round((time.perf_counter() - t0) * 1000)
                boxes = []
                dets = []
                if res.boxes is not None and len(res.boxes):
                    for cls, p, xyxy in zip(res.boxes.cls.tolist(), res.boxes.conf.tolist(), res.boxes.xyxy.tolist()):
                        name = self.names.get(int(cls), str(int(cls)))
                        dets.append((name, float(p)))
                        boxes.append((name, float(p), xyxy))
                self.dets = dets

                fps_n += 1
                now_m = time.monotonic()
                if now_m - fps_t >= 2.0:
                    self.fps = round(fps_n / (now_m - fps_t), 1)
                    fps_n, fps_t = 0, now_m

                result = None if self.preview else self.tracker.update(dets)
                self.log(f"tick {self.infer_ms}ms | {', '.join(f'{n} {int(p * 100)}%' for n, p in dets) or 'none'} "
                         f"| ok {self.tracker.ok_count}/{self.case_cfg.pass_ticks_needed} "
                         f"fail {self.tracker.fail_count}/{self.case_cfg.fail_ticks_needed}", debug_only=True)
                if result is None and not self.preview and time.time() - self.started_at >= self.case_cfg.scan_seconds:
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
    def _activate(self, cfg, task_name):
        """Load (or reuse) cfg's model and arm the case rules. False + message on failure."""
        np, YOLO = self.np, self.YOLO
        model_path = cfg.get("model") or ""
        if not os.path.isabs(model_path):
            model_path = os.path.join(self.base_dir, model_path)
        self.imgsz = int(cfg.get("imgsz", 640))
        self.device = cfg.get("device", "cpu")
        fail_state = "idle" if self.vin_mode else "no_model"
        if not os.path.exists(model_path):
            self.state = fail_state
            self.message = f"model file not found: {model_path}"
            self.log(self.message, "ERROR")
            return False
        if model_path not in self._models:
            try:
                t0 = time.time()
                self.state, self.message = "loading", f"loading model for {task_name or 'inspection'}..."
                model = YOLO(model_path)
                names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
                model.predict(np.zeros((self.imgsz, self.imgsz, 3), np.uint8), imgsz=self.imgsz, device=self.device, verbose=False)
                self._models[model_path] = (model, names)
                self.log(f"model loaded + warmed up in {time.time() - t0:.1f}s: {model_path} (imgsz {self.imgsz}, {self.device})")
                self.log(f"model classes: {list(names.values())}")
            except Exception as e:
                self.state = fail_state
                self.message = f"could not load model: {e}"
                self.log(self.message, "ERROR")
                return False
        self.model, self.names = self._models[model_path]
        self.cfg = cfg
        self.case_cfg = self._make_case_cfg(cfg)
        all_classes = list(self.names.values())
        known = {n.lower().strip() for n in all_classes}
        missing = [c for c in self.case_cfg.mandatory + self.case_cfg.ignored if c.lower().strip() not in known]
        if missing:
            self.log(f"config names classes the model does not have: {missing}", "WARN")
        self.tracker = CaseTracker(self.case_cfg, all_classes)
        self.result, self.result_defects, self.dets, self.display = None, [], [], None
        self.state = "waiting"
        self.message = "waiting for the phone camera..."
        return True

    def _start_vin(self, vin):
        """Resolve the VIN to a task and arm it. Unknown VIN -> stay idle with a message."""
        task = match_task(self.tasks, vin)
        if task is None:
            self.vin, self.task_name, self.result = vin, None, None
            self.state, self.message = "idle", f"no task for VIN {vin}"
            self.log(f"VIN {vin}: no matching task in tasks.json", "WARN")
            return
        cfg = dict(self.base_cfg)
        cfg.update({k: v for k, v in task.items() if k not in ("vins", "name")})
        self.vin, self.task_name = vin, task.get("name") or task["model"]
        self._last_vin = vin
        self.log(f"VIN {vin} -> task '{self.task_name}' ({cfg['model']})")
        if self._activate(cfg, self.task_name):
            self.message = f"{self.task_name} -- waiting for the phone camera..."

    def _poll_vin(self, frame):
        """idle/done: a pending VIN (typed / API) wins; otherwise look for a QR / barcode in the frame."""
        if self._pending_vin:
            vin, self._pending_vin = self._pending_vin, None
            return vin
        if frame is not None and time.time() - self._code_t >= 0.4:
            self._code_t = time.time()
            img = frame
            if img is not None:
                for code in read_codes(self.cv2, img, self._codes):
                    code = code.strip()
                    # the car's QR stays in view after the result: ignore the same VIN for a while
                    if code and not (code == self._last_vin and time.time() - self.done_at < float(
                            self.base_cfg.get("rescan_block_seconds", 15))):
                        return code
        return None

    def _reset_scan(self):
        if self.vin_mode:
            self.state, self.message = "idle", "scan the VIN"
            self.vin, self.task_name = None, None
        else:
            self.state, self.message = "waiting", "waiting for the phone camera..."
        self.result = None
        self.result_defects = []
        self.dets = []
        self.display = None
        if self.tracker:
            self.tracker.reset()
        self.log("next car -- ready for a new " + ("VIN" if self.vin_mode else "scan"))

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
        hud = f"PREVIEW | {self.infer_ms} ms" if self.preview else f"scanning {left}s | {self.infer_ms} ms | fail {self.tracker.fail_count}/{self.case_cfg.fail_ticks_needed}"
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
        if self.vin:
            stamp = f"{stamp}_{''.join(c if c.isalnum() or c in '-_' else '_' for c in self.vin)}"
        try:
            csv_path = os.path.join(out_dir, "results.csv")
            new = not os.path.exists(csv_path)
            with open(csv_path, "a", encoding="utf-8") as f:
                if new:
                    f.write("time,vin,task,result,defects\n")
                f.write(f"{datetime.now().isoformat(timespec='seconds')},{self.vin or ''},{self.task_name or ''},"
                        f"{result},{'|'.join(self.result_defects)}\n")
        except Exception as e:
            self.log(f"could not append results.csv: {e}", "WARN")
        with open(os.path.join(out_dir, f"{stamp}_{tag}.json"), "w", encoding="utf-8") as f:
            json.dump({"result": result, "vin": self.vin, "task": self.task_name, "time": stamp, "defects": self.result_defects,
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
