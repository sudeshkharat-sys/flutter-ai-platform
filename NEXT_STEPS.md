# Next steps -- Flutter app (lock / unlock inspection)

Branch: `claude/yolo26-end2end-decode` (off `sudesh/live-feed-runner-experiment`). Companion notes for the
training side: `docs/NEXT_STEPS.md` in the ai-vision-platform repo.

**Nothing below has been compiled or run yet** (no Flutter SDK in the authoring environment). First job next
session: pull this branch, rebuild the APK, and run the checklist at the bottom.

## Use case
Phone placed inside a car bonnet; a Max_reg region is detected, then a YOLO-cls model says `locked` / `unlocked`.
Hands are in the bonnet (shake, dark, can't watch the screen), so decisions must be fast and robust.

## Already built (this branch)
- YOLO26 end-to-end output decoder (`[1,N,6]`) -- cause of "hundreds of boxes".
- Model kind `classifier` (Models upload, New App screen, Add Model modal); `/models/upload` now saves `model_kind`.
- Per-class **Classify region** (pass label, min confidence, crop margin) in the App Builder; classifier picked
  from the library and attached on save.
- Result panel shows PREDICTED / EXPECTED / status like OCR; label drawn on the box; saved in history + sync.
- Classifier runs in photo mode and in live mode (inside the tick isolate).
- Live decision: **no voting** (detector or classifier) -- the first tick with a confident answer decides. One 50% threshold everywhere (detector `_watchConfidenceThreshold`, classifier default min conf). Voting to be reconsidered after on-site testing.
- Live debug log per tick: total ms, decode/detect/classify ms, frame size, rolling FPS, detections with `[cls label %]`.
- Torch: single tap = this task only; double tap = always on for VIN scan + photo + live (session only).
- Camera zoom button (1x/1.5x/2x/3x, remembered via shared_preferences).

## Backlog (priority order)
1. **In-APK settings screen** (long-press torch or menu): votes needed, OK / NOT OK confidence, timeout,
   zoom, live camera quality. Saved on the phone; defaults come from the App Builder; reset button.
   Goal: tune on site without rebuilding.
2. **Detector + classifier agreement for OK**: OK only if classifier = locked AND detector sees both closed locks
   (LHS_Closed, RHS_Closed) and neither open lock. Biggest guard against a false OK.
3. **Vote gating**: skip blurry frames (cheap sharpness score), only vote when the Max_reg box is steady
   (IoU with previous tick), lock focus/exposure after first good frame.
4. **Result feedback without looking**: vibrate + beep, different pattern for OK / NOT OK.
5. **Shorter timeout** (45 s today, `_watchTimeout`), configurable.
6. **Live resolution**: live camera is `ResolutionPreset.low` (~320x240). Options: raise to medium/high; or keep
   detection on the small frame and crop the Max_reg region from the full-resolution frame for the classifier.
7. **Save on-site frames** (esp. NOT OK / uncertain / not found) and get them off the phone for retraining.
8. Possible third class `unclear` (needs a retrained classifier) so garbage frames can never pass.

## Open questions
- Should an OK vote keep a stricter confidence than a NOT OK vote? (currently 0.75 vs class minimum 0.6)
- Stop button in the classifier trainer currently discards the model -- keep best-so-far instead?
- Were the classifier and detector trained with Preprocess ON? The phone does not apply it (retrain with it off).

## Verification checklist (after rebuilding the APK)
- [ ] Classify region checkbox is clickable; classifier appears in the dropdown (re-upload if model kind was saved wrong).
- [ ] Photo mode: PREDICTED / EXPECTED panel shows; locked -> OK, unlocked -> NOT OK; history shows the label.
- [ ] Live mode: label on the box, decision within ~2-3 frames; shaky frames don't flip the result.
- [ ] Torch: single tap resets per task; double tap stays on across VIN scan, photo and live; orange ring shown.
- [ ] Zoom button cycles and is remembered after restart.
- [ ] Debug console shows `[Classifier]` lines; no "classifier did not run".


## Update: live decision, speed and feedback (untested -- needs an APK rebuild)
- **Live decision (detector-only tasks):** OK = all mandatory classes seen, no fail class. NOT OK = a fail class
  (anything neither mandatory nor ignored) seen on `_failTicksNeeded = 2` ticks in a row. Anything else keeps
  scanning. Tasks with "Classify region" still use the classifier as before.
- **Timeout (45 s):** the last frame now goes through a full image detection (like a photo); a defect there gives
  NOT OK, NOT FOUND only if nothing relevant is detected.
- **Camera:** `ResolutionPreset.high` (720p); detection still runs on a 640-wide downscale. Auto focus + exposure set explicitly.
- **Blur gate:** `_skipBlurryFrame` skips ticks below `_minSharpness = 2.0` (max 4 in a row). The value is guessed:
  read the "Skipped blurry frame (sharpness X)" log lines on site and tune it.
- **Threads:** auto = min(4, cores). Debug console has a `Threads:` button cycling auto/2/4/6/8 (saved, reloads the
  interpreters). Compare the `detect` ms in the tick log.
- **Feedback:** vibration + beep on every final result (OK one long buzz; NOT OK three short; NOT FOUND two medium),
  via Flutter HapticFeedback/SystemSound (no new plugin). Depends on the phone's touch-feedback setting.
- Only the multiclass live screen got the decision/threads/feedback changes; the non-multiclass variant only got
  720p, focus mode and the blur gate.

## Update: detection speed (untested -- needs an APK rebuild)
Field log showed `detect` ~1.3-2.1 s per tick (decode only ~40 ms), only ~2.7 cores busy. Changes in `detector.dart.j2`:
- Tick log now splits detect into `[prep / run / post]` ms (prep = resize + float conversion, run = model + output copy,
  post = box parsing + NMS). Use it to see which stage still dominates.
- `_imageToFloat32` reads the resized image's raw RGB bytes in one pass (no per-pixel `getPixel`).
- Detect-only models: input passed as flat floats and output read as raw bytes (`_runFlat`), exposed as per-row
  `Float32List` views, so the parsers are unchanged. Segmentation models and any unexpected tensor layout fall back to
  the old nested-list path automatically (look for "flat output path unavailable" in logcat).
- To verify on device: same boxes/percentages as before on the same scene, and lower `detect` ms.
- Scope: the faster preprocessing and flat-output path run for LIVE ticks only (`predictFrame`); photo inspection
  (`predictBytes` / `predictImage`) uses the original per-pixel + nested-list path again.

## Update: letterbox + saved model input (untested -- needs an APK rebuild)
- Live ticks now **letterbox** the frame into the model input (aspect kept, grey 114 padding) like Ultralytics
  training; before, a portrait 360x640 frame was stretched to a square. Boxes are mapped back by the existing
  letterbox parser. Photo inspection is unchanged (stretch) unless the app has OCR (already letterboxed).
- Debug console: **Save input** button. The next live tick writes `model_input_<size>.jpg` (exactly what the network
  receives) and `frame_<w>x<h>.jpg` to `Pictures/MAI/<app>/DebugInput/`. Run model_input through the Python `.pt`
  model: detects well there -> the problem is in the app / TFLite export; fails there too -> training data issue.

## Update: last scan debug log (untested -- needs an APK rebuild)
- `ScanLog` (camera_prefs.dart) keeps the FULL debug log of the most recent scan (up to 1500 lines; the on-screen
  console only shows the newest 50 and disappears with the screen), saved to the phone when the scan screen closes.
- Home bottom bar: **Last scan log** opens it (oldest first, selectable, **Copy all**). Works for live and photo scans.


## Task builder inside the PC Runner exe (camera_connector) -- BUILT (Pass/Fail/Ignore), untested on real hardware
Built: tasks dialog, `inspection_tasks.json`, phone PC-VIEW window + torch + zoom. Still to do: phone OK/NOT OK banner + vibrate, Region classes. Original plan below.
VIN scanning is **out of scope for now** (add later). One task is active at a time, picked in the viewer.

**Where it lives:** the task list is stored in `tasks.json` next to the exe, but is created/edited in the
viewer page (a "Tasks" panel), never by hand. Nothing is sent to the phone except task name + result.

**Task builder flow (viewer page)**
1. Pick a `.pt` from the model dropdown -> the exe loads it and lists its class names.
2. For each class choose a role:
   - **Pass** (mandatory): must be seen for OK.
   - **Fail**: seeing it = NOT OK (default for any class not marked pass/ignore).
   - **Ignore**: never counts.
   - **Region** (optional extra on a Pass class): the box must be inside a rectangle you draw on the live
     frame (drag on the preview). "Region" = the part of the screen where that class is allowed to be; a box
     elsewhere does not count. Saved as fractions 0..1 (x1,y1,x2,y2) so it survives resolution changes.
3. Set min confidence per class (default 0.5) and scan seconds, name the task, **Save**.
4. Activate a task from a dropdown (replaces model dropdown + VIN matching for now).

**`tasks.json` shape (per task):** `name, model, scan_seconds, classes: {Name: {role: pass|fail|ignore, conf, region?: [x1,y1,x2,y2]}}`.
Backward compatible: old `mandatory/ignored` keys are read and converted.

**Rule (extends `case_logic.py`):** OK = every pass class seen (and inside its region, if set) and no fail class;
NOT OK = fail class on 2 frames in a row; timeout = last frame decides, NOT FOUND if nothing relevant.

**Phone side (same step):**
- Poll `GET http://<pc>/api/inspection` ~1/s (state `scanning|OK|NOT_OK|NOT_FOUND`, task name) -> big
  green/red banner + vibrate/beep (1 = OK, 2 = NOT OK, 3 = not found).
- Small live preview in the app: PC's annotated `/snapshot` polled 3-5 fps (optional toggle, low fps to
  keep the phone cool) so the user sees what the PC sees, boxes included.

**Build order:** (a) `/api/inspection` fields + phone banner/feedback, (b) phone preview, (c) task builder
UI + region drawing + `case_logic` region/conf support + tests in `test_case_logic.py`.
