"""Run:  python test_case_logic.py   (no dependencies)"""
from case_logic import CaseConfig, defect_classes, CaseTracker, OK, NOT_OK, NOT_FOUND, tick_state

CLASSES = ["Max_reg", "RHS_Closed", "LHS_Closed", "LHS_Open", "RHS_Open"]
CFG = CaseConfig(mandatory=["RHS_Closed", "LHS_Closed"], ignored=["Max_reg"], conf=0.5)


def run(frames, cfg=CFG):
    t = CaseTracker(cfg, CLASSES)
    for i, f in enumerate(frames):
        r = t.update(f)
        if r:
            return r, i + 1
    return None, len(frames)


# --- the four lock combinations the user described
assert run([[("RHS_Closed", .8), ("LHS_Closed", .8)]]) == (OK, 1)
assert run([[("RHS_Closed", .8), ("LHS_Open", .8)]] * 2) == (NOT_OK, 2)
assert run([[("RHS_Open", .8), ("LHS_Closed", .8)]] * 2) == (NOT_OK, 2)
assert run([[("RHS_Open", .8), ("LHS_Open", .8)]] * 2) == (NOT_OK, 2)

# --- one flickering open-lock frame does not fail a good part
r = run([[("LHS_Open", .8)], [("RHS_Closed", .8), ("LHS_Closed", .8)]])
assert r == (OK, 2), r

# --- fail frames must be consecutive
r = run([[("LHS_Open", .8)], [("Max_reg", .8)], [("LHS_Open", .8)], [("Max_reg", .8)]])
assert r[0] is None, r

# --- partial mandatory keeps scanning; ignored class does nothing
assert run([[("LHS_Closed", .9), ("Max_reg", .9)]] * 5)[0] is None
assert tick_state([("Max_reg", .9)], CFG, CLASSES) == "none"

# --- below confidence is not seen
assert tick_state([("LHS_Open", .4), ("RHS_Closed", .9), ("LHS_Closed", .9)], CFG, CLASSES) == "ok"

# --- mixed OK + defect in one frame: defect wins
assert tick_state([("RHS_Closed", .9), ("LHS_Closed", .9), ("LHS_Open", .9)], CFG, CLASSES) == "defect"

# --- timeout decides from the last frame
t = CaseTracker(CFG, CLASSES)
assert t.final([("Max_reg", .9)]) == NOT_FOUND
assert t.final([("LHS_Open", .9)]) == NOT_OK
assert t.final([("RHS_Closed", .9), ("LHS_Closed", .9)]) == OK

# --- no mandatory configured: every class is mandatory, nothing is a fail class
cfg_all = CaseConfig(mandatory=[], conf=0.5)
assert tick_state([(c, .9) for c in CLASSES], cfg_all, CLASSES) == "ok"
assert tick_state([("Max_reg", .9)], cfg_all, CLASSES) == "none"

# --- a class named "not ok" is always a defect unless ignored
cfg_n = CaseConfig(mandatory=["Clip"], conf=0.5)
assert tick_state([("Clip", .9), ("Not_OK", .9)], cfg_n, ["Clip", "Not_OK"]) == "defect"

# --- explicit fail list; pass empty => every class not fail/ignored is mandatory (built by the inspector)
cfg_f = CaseConfig(mandatory=["Lamp_On"], ignored=["Max_reg"], fail=["Crack"], conf=0.5)
assert tick_state([("Lamp_On", .9), ("Crack", .9)], cfg_f, []) == "defect"
assert tick_state([("Lamp_On", .9), ("Max_reg", .9)], cfg_f, []) == "ok"
assert defect_classes([("Crack", .9), ("Max_reg", .9)], cfg_f) == ["Crack"]
cfg_g = CaseConfig(mandatory=[], fail=["Crack"], conf=0.5)   # only a fail list: Crack alone is a defect
assert tick_state([("Crack", .9)], cfg_g, ["Crack", "Lamp_On"]) == "defect"

# --- 3-in-a-row for OK and NOT OK (the PC Runner default)
C3 = CaseConfig(mandatory=["RHS_Closed", "LHS_Closed"], ignored=["Max_reg"], pass_ticks_needed=3, fail_ticks_needed=3)
GOOD, BAD = [("RHS_Closed", .8), ("LHS_Closed", .8)], [("LHS_Open", .8)]
assert run([GOOD] * 3, C3) == (OK, 3)
assert run([GOOD, GOOD, [("Max_reg", .9)], GOOD, GOOD], C3)[0] is None    # a gap resets the count
assert run([BAD] * 3, C3) == (NOT_OK, 3)
assert run([BAD, BAD, GOOD, BAD, BAD], C3)[0] is None

print("all case_logic tests passed")
