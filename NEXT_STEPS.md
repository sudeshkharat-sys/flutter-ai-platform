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
- Live decision by **votes**: first side to 2 votes wins (<= 3 frames); votes never reset; unsure / region-not-found
  frames cast no vote; an OK vote needs classifier confidence >= 0.75. Constants `_clsVotesNeeded`, `_clsOkVoteConf`
  in `live_inspection_camera_screen.dart.j2`.
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
