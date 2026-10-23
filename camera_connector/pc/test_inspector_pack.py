"""Queue + scan flow of the Inspector in app-pack mode, with the model loading stubbed out.
Run: python test_inspector_pack.py"""
import tempfile

from inspector import Inspector

VIN = "MA1TA2GKXN2A12345"
PACK = {"format": 1, "app_name": "Demo", "scan_types": ["model"],
        "master_data": [{"model_code": "ABCDEFGHIJKLM12"}],
        "tasks": [{"name": "Headlamp", "model": "models/a.pt", "vehicle_code": "ABCDEFGHIJKLM12"},
                  {"name": "Bonnet", "model": "models/b.pt", "vehicle_code": "ABCDEFGHIJKLM12"}]}

with tempfile.TemporaryDirectory() as d:
    logs = []
    ins = Inspector({}, d, lambda m, lvl="INFO", **k: logs.append(m), lambda: (None, -1))
    armed = []
    ins._activate_task = lambda t: (armed.append(t["name"]), setattr(ins, "state", "waiting"), setattr(ins, "task_name", t["name"]), True)[-1]
    ins.load_pack(PACK)
    assert ins.vin_mode

    # a QR from the wrong label is rejected, state stays idle
    ins._start_vin("garbage")
    assert ins.state == "idle" and not ins.queue

    ins._start_vin(VIN + "ABCDEFGHIJKLM12XY")
    assert armed == ["Headlamp"] and ins.vin == VIN and len(ins.queue) == 2, armed
    assert ins.state == "ready", ins.state                 # waits for Start: the operator aims the camera first
    assert ins.start_scan() and ins.state == "waiting"
    assert not ins.start_scan()
    st = ins.status()
    assert st["task_i"] == 1 and st["task_n"] == 2 and st["pack"]["loaded"]

    ins.state = "done"; ins.result = "NOT FOUND"   # nothing found: Retry runs the same task again
    ins.model = object()
    assert ins.retry() and ins.state == "waiting" and ins.result is None and armed == ["Headlamp"]
    ins.state = "done"; ins.result = "OK"          # first task finished, operator presses Submit
    ins._reset_scan()
    assert armed == ["Headlamp", "Bonnet"] and ins.vin == VIN and ins.state == "ready"
    assert ins.status()["task_i"] == 2

    ins.state = "done"; ins.result = "NOT OK"      # last task: Submit goes back to scanning the next label
    ins._reset_scan()
    assert ins.state == "idle" and ins.vin is None and not ins.queue

    # unmapped code with auto-submit: OK without a task, saved to results.csv
    ins.load_pack(dict(PACK, unmapped_code_auto_submit=True))
    ins._start_vin(VIN + "NOPENOPENOPE999XY")
    assert ins.state == "done" and ins.result == "OK", (ins.state, ins.result)
    assert "unmapped" in open(d + "/results/results.csv").read()
    ins._reset_scan()
    assert ins.state == "idle"

    assert ins.set_roi([0.1, 0.1, 0.6, 0.5]) and ins.scan_roi == [0.1, 0.1, 0.6, 0.5]
    assert not ins.set_roi([0.1, 0.1, 0.11, 0.5])
    assert ins.set_roi(None) and ins.scan_roi is None
    # auto-start on: the next task starts by itself
    ins.set_auto_start(True)
    ins._start_vin(VIN + "ABCDEFGHIJKLM12XY")
    assert ins.state == "waiting"
    ins.state = "done"; ins._reset_scan()
    assert ins.state == "waiting" and ins.status()["task_i"] == 2

    # modes: manual ignores the pack, pack mode needs a pack
    ins.state = "idle"; ins.queue = []
    assert ins.set_mode("manual") and not ins.vin_mode and ins._pk is None
    ins._apply_mode()
    assert ins.state in ("no_model", "waiting")
    assert ins.set_mode("pack") and ins.vin_mode
    ins.load_pack(None)
    assert not ins.set_mode("pack")
    # Scan button: nothing happens until it is pressed; then good / bad / missing labels each give a clear reply
    ins.set_auto_start(False); ins.set_mode("manual")
    ins.load_pack(PACK, switch=True)
    ins.state, ins.queue = "idle", []
    ins._label_codes = lambda f: [VIN + "ABCDEFGHIJKLM12XY"]
    ins._pack_idle(object())
    assert ins.state == "idle"                                   # no request, auto-scan off
    ins._label_codes = lambda f: ["not-a-vin"]
    ins.request_scan(); ins._pack_idle(object())
    assert ins.scan_reply and not ins.scan_reply["ok"] and "not-a-vin" in ins.scan_reply["message"], ins.scan_reply
    ins._label_codes = lambda f: [VIN + "NOPENOPENOPE999XY"]
    ins.request_scan(); ins._pack_idle(object())
    assert not ins.scan_reply["ok"] and "master data" in ins.scan_reply["message"], ins.scan_reply
    ins._label_codes = lambda f: []
    ins.request_scan(); ins._scan_req -= 10; ins._pack_idle(object())
    assert not ins.scan_reply["ok"] and "no QR" in ins.scan_reply["message"], ins.scan_reply
    ins._label_codes = lambda f: ["noise", VIN + "ABCDEFGHIJKLM12XY"]     # several QR codes: the valid one wins
    ins.request_scan(); ins._pack_idle(object())
    assert ins.scan_reply["ok"] and ins.state == "ready" and ins.vin == VIN, (ins.scan_reply, ins.state)
print("inspector pack tests passed")
