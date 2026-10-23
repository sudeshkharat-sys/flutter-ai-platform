"""Run: python test_pack.py  (no pytest needed)."""
import json
import os
import tempfile
import zipfile

import pack as P

VIN = "MA1TA2GKXN2A12345"  # 17 chars
assert len(VIN) == 17

BASE = {
    "format": 1, "app_name": "Demo", "scan_types": ["model"],
    "master_data": [{"model_code": "ABCDEFGHIJKLM12", "platform_name": "X"}, {"model_code": "ZZZZZZZZZZZZZ99", "platform_name": "Y"}],
    "engine_data": [{"part_no": "EN-77", "model_name": "E"}],
    "tasks": [{"name": "Headlamp", "model": "models/a.pt", "vehicle_code": "ABCDEFGHIJKLM12", "pass": ["lamp"]},
              {"name": "Bonnet", "model": "models/b.pt", "vehicle_code": "ABCDEFGHIJKLM12", "pass": ["latch"]},
              {"name": "Other", "model": "models/a.pt", "vehicle_code": "ZZZZZZZZZZZZZ99", "pass": ["lamp"]}],
}


def pack(**kw):
    return {**BASE, **kw}


# VIN scan: model code followed by 2 junk chars -> stripped
raw = VIN + "ABCDEFGHIJKLM12" + "XY" 
info, err = P.decode_scan(pack(), raw)
assert err is None and info["id"] == VIN and info["code"] == "ABCDEFGHIJKLM12" and info["known"], (info, err)
# 1 junk char
info, _ = P.decode_scan(pack(), VIN + "ABCDEFGHIJKLM12" + "Q" )
assert info["code"] == "ABCDEFGHIJKLM12"
# wrong length
assert P.decode_scan(pack(), "SHORT")[1]

plan = P.plan_scan(pack(), raw)
assert plan["action"] == "run" and [t["name"] for t in plan["tasks"]] == ["Headlamp", "Bonnet"], plan

# unknown code: rejected, or auto-OK when unmapped_code_auto_submit
unk = VIN + "NOPENOPENOPE999" + "XY" 
assert P.plan_scan(pack(), unk)["action"] == "reject"
assert P.plan_scan(pack(unmapped_code_auto_submit=True), unk)["action"] == "unmapped"
# known code without a task
nt = pack(master_data=BASE["master_data"] + [{"model_code": "NOTASKNOTASK123"}])
assert P.plan_scan(nt, VIN + "NOTASKNOTASK123" + "XY" )["action"] == "reject"
assert P.plan_scan(dict(nt, unmapped_code_auto_submit=True), VIN + "NOTASKNOTASK123" + "XY" )["action"] == "unmapped"

# open scan (skip master data): any code accepted, every task runs
plan = P.plan_scan(pack(skip_masterdata_validation=True), unk)
assert plan["action"] == "run" and len(plan["tasks"]) == 3

# chakan: VIN_MODELCODE_GARBAGE
ck = pack(scan_types=["chakan"])
plan = P.plan_scan(ck, VIN + "_ABCDEFGHIJKLM12XY_junk")
assert plan["action"] == "run" and plan["id"] == VIN and plan["code"] == "ABCDEFGHIJKLM12"
assert P.plan_scan(ck, "nounderscore")["action"] == "reject"

# engine: PART_NO SERIAL
en = pack(scan_types=["engine"], tasks=[{"name": "Eng", "model": "models/a.pt", "vehicle_code": "EN-77"}])
plan = P.plan_scan(en, "EN-77 SER0001")
assert plan["action"] == "run" and plan["id"] == "SER0001" and plan["code"] == "EN-77"
assert P.plan_scan(en, "NOPART SER1")["action"] == "reject"
assert P.plan_scan(en, "nospace")["action"] == "reject"

# combined app: tries each scan type
cb = pack(scan_types=["chakan", "engine", "model"], tasks=BASE["tasks"] + [{"name": "Eng", "model": "models/a.pt", "vehicle_code": "EN-77"}])
assert P.plan_scan(cb, "EN-77 SER1")["action"] == "run"
assert P.plan_scan(cb, VIN + "_ZZZZZZZZZZZZZ99_x")["tasks"][0]["name"] == "Other"
assert P.plan_scan(cb, raw)["action"] == "run"

# import: zip -> pack.json + models/
with tempfile.TemporaryDirectory() as d:
    z = os.path.join(d, "p.zip")
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("runner_pack.json", json.dumps(BASE))
        f.writestr("models/a.pt", b"x")
    out = os.path.join(d, "app")
    os.makedirs(out)
    ok, msg, _ = P.import_pack(z, out)
    assert not ok and "b.pt" in msg or "Bonnet" in msg, msg          # b.pt missing from the zip
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("runner_pack.json", json.dumps(BASE))
        f.writestr("models/a.pt", b"x")
        f.writestr("models/b.pt", b"y")
    ok, msg, pk = P.import_pack(z, out)
    assert ok and os.path.exists(os.path.join(out, "models", "b.pt")), msg
    assert P.load_pack(out)["app_name"] == "Demo"
    P.clear_pack(out)
    assert P.load_pack(out) is None
    open(os.path.join(d, "bad.zip"), "w").write("nope")
    assert not P.import_pack(os.path.join(d, "bad.zip"), out)[0]

print("pack tests passed")
