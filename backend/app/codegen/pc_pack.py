"""
pc_pack.py -- "Download for PC": bundles an app's tasks, scan logic and .pt models for the PC runner
(camera_connector/pc, pc_runner.exe). The phone app gets .tflite files; the PC runs the original .pt.

The zip layout and the meaning of every field are documented in camera_connector/pc/pack.py.
"""

import io
import json
import os
import re
import zipfile

PACK_FORMAT = 1


def _j(val, default):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return default
    return default if val is None else val


def _safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "model")).strip("_") or "model"


def build_pack(app, model_assets, master_rows, engine_rows):
    """-> (zip_bytes, warnings). Raises ValueError when there is nothing runnable to export."""
    settings = _j(app.get("app_settings"), {})
    tasks_in = _j(app.get("inspection_tasks"), [])
    warnings = []

    # scan types: a combined app can scan several label kinds
    app_type = settings.get("app_type", "sequential")
    caps = _j(settings.get("combined_capabilities"), [])
    if app_type == "combined" and caps:
        kinds = [k for k in ("chakan", "engine", "model") if k in caps or (k == "model" and "vin" in caps)]
        scan_types = kinds or ["model"]
    else:
        scan_types = [settings.get("scan_type") or ("engine" if app_type == "ocr" else "model")]

    # one .pt per model asset, with a unique file name
    assets = {a["id"]: a for a in model_assets}
    files, used = {}, set()
    for aid, a in assets.items():
        pt = a.get("pt_path")
        if not pt or not os.path.exists(pt):
            continue
        base = _safe(a.get("vision_project_name")) or "model"
        name, n = base, 1
        while name in used:
            n += 1
            name = f"{base}_{n}"
        used.add(name)
        files[aid] = (f"models/{name}.pt", pt)

    conf = float(settings.get("confidence_threshold", 0.5) or 0.5)
    tasks = []
    for t in tasks_in:
        tname = t.get("taskName") or t.get("modelName") or "task"
        mid = t.get("modelId")
        if mid not in files:
            warnings.append(f"task '{tname}': its model has no .pt file on the server -- skipped")
            continue
        model_classes = [str(c) for c in _j(assets[mid].get("classes"), [])]
        mandatory = [str(c) for c in _j(t.get("mandatoryClasses"), [])]
        ignored = [str(c) for c in _j(t.get("ignoredClasses"), [])]
        selected = [str(c) for c in _j(t.get("classes"), [])]
        # Same rule as the phone app: the task's own class list is its universe (a class outside it is never a
        # defect); with Pass classes set, the other listed classes are Fail; with none, every listed class must be seen.
        universe = selected or model_classes
        if mandatory:
            passes = mandatory
            fail = [c for c in universe if c not in mandatory and c not in ignored]
        else:
            passes = [c for c in universe if c not in ignored]
            fail = []
        tasks.append({
            "name": tname,
            "model": files[mid][0],
            "vehicle_code": t.get("vehicleCode"),
            "pass": passes,
            "fail": fail,
            "ignore": ignored,
            "classes": universe,
            "explicit": True,
            "conf": conf,
            "scan_seconds": 45,
        })
    if not tasks:
        raise ValueError("nothing to export: no task with a .pt model file")

    pack = {
        "format": PACK_FORMAT,
        "app_name": app.get("name"),
        "scan_types": scan_types,
        "skip_masterdata_validation": bool(settings.get("skip_masterdata_validation")),
        "unmapped_code_auto_submit": bool(settings.get("unmapped_code_auto_submit")),
        "detection_method": settings.get("detection_method", "default"),
        "master_data": [{"model_code": str(m["model_code"]), "platform_name": str(m.get("platform_name") or "")}
                        for m in master_rows],
        "engine_data": [{"part_no": str(m["part_no"]), "model_name": str(m.get("model_name") or "")}
                        for m in engine_rows],
        "tasks": tasks,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:       # .pt files are already compressed
        z.writestr("runner_pack.json", json.dumps(pack, indent=2))
        for aid, (arc, path) in files.items():
            if any(t["model"] == arc for t in tasks):
                z.write(path, arc)
    return buf.getvalue(), warnings
