"""
case_logic.py -- pass / fail rules for the PC runner. Pure Python (no OpenCV / model imports) so it is
unit-testable anywhere. Mirrors the Flutter live screen's rules:

  OK        every mandatory class is seen (>= conf) and no fail class is seen
  NOT OK    a fail class (neither mandatory nor ignored, or a "not ok" named class) is seen on
            `fail_ticks_needed` frames IN A ROW
  (task builder) a class listed under Fail is always a fail class; Pass = mandatory; Ignore = ignored;
            unassigned classes are Fail when Pass is set
  nothing   anything else -> keep scanning
  timeout   after `scan_seconds`: the last frame decides -- defect -> NOT OK, all mandatory -> OK,
            otherwise NOT FOUND

A detection is a (class_name, confidence) pair.
"""

OK = "OK"
NOT_OK = "NOT OK"
NOT_FOUND = "NOT FOUND"


def _norm(name):
    return str(name).lower().strip()


def _is_not_ok_name(name):
    n = _norm(name).replace("_", " ").replace("-", " ")
    return "not ok" in n or "notok" in n.replace(" ", "")


class CaseConfig:
    def __init__(self, mandatory=None, ignored=None, conf=0.5,
                 fail_ticks_needed=2, pass_ticks_needed=1, scan_seconds=45, fail=None, explicit=False):
        self.explicit = bool(explicit)   # app-pack tasks: ONLY the listed fail classes (and "not ok" names) are defects
        self.fail = [str(c) for c in (fail or [])]   # explicit fail classes (task builder)
        self.mandatory = [str(c) for c in (mandatory or [])]
        self.ignored = [str(c) for c in (ignored or [])]
        self.conf = float(conf)
        self.fail_ticks_needed = max(1, int(fail_ticks_needed))
        self.pass_ticks_needed = max(1, int(pass_ticks_needed))
        self.scan_seconds = float(scan_seconds)


def tick_state(dets, cfg, all_classes=()):
    """Classify ONE frame's detections: 'ok', 'defect' or 'none'.

    With no mandatory classes configured every model class is mandatory and nothing can be a
    fail class (same as the phone app)."""
    mandatory = {_norm(c) for c in cfg.mandatory} or {_norm(c) for c in all_classes}
    ignored = {_norm(c) for c in cfg.ignored}
    seen = {_norm(n) for n, c in dets if c >= cfg.conf}

    defect = any(name in {_norm(c) for c in cfg.fail} and name not in ignored for name in seen)
    if defect:
        return "defect"
    if cfg.mandatory and not cfg.explicit:
        for name in seen:
            if name in ignored:
                continue
            if name not in mandatory or _is_not_ok_name(name):
                defect = True
                break
    else:
        defect = any(_is_not_ok_name(n) and n not in ignored for n in seen)
    if defect:
        return "defect"
    if mandatory and mandatory.issubset(seen):
        return "ok"
    return "none"


def defect_classes(dets, cfg, all_classes=()):
    """Names of the fail classes seen (for logging / the result file)."""
    mandatory = {_norm(c) for c in cfg.mandatory}
    ignored = {_norm(c) for c in cfg.ignored}
    fail = {_norm(c) for c in cfg.fail}
    out = []
    for n, c in dets:
        k = _norm(n)
        if c < cfg.conf or k in ignored:
            continue
        if k in fail or (cfg.mandatory and not cfg.explicit and k not in mandatory) or _is_not_ok_name(n):
            out.append(n)
    return out


class BlurGate:
    """Decides whether a frame is sharp enough to vote. `sharpness` is any "bigger = sharper" number (the PC uses
    the variance of the Laplacian). A frame is blurry when it is clearly below the sharpest recent frame of this
    scene (`ratio`); after `max_skip` blurry frames in a row the next one is accepted anyway, so a scene that
    simply has little detail can never stall the scan."""

    def __init__(self, ratio=0.45, max_skip=20, decay=0.995):
        self.ratio, self.max_skip, self.decay = ratio, max_skip, decay
        self.reset()

    def reset(self):
        self.ref = 0.0
        self.skipped = 0

    def blurry(self, sharpness):
        self.ref = max(sharpness, self.ref * self.decay)
        if self.ref > 0 and sharpness < self.ref * self.ratio and self.skipped < self.max_skip:
            self.skipped += 1
            return True
        self.skipped = 0
        return False


class CaseTracker:
    """Feed it one frame's detections at a time; returns a result string when the case resolves."""

    def __init__(self, cfg, all_classes=()):
        self.cfg = cfg
        self.all_classes = list(all_classes)
        self.reset()

    def reset(self):
        self.ok_count = 0
        self.fail_count = 0
        self.last_state = "none"

    def update(self, dets):
        state = tick_state(dets, self.cfg, self.all_classes)
        self.last_state = state
        if state == "ok":
            self.ok_count += 1
            self.fail_count = 0
            if self.ok_count >= self.cfg.pass_ticks_needed:
                return OK
        elif state == "defect":
            self.fail_count += 1
            self.ok_count = 0
            if self.fail_count >= self.cfg.fail_ticks_needed:
                return NOT_OK
        else:
            self.fail_count = 0
            self.ok_count = 0   # OK frames must be consecutive too
        return None

    def final(self, dets):
        """Deadline reached: decide from the last frame."""
        state = tick_state(dets, self.cfg, self.all_classes)
        if state == "defect":
            return NOT_OK
        if state == "ok":
            return OK
        return NOT_FOUND
