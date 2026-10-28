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

from case_logic import CaseConfig, CaseTracker, BlurGate, OK, NOT_OK, NOT_FOUND
import pack as packmod

DEFAULT_CONFIG = {
    "model": "best.pt",
    "imgsz": 640,
    "conf": 0.5,
    "device": "cpu",
    "mandatory": [],
    "ignored": [],
    "fail_ticks_needed": 3,
    "pass_ticks_needed": 3,
    "scan_seconds": 45,
    "blur_gate": True,          # frames clearly blurrier than the sharpest recent one do not vote
    "settle_seconds": 1.5,      # after a scan starts, wait this long (camera refocusing) before frames may vote; the time limit starts after it
    "min_tick_ms": 150,         # at least this long between two voting frames (3 in a row then take >= 0.45 s)
    "auto_restart_seconds": 0,
    "results_dir": "results",
    "save_images": True,
    "beep": True,
    "preview_only": True,
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
        log("no runner_config.json -- using defaults (boxes only; pick the .pt in the viewer)")
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
        try:  # several QR codes in view: read them all
            ok, texts, _, _ = detectors["qr"].detectAndDecodeMulti(img)
            if ok:
                out += [t for t in texts if t]
        except Exception:
            pass
        if not out:
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


DEFAULT_ROI = [0.3, 0.3, 0.7, 0.7]   # fixed red box (fractions of the frame), used until a square is drawn


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
        self.pack = None            # imported app pack (scan logic + tasks), see pack.py
        self.mode = cfg.get("mode") or "pack"      # "pack" = scan a label, tasks come from the pack; "manual" = one chosen task
        self.auto_start = bool(cfg.get("auto_start", True))    # off: after Submit the next task waits for Start
        self.auto_scan = bool(cfg.get("auto_scan", True))      # off: a label is only read when Scan is pressed
        self._scan_req = None
        self.scan_reply = None
        self.blur = BlurGate()
        self.sharp = 0.0
        self.blur_skipped = 0
        self._last_good = []        # detections of the last sharp frame (the time-out verdict uses these)
        self._tick_t = 0.0
        self._mode_pending = False
        self.queue = []             # tasks of the scanned code, run one after the other
        self.queue_i = 0
        self.code = None
        self.scan_roi = cfg.get("scan_roi")   # [x1, y1, x2, y2] fractions: only this part of the frame is decoded
        self._bad_code = None
        self._pending_vin = None
        self._pending_model = None
        self._pending_task = None
        self.task_file = os.path.join(base_dir, "inspection_tasks.json")
        self.task_list = self._read_task_list()
        self._load_lock = threading.Lock()
        self.np = self.cv2 = self.YOLO = None
        self.imgsz = int(cfg.get("imgsz", 640))
        self.device = cfg.get("device", "cpu")
        self._last_vin = None
        self.model = None           # nothing loaded until a .pt is picked / found
        self.names = {}
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
        self._last_frame_t = 0.0    # when the loop last got a frame (a long gap = phone disconnected / camera stalled)
        self.done_at = 0.0
        self._restart = threading.Event()
        self._lock = threading.Lock()

    @property
    def _pk(self):
        """The app pack when it is the active mode, else None."""
        return self.pack if self.mode == "pack" else None

    def _refresh_vin_mode(self):
        self.vin_mode = bool(self.tasks) or bool(self._pk)

    @staticmethod
    def _make_case_cfg(cfg):
        return CaseConfig(
            mandatory=cfg.get("mandatory"), ignored=cfg.get("ignored"), fail=cfg.get("fail"), conf=cfg.get("conf", 0.5),
            fail_ticks_needed=cfg.get("fail_ticks_needed", 2), pass_ticks_needed=cfg.get("pass_ticks_needed", 1),
            scan_seconds=cfg.get("scan_seconds", 45), explicit=cfg.get("explicit", False))

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

    def _save_choice(self, key, name):
        """Remember a choice (model / task) in runner_config.json so the next start uses it."""
        path = os.path.join(self.base_dir, "runner_config.json")
        try:
            data = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data[key] = name
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.log(f"could not remember the choice: {e}", "WARN")

    # --- task builder: name + model + Pass / Fail / Ignore class lists, stored in inspection_tasks.json
    def _read_task_list(self):
        try:
            if os.path.exists(self.task_file):
                with open(self.task_file, "r", encoding="utf-8") as f:
                    return [t for t in json.load(f).get("tasks", []) if t.get("name") and t.get("model")]
        except Exception as e:
            self.log(f"could not read {self.task_file}: {e}", "ERROR")
        return []

    def list_tasks(self):
        return self.task_list

    def save_task(self, task):
        """Create or overwrite (by name) a task: {name, model, pass[], fail[], ignore[], scan_seconds, conf}."""
        name = str(task.get("name", "")).strip()
        model = str(task.get("model", "")).strip()
        if not name or model not in self.list_models():
            return False, "give the task a name and pick a model"
        lists = {k: [str(c) for c in task.get(k, [])] for k in ("pass", "fail", "ignore")}
        seen = set()
        for k, v in lists.items():
            if seen & set(v):
                return False, "a class can be in only one of Pass / Fail / Ignore"
            seen |= set(v)
        try:
            entry = {"name": name, "model": model, **lists,
                     "scan_seconds": max(1.0, float(task.get("scan_seconds", 45))),
                     "conf": min(0.99, max(0.05, float(task.get("conf", 0.5))))}
        except (TypeError, ValueError):
            return False, "scan seconds and confidence must be numbers"
        tasks = [t for t in self.task_list if t["name"] != name] + [entry]
        try:
            with open(self.task_file, "w", encoding="utf-8") as f:
                json.dump({"tasks": tasks}, f, indent=2)
        except Exception as e:
            return False, f"could not save: {e}"
        self.task_list = tasks
        self.log(f"task saved: {name} ({model}) pass={lists['pass']} fail={lists['fail']} ignore={lists['ignore']}")
        if self.task_name == name and self.state != "scanning":
            self._pending_task = name  # re-arm the active task with the new rules
        return True, "saved"

    def delete_task(self, name):
        tasks = [t for t in self.task_list if t["name"] != name]
        if len(tasks) == len(self.task_list):
            return False
        with open(self.task_file, "w", encoding="utf-8") as f:
            json.dump({"tasks": tasks}, f, indent=2)
        self.task_list = tasks
        if self.task_name == name:
            self.task_name = None
        return True

    def select_task(self, name):
        if not any(t["name"] == name for t in self.task_list):
            return False
        self._pending_task = name
        return True

    def get_classes(self, model_name):
        """Class names of a .pt (loads + caches it). (names, error)"""
        if model_name not in self.list_models():
            return [], "model not found"
        if self.YOLO is None:
            return [], "model engine still starting (or ultralytics missing) -- try again in a moment"
        path = os.path.join(self.base_dir, model_name)
        try:
            return list(self._load(path)[1].values()), ""
        except Exception as e:
            return [], f"could not load model: {e}"

    def _task_cfg(self, task):
        """Task -> config overrides. Pass = mandatory, Ignore = ignored, Fail = fail. With no Pass classes,
        every class that is neither Fail nor Ignore must be seen."""
        # A task with an explicit Fail list follows the phone app: only the classes assigned to the task count
        # (Pass / Fail / Ignore), any other class the model sees is not part of it. Old pass-only tasks keep the
        # builder rule "unassigned class = fail".
        explicit = bool(task.get("explicit", bool(task.get("fail"))))
        universe = [str(c) for c in (task.get("classes") or (task.get("pass", []) + task.get("fail", []) + task.get("ignore", [])))]
        cfg = dict(self.base_cfg, model=task["model"], conf=task.get("conf", 0.5),
                   explicit=explicit,
                   only_classes=universe if explicit else [],
                   scan_seconds=task.get("scan_seconds", 45), fail=task.get("fail", []),
                   ignored=task.get("ignore", []), mandatory=task.get("pass", []))
        if not cfg["mandatory"]:
            _, names = self._models.get(os.path.join(self.base_dir, task["model"]), (None, {}))
            cfg["mandatory"] = [n for n in names.values() if n not in cfg["fail"] and n not in cfg["ignored"]]
        return cfg

    def phone_reconnected(self):
        """The phone app (re)started and registered: a scan clock that ran while no phone was streaming must not count,
        and a NOT FOUND that was only the old stream dying is thrown away (fresh scan)."""
        if self.preview:
            return
        if self.state == "scanning":
            self.started_at = time.time() + float(self.cfg.get("settle_seconds", 1.5))
            self.tracker.reset()
            self.blur.reset()
            self._last_good = []
            self.log("phone reconnected -- scan timer restarted")
        elif self.state == "done" and self.result == NOT_FOUND:
            self.log("phone reconnected -- discarding the old NOT FOUND, scanning again")
            self._restart.set()

    def set_preview(self, on):
        """Boxes-only mode: keeps detecting and drawing, never decides OK / NOT OK."""
        self.preview = bool(on)
        if self.state == "done":
            self._restart.set()
        elif self.state == "scanning" and not self.preview:
            # boxes-only had been running since the first frame: Start inspection must begin a FRESH scan
            # (else the old start time makes the time limit expire at once -> NOT FOUND within seconds)
            self.state, self.message = "waiting", "waiting for the phone camera..."
            self.result, self.dets = None, []
            if self.tracker:
                self.tracker.reset()
        self.log(f"preview (boxes only) {'ON' if self.preview else 'OFF'}")

    def load_pack(self, pk, switch=False):
        """Use an imported app pack (None = remove it). switch=True also makes pack mode active."""
        self.pack = pk
        self.queue, self.queue_i, self.code = [], 0, None
        if pk and switch:
            self.mode = "pack"
            self._save_choice("mode", "pack")
        self._refresh_vin_mode()
        if pk:
            if self._pk and self.state != "loading" and self.np is not None:
                self.state, self.message = "idle", "scan the label"
                self.vin, self.task_name, self.result = None, None, None
            self.log(f"app pack loaded: {pk.get('app_name')} scan={pk.get('scan_types')} tasks={len(pk.get('tasks', []))}")
        else:
            self._mode_pending = True
            self.log("app pack removed")

    def set_mode(self, mode):
        """'pack' (scan a label, tasks from the pack) or 'manual' (the task chosen in the dropdown, repeated)."""
        if mode not in ("pack", "manual") or (mode == "pack" and not self.pack):
            return False
        self.mode = mode
        self._save_choice("mode", mode)
        self._refresh_vin_mode()
        self._mode_pending = True
        self.log(f"mode: {mode}")
        return True

    def _apply_mode(self):
        self.queue, self.queue_i, self.code, self.vin = [], 0, None, None
        self.result, self.task_name = None, None
        if self._pk:
            self.state, self.message = "idle", "scan the label"
            return
        saved = self.base_cfg.get("task")
        if saved and any(t["name"] == saved for t in self.task_list):
            self._pending_task = saved
        elif self.model is None:
            self.state, self.message = "no_model", "pick or create a task"
        else:
            self.state, self.message = "waiting", "waiting for the phone camera..."

    def set_auto_start(self, on):
        self.auto_start = bool(on)
        self._save_choice("auto_start", self.auto_start)

    def set_auto_scan(self, on):
        self.auto_scan = bool(on)
        self._save_choice("auto_scan", self.auto_scan)

    def start_scan(self):
        """Start button: the armed task begins looking at the camera."""
        if self.state != "ready":
            return False
        self.state, self.message = "waiting", "waiting for the phone camera..."
        self.log("start pressed")
        return True

    def retry(self):
        """Recapture: run the same task again (the earlier result stays in results.csv)."""
        if self.state != "done" or self.model is None or not self.task_name or self.task_name.startswith("("):
            return False
        self.result, self.result_defects, self.dets, self.display = None, [], [], None
        if self.tracker:
            self.tracker.reset()
        self.state, self.message = "waiting", "retry -- waiting for the phone camera..."
        self.log(f"retry: {self.task_name}")
        return True

    def request_scan(self):
        """Scan button: read the label in the scan square now (result in scan_reply)."""
        if not self._pk or self.state != "idle":
            self.scan_reply = {"ok": False, "message": "not waiting for a label right now"}
            return
        self.scan_reply = None
        self._scan_req = time.time()

    def set_roi(self, roi):
        """The manual square: only this part of the frame is searched for the QR code. None clears it."""
        if roi is not None:
            x1, y1, x2, y2 = [min(1.0, max(0.0, float(v))) for v in roi]
            if x2 - x1 < 0.03 or y2 - y1 < 0.03:
                return False
            roi = [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]
        self.scan_roi = roi
        self._save_choice("scan_roi", roi)
        self.log(f"scan square: {roi or 'whole frame'}")
        return True

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
            left = min(round(self.case_cfg.scan_seconds), max(0, round(self.case_cfg.scan_seconds - (time.time() - self.started_at))))
        return {
            "state": self.state,
            "vin": self.vin,
            "task": self.task_name or (self.queue[self.queue_i]["name"] if self.queue and self.queue_i < len(self.queue) else None),
            "model": self.cfg.get("model"),
            "preview": self.preview,
            "vin_mode": self.vin_mode,
            "pack": packmod.summary(self.pack),
            "mode": self.mode,
            "auto_start": self.auto_start,
            "auto_scan": self.auto_scan,
            "scan_reply": self.scan_reply,
            "sharp": round(self.sharp),
            "blur_skipped": self.blur_skipped,
            "code": self.code,
            "task_i": self.queue_i + 1 if self.queue else 0,
            "task_n": len(self.queue),
            "next_task": self.queue[self.queue_i + 1]["name"] if self.queue and self.queue_i + 1 < len(self.queue) else None,
            "roi": self.effective_roi(),
            "message": self.message,
            "result": self.result,
            "defects": self.result_defects,
            "seconds_left": left,
            "fps": self.fps,
            "infer_ms": self.infer_ms,
            "detections": [{"name": n, "conf": round(c, 2)} for n, c in self.dets],
            "mandatory": self.case_cfg.mandatory,
            "ignored": self.case_cfg.ignored,
            "fail": self.case_cfg.fail,
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
            self.message = "scan the label" if self._pk else "scan the VIN"
            self.log("VIN mode -- waiting for a VIN (QR in the camera view, typed in the viewer, or POST /api/vin)")
        else:
            found = self.list_models()
            cur = self.base_cfg.get("model") or ""
            if found and not os.path.exists(cur if os.path.isabs(cur) else os.path.join(self.base_dir, cur)):
                self.base_cfg = dict(self.base_cfg, model=found[0])  # configured .pt missing: use the one in the folder
                self.log(f"'{cur}' not found -- using {found[0]}", "WARN")
        if self.vin_mode:
            pass
        elif self.tasks == [] and self.task_list and self.base_cfg.get("task") in [t["name"] for t in self.task_list]:
            self._pending_task = self.base_cfg["task"]
            self.state, self.message = "no_model", "loading the saved task..."
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
                        self.task_name = None  # a plain model pick (boxes only) is not a task
                        self._save_choice("model", name)
                    else:
                        self.state = "no_model"
                    last_frame_no = -1
                if self._pending_task:
                    name, self._pending_task = self._pending_task, None
                    task = next((t for t in self.task_list if t["name"] == name), None)
                    if task and self._activate_task(task):
                        self._save_choice("task", name)
                        self.base_cfg = dict(self.base_cfg, task=name)
                    last_frame_no = -1
                if self._restart.is_set():
                    self._restart.clear()
                    self._reset_scan()
                    last_frame_no = -1
                frame, fid = self.get_frame()
                if self._mode_pending:
                    self._mode_pending = False
                    self._apply_mode()
                    last_frame_no = -1
                if self._pk and self.state == "idle":
                    self._pack_idle(frame)
                    time.sleep(0.15)
                    continue
                if self.vin_mode and (self.state == "idle" or (self.state == "done" and not self._pk)):
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
                if self.state not in ("waiting", "scanning") or self.model is None:
                    time.sleep(0.05)
                    continue
                if frame is None or fid == last_frame_no:
                    time.sleep(0.01)
                    continue
                last_frame_no = fid
                now_f = time.time()
                if self.state == "scanning" and self._last_frame_t and now_f - self._last_frame_t > 3.0:
                    # No frames for a while (phone was disconnected / app closed / camera stalled): the time that passed
                    # was not scanning time. Without this the first frame after reconnecting hit the old deadline -> instant NOT FOUND.
                    self.log(f"frames resumed after {now_f - self._last_frame_t:.0f}s -- scan timer restarted")
                    self.started_at = now_f + (0 if self.preview else float(self.cfg.get("settle_seconds", 1.5)))
                    self.tracker.reset()
                    self.blur.reset()
                    self._last_good = []
                self._last_frame_t = now_f
                if self.state == "waiting":
                    self.state = "scanning"
                    self.message = "scanning"
                    self.started_at = time.time() + (0 if self.preview else float(self.cfg.get('settle_seconds', 1.5)))
                    self.tracker.reset()
                    self.blur.reset()
                    self._last_good = []
                    self.result = None
                    self.log("scan started")

                img = frame
                gap = float(self.cfg.get("min_tick_ms", 150)) / 1000.0
                if not self.preview and gap > 0 and time.time() - self._tick_t < gap:
                    time.sleep(max(0.0, gap - (time.time() - self._tick_t)))
                    last_frame_no = -1          # take the freshest frame next round, not the one that waited
                    continue
                self._tick_t = time.time()
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
                only = {c.lower().strip() for c in (self.cfg.get("only_classes") or [])}
                if only:   # app-pack task: classes the task does not list are not part of it (not drawn, never a defect)
                    dets = [d for d in dets if d[0].lower().strip() in only]
                    boxes = [b for b in boxes if b[0].lower().strip() in only]
                self.dets = dets

                fps_n += 1
                now_m = time.monotonic()
                if now_m - fps_t >= 2.0:
                    self.fps = round(fps_n / (now_m - fps_t), 1)
                    fps_n, fps_t = 0, now_m

                bad = False
                if not self.preview and self.cfg.get("blur_gate", True):
                    g = cv2.cvtColor(cv2.resize(img, (320, max(1, int(img.shape[0] * 320 / img.shape[1])))), cv2.COLOR_BGR2GRAY)
                    self.sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
                    bad = self.blur.blurry(self.sharp)
                    self.blur_skipped += 1 if bad else 0
                settling = not self.preview and time.time() < self.started_at
                if settling:
                    result = None       # camera still settling after the scan armed: shown, but no vote
                elif bad:
                    result = None       # blurry frame: shown, but it does not vote (and does not reset the count)
                    self.log(f"skipped blurry frame (sharpness {self.sharp:.0f}, best recent {self.blur.ref:.0f})", debug_only=True)
                else:
                    self._last_good = dets
                    result = None if self.preview else self.tracker.update(dets)
                self.log(f"tick {self.infer_ms}ms | {', '.join(f'{n} {int(p * 100)}%' for n, p in dets) or 'none'} "
                         f"| ok {self.tracker.ok_count}/{self.case_cfg.pass_ticks_needed} "
                         f"fail {self.tracker.fail_count}/{self.case_cfg.fail_ticks_needed}", debug_only=True)
                if result is None and not self.preview and time.time() - self.started_at >= self.case_cfg.scan_seconds:
                    result = self.tracker.final(self._last_good if bad else dets)
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
                self.state, self.message = "loading", f"loading model for {task_name or 'inspection'}..."
                self._load(model_path)
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
        self.blur.reset()
        self._last_good = []
        self.result, self.result_defects, self.dets, self.display = None, [], [], None
        self.state = "waiting"
        self.message = "waiting for the phone camera..."
        return True

    def _load(self, model_path):
        """Load + warm up a .pt once; cached by path (shared by the inspector and the task builder)."""
        with self._load_lock:
            if model_path not in self._models:
                t0 = time.time()
                model = self.YOLO(model_path)
                names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
                model.predict(self.np.zeros((self.imgsz, self.imgsz, 3), self.np.uint8), imgsz=self.imgsz,
                              device=self.device, verbose=False)
                self._models[model_path] = (model, names)
                self.log(f"model loaded + warmed up in {time.time() - t0:.1f}s: {model_path} (imgsz {self.imgsz}, {self.device})")
                self.log(f"model classes: {list(names.values())}")
            return self._models[model_path]

    def _activate_task(self, task):
        """Arm a builder task (its model + Pass/Fail/Ignore rules)."""
        path = os.path.join(self.base_dir, task["model"])
        if path not in self._models:  # classes are needed when Pass is empty
            try:
                self.state, self.message = "loading", f"loading model for {task['name']}..."
                self._load(path)
            except Exception as e:
                self.state, self.message = "no_model", f"could not load model: {e}"
                self.log(self.message, "ERROR")
                return False
        cfg = self._task_cfg(task)
        self.base_cfg = dict(self.base_cfg, model=task["model"])
        if not self._activate(cfg, task["name"]):
            return False
        self.task_name = task["name"]
        self.message = f"task {self.task_name} -- waiting for the phone camera..."
        self.log(f"task selected: {self.task_name} ({task['model']}) pass={task.get('pass')} "
                 f"fail={task.get('fail')} ignore={task.get('ignore')}")
        return True

    def _start_vin(self, vin):
        """Resolve the VIN to a task and arm it. Unknown VIN -> stay idle with a message."""
        if self._pk:
            return self._start_pack(vin)
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

    def _start_pack(self, raw):
        """A label was scanned: decode it with the pack's scan logic and arm the first task."""
        plan = packmod.plan_scan(self.pack, raw)
        self._last_vin = raw
        if plan["action"] == "reject":
            self.vin, self.task_name, self.result, self.queue = None, None, None, []
            self.state, self.message = "idle", plan["message"]
            self.log(f"scan rejected: {plan['message']}  (raw: {raw[:60]})", "WARN")
            return
        self.vin, self.code = plan["id"], plan["code"]
        if plan["action"] == "unmapped":
            self.queue, self.queue_i = [], 0
            self.task_name = "(unmapped code)"
            self._finish_unmapped(plan["message"])
            return
        self.queue, self.queue_i = plan["tasks"], 0
        self.log(f"scanned {self.vin} / code {self.code}: {len(self.queue)} task(s) {[t['name'] for t in self.queue]}")
        self._run_queue_task()

    def _run_queue_task(self):
        task = self.queue[self.queue_i]
        if self._activate_task(task):
            self._hold(f"{task['name']} ({self.queue_i + 1}/{len(self.queue)})")
            return True
        return False

    def _hold(self, label):
        """Task armed: wait for Start (so the operator can aim the camera first) unless auto-start is on."""
        if self.auto_start or self.preview:
            self.message = f"{label} -- waiting for the phone camera..."
        else:
            self.state, self.message = "ready", f"{label} -- aim the camera, then press START"

    def _finish_unmapped(self, message):
        """Code with no task and 'auto submit unmapped codes' on: OK without inspecting anything."""
        self.result, self.result_defects = OK, []
        self.state, self.message, self.done_at = "done", message, time.time()
        self.display = None
        self.log(f"RESULT: OK (unmapped) {self.vin} {message}")
        try:
            out_dir = self.cfg.get("results_dir") or "results"
            if not os.path.isabs(out_dir):
                out_dir = os.path.join(self.base_dir, out_dir)
            os.makedirs(out_dir, exist_ok=True)
            csv_path = os.path.join(out_dir, "results.csv")
            new = not os.path.exists(csv_path)
            with open(csv_path, "a", encoding="utf-8") as f:
                if new:
                    f.write("time,vin,task,result,defects\n")
                f.write(f"{datetime.now().isoformat(timespec='seconds')},{self.vin},(unmapped code {self.code}),OK,\n")
        except Exception as e:
            self.log(f"could not append results.csv: {e}", "WARN")

    def _label_codes(self, img):
        """QR texts in the scan square: as is, then upscaled / contrast-boosted (small or dense codes)."""
        cv2 = self.cv2
        crop = self._crop_roi(img)
        found = list(read_codes(cv2, crop, self._codes))
        if not found:
            try:
                g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                big = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                for variant in (big, cv2.createCLAHE(2.0, (8, 8)).apply(big),
                                cv2.adaptiveThreshold(big, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 5)):
                    found += read_codes(cv2, variant, self._codes)
                    if found:
                        break
            except Exception:
                pass
        out = []
        for c in found:
            c = c.strip()
            if c and c not in out:
                out.append(c)
        return out

    def _pack_idle(self, frame):
        """Pack mode, waiting for a label: read it when Scan is pressed (or always, with auto-scan on)."""
        req = self._scan_req
        if not req and not self.auto_scan:
            return
        if frame is None:
            if req and time.time() - req > 4:
                self._scan_req, self.scan_reply = None, {"ok": False, "message": "no camera picture from the phone"}
            return
        bad = None
        for code in self._label_codes(frame):
            if code != self._bad_code:
                self.log(f"QR read: {code[:80]}")
            plan = packmod.plan_scan(self.pack, code)
            if plan["action"] == "reject":
                bad = (code, plan["message"])
                if code != self._bad_code:
                    self._bad_code = code
                    self.log(f"label rejected: {plan['message']}", "WARN")
                continue
            if not req and code == self._last_vin and time.time() - self.done_at < float(
                    self.base_cfg.get("rescan_block_seconds", 15)):
                continue
            self._scan_req = None
            self._start_pack(code)
            self.scan_reply = {"ok": self.state != "idle", "message": self.message}
            return
        if bad:
            self.message = f"{bad[1]}  ({bad[0][:40]})"
            if req:
                self._scan_req, self.scan_reply = None, {"ok": False, "message": self.message}
        elif req and time.time() - req > 4:
            self._scan_req = None
            self.scan_reply = {"ok": False, "message": "no QR code read in the scan square -- hold steady, move closer or enlarge the square"}
            self.log(self.scan_reply["message"], "WARN")

    def effective_roi(self):
        """The red box: the saved square, else a fixed centred box while a VIN / label is being waited for."""
        return self.scan_roi or (DEFAULT_ROI if self.vin_mode else None)

    def _crop_roi(self, img):
        r = self.effective_roi()
        if not r:
            return img
        h, w = img.shape[:2]
        c = img[int(r[1] * h):int(r[3] * h), int(r[0] * w):int(r[2] * w)]
        return c if c.size else img

    def _poll_vin(self, frame):
        """idle/done: a pending VIN (typed / API) wins; otherwise look for a QR / barcode in the frame."""
        if self._pending_vin:
            vin, self._pending_vin = self._pending_vin, None
            return vin
        if frame is not None and time.time() - self._code_t >= 0.4:
            self._code_t = time.time()
            img = self._crop_roi(frame)
            if img is not None:
                for code in read_codes(self.cv2, img, self._codes):
                    code = code.strip()
                    # the car's QR stays in view after the result: ignore the same VIN for a while
                    if code and not (code == self._last_vin and time.time() - self.done_at < float(
                            self.base_cfg.get("rescan_block_seconds", 15))):
                        if self._pk:
                            # with several QR codes in view, only take the one this app understands
                            info, err = packmod.decode_scan(self.pack, code)
                            if err:
                                if code != self._bad_code:
                                    self._bad_code = code
                                    self.message = f"ignored: {code[:40]} ({err})"
                                continue
                        return code
        return None

    def _reset_scan(self):
        if self._pk and self.queue and self.state == "done" and self.queue_i + 1 < len(self.queue):
            self.queue_i += 1
            self.result, self.result_defects, self.dets, self.display = None, [], [], None
            if self._run_queue_task():
                if self.tracker:
                    self.tracker.reset()
                self.log(f"next task {self.queue_i + 1}/{len(self.queue)} for {self.vin}")
                return
        self.queue, self.queue_i, self.code = [], 0, None
        self._bad_code = None
        if self.vin_mode:
            self.state, self.message = "idle", "scan the VIN"
            self.vin, self.task_name = None, None
        elif self.model is None:
            self.state, self.message = "no_model", "no model loaded -- pick a .pt in the dropdown"
        else:
            self.state, self.message = "waiting", "waiting for the phone camera..."
        self.result = None
        self.result_defects = []
        self.dets = []
        self.display = None
        if self.tracker:
            self.tracker.reset()
        if not self.vin_mode and self.model is not None and self.task_name:
            self._hold(self.task_name)      # manual task stays selected; Start begins the next scan
        self.log("next car -- ready for a new " + ("VIN" if self.vin_mode else "scan"))

    def _color(self, name):
        n = name.lower().strip()
        if self.case_cfg.explicit:
            if n in {c.lower().strip() for c in self.case_cfg.ignored}:
                return COLOR_IGN
            if n in {c.lower().strip() for c in self.case_cfg.fail}:
                return COLOR_BAD
            return COLOR_OK
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
        left = min(int(self.case_cfg.scan_seconds), max(0, int(self.case_cfg.scan_seconds - (time.time() - self.started_at))))
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
