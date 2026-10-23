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
    st = ins.status()
    assert st["task_i"] == 1 and st["task_n"] == 2 and st["pack"]["loaded"]

    ins.state = "done"; ins.result = "OK"          # first task finished, operator presses Submit
    ins._reset_scan()
    assert armed == ["Headlamp", "Bonnet"] and ins.vin == VIN and ins.state == "waiting"
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
print("inspector pack tests passed")
