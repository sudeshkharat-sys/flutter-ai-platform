"""
pack.py -- the "app pack" exported from the Flutter platform ("Download for PC"), and the scan logic in it.
Pure Python (no OpenCV / model imports) so it is unit-testable anywhere.

Pack zip layout:
    runner_pack.json     scan settings + master data + tasks (below)
    models/<name>.pt     the task models

runner_pack.json:
    format                1
    app_name              name of the platform app
    scan_types            ["model"] | ["engine"] | ["chakan"] | a mix for a combined app
    skip_masterdata_validation   accept any scanned code (no master-data check)
    unmapped_code_auto_submit    a code with no task is auto-OK instead of rejected
    detection_method      "default" | "multiclass"
    master_data           [{model_code, platform_name}]   -- VIN / chakan model codes
    engine_data           [{part_no, model_name}]          -- engine part numbers
    tasks                 [{name, model, vehicle_code, pass[], fail[], ignore[], conf, scan_seconds}]

The decoding rules are the ones in the generated phone app (scan_screen.dart.j2):
    model   VIN scan : 30-35 chars, VIN = first 17, model code = rest minus 0-2 trailing junk chars
    chakan           : VIN_MODELCODE_GARBAGE, model code resolved the same way
    engine           : "PART_NO SERIAL_NO", the part number is the code
A task belongs to a scanned code when task.vehicle_code == code (no code -> every task).
"""

import json
import os
import shutil
import zipfile

PACK_FILE = "runner_pack.json"
LOCAL_PACK = "pack.json"          # where the PC keeps the imported pack (next to the exe)
FORMAT = 1


def _up(s):
    return str(s or "").strip().upper()


def _known_model_code(pack, code):
    return bool(code) and any(_up(m.get("model_code")) == _up(code) for m in pack.get("master_data", []))


def _known_part(pack, part):
    return bool(part) and any(_up(m.get("part_no")) == _up(part) for m in pack.get("engine_data", []))


def resolve_model_code(pack, suffix):
    """Strip 2, then 1, then 0 trailing junk characters until a known model code is left."""
    for strip in (2, 1, 0):
        if len(suffix) > strip:
            cand = suffix[:len(suffix) - strip]
            if _known_model_code(pack, cand):
                return cand
    return suffix[:-2] if len(suffix) > 2 else suffix


def _decode_one(pack, kind, raw):
    """-> {type, id, code} or None when raw does not have this scan type's shape."""
    if kind == "model":
        if not 30 <= len(raw) <= 35:
            return None
        return {"type": "model", "id": raw[:17], "code": resolve_model_code(pack, raw[17:])}
    if kind == "chakan":
        parts = raw.split("_")
        if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
            return None
        return {"type": "chakan", "id": parts[0].strip(), "code": resolve_model_code(pack, parts[1].strip())}
    if kind == "engine":
        sp = raw.find(" ")
        if sp == -1:
            return None
        return {"type": "engine", "id": raw[sp + 1:].strip(), "code": raw[:sp].strip()}
    return None


def decode_scan(pack, raw):
    """Decode one scanned code. -> (info, error). info = {type, id, code, known}; exactly one is None."""
    raw = (raw or "").strip()
    if not raw:
        return None, "empty code"
    kinds = pack.get("scan_types") or ["model"]
    hit = None
    for kind in kinds:
        hit = _decode_one(pack, kind, raw)
        if hit:
            break
    if not hit:
        return None, {"model": "not a VIN model-code label (expected 30-35 characters)",
                      "chakan": "not a Chakan label (expected VIN_MODELCODE_...)",
                      "engine": "not an engine label (expected PART_NO SERIAL_NO)"}.get(kinds[0], "unrecognised code")
    if pack.get("skip_masterdata_validation"):
        hit["known"] = True
        hit["code"] = ""        # no master data: every task applies (same as the phone app)
    elif hit["type"] == "engine":
        hit["known"] = _known_part(pack, hit["code"])
    else:
        hit["known"] = _known_model_code(pack, hit["code"])
    return hit, None


def tasks_for(pack, code):
    """Tasks of the pack for a model code / part number ('' = all tasks)."""
    tasks = pack.get("tasks", [])
    if not code:
        return list(tasks)
    return [t for t in tasks if _up(t.get("vehicle_code")) == _up(code)]


def plan_scan(pack, raw):
    """What to do with a scanned code. -> dict(action, ...):
         {"action": "reject", "message"}                       unreadable / unknown code
         {"action": "unmapped", "id", "code", "message"}       auto-OK, nothing to inspect
         {"action": "run", "id", "code", "tasks": [...]}       inspect these tasks in order"""
    info, err = decode_scan(pack, raw)
    if err:
        return {"action": "reject", "message": err}
    auto = bool(pack.get("unmapped_code_auto_submit"))
    if not info["known"]:
        if auto:
            return {"action": "unmapped", "id": info["id"], "code": info["code"],
                    "message": f"code {info['code']} not in master data -- auto OK"}
        return {"action": "reject", "message": f"code {info['code']} is not in the master data (add it and re-export)"}
    tasks = tasks_for(pack, info["code"])
    if not tasks:
        if auto:
            return {"action": "unmapped", "id": info["id"], "code": info["code"],
                    "message": f"no task for {info['code']} -- auto OK"}
        return {"action": "reject", "message": f"no task for code {info['code']}"}
    return {"action": "run", "id": info["id"], "code": info["code"], "tasks": tasks}


def summary(pack):
    if not pack:
        return {"loaded": False}
    return {"loaded": True, "app_name": pack.get("app_name"), "scan_types": pack.get("scan_types"),
            "tasks": [t.get("name") for t in pack.get("tasks", [])],
            "skip_masterdata_validation": bool(pack.get("skip_masterdata_validation")),
            "unmapped_code_auto_submit": bool(pack.get("unmapped_code_auto_submit")),
            "codes": len(pack.get("master_data", [])) + len(pack.get("engine_data", []))}


def load_pack(base_dir):
    path = os.path.join(base_dir, LOCAL_PACK)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def clear_pack(base_dir):
    try:
        os.remove(os.path.join(base_dir, LOCAL_PACK))
    except OSError:
        pass


def import_pack(zip_path, base_dir):
    """Unpack an exported zip: models go to base_dir/models/, the settings to base_dir/pack.json.
    -> (ok, message, pack)"""
    try:
        with zipfile.ZipFile(zip_path) as z:
            if PACK_FILE not in z.namelist():
                return False, f"not an app pack (no {PACK_FILE} in the zip)", None
            pack = json.loads(z.read(PACK_FILE).decode("utf-8"))
            if pack.get("format") != FORMAT:
                return False, f"unsupported pack format {pack.get('format')}", None
            models_dir = os.path.join(base_dir, "models")
            os.makedirs(models_dir, exist_ok=True)
            names = [n for n in z.namelist() if n.startswith("models/") and n.lower().endswith(".pt")]
            for n in names:
                dest = os.path.join(models_dir, os.path.basename(n))
                with z.open(n) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile:
        return False, "that file is not a zip", None
    except Exception as e:
        return False, f"could not import: {e}", None
    missing = [t["name"] for t in pack.get("tasks", [])
               if not os.path.exists(os.path.join(base_dir, t.get("model", "")))]
    if missing:
        return False, f"model file missing in the pack for task(s): {missing}", None
    with open(os.path.join(base_dir, LOCAL_PACK), "w", encoding="utf-8") as f:
        json.dump(pack, f, indent=2)
    return True, f"imported {pack.get('app_name')}: {len(pack.get('tasks', []))} task(s), {len(names)} model(s)", pack
