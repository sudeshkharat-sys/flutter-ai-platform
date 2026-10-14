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

Note: commit 76b8750 (2-tick NOT OK rule, 720p, blur gate, thread button, vibration) was reverted; it can be re-applied with `git revert` of the revert commit.
