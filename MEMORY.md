# MEMORY

Running notes for ideas and decisions so we don't lose them. Newest at the bottom.
Add a new `##` section per idea; mark it built when done.

## Radiator scan (done, pushed)
- Model code = longest saved model code the scan starts with; everything after it = radiator traceability (VIN).
- A QR starting with no saved model code is rejected ("Invalid radiator barcode").
- Samples: `HR200L5GAA1017/11/260122:08:53`, `HR200L5GAA 416/11/260121:11:45`, `HR200L5GAA :DH045B :171126: B:51164`.
- Code: `backend/app/codegen/templates/scan_screen.dart.j2` (`_radiatorModelPrefix`).

## Label layout settings (printer)

Status: **first version written (untested - no Flutter SDK here, needs an APK rebuild).** Files: `label_layout_screen.dart.j2` (screen), `print_service.dart.j2` (`LabelRow`, saved layout, `_buildZpl` driven by rows), tile in `settings_screen.dart.j2`.

### Why
How the Zebra label prints is trial and error. Today every size is hard-coded
in `backend/app/codegen/templates/print_service.dart.j2` (`_buildZpl`), so each
tweak means a code change + rebuild. Instead: edit the label inside the app
until it looks right, then bake the final values in as the app-builder default.

### Idea
- New **Label layout** option in app **Settings**, next to the existing PC and
  Wi-Fi options / printer setup.
- Shows the label as a list of rows, starting from the current defaults
  (title, VIN, date + time, status, failed tasks, footer).
- **Add row** button: inserts a new row above/below a chosen row (e.g. above
  "DIGITAL EYE INSPECTION REPORT"); user types its text (e.g. a custom task name).
- Per row: **+ / -** buttons for text size.
- Saved on the phone (like printer DPI).

### Decisions (agreed)
1. **Live-value rows cannot be deleted** (VIN, date, time, status, failed
   tasks). They can be moved, resized or hidden. Only **custom rows** can be
   edited or deleted.
2. **Reset to default** button.
3. **VIN fit, not limit:** long values (radiator traceability such as
   `:DH045B :171126: B:51164`) shrink automatically to fit the row width; the
   chosen size is the maximum. No hard character limit that cuts text.
4. **No separate test-print button.** Not needed: do real inspections and print
   with live values to test.

### Later (ideas, not decided)
- Move up / down arrows on each row, show/hide switch, left/center alignment,
  optional line under a row.
- Copy layout between phones; or bake final layout into the app builder default
  (`generator.py` / `AppBuilder.js`) once the right sizes are known.
- Label width / length setting (code assumes 4 in = 1200 dots at 300 dpi).
- Same layout for PC / Wi-Fi printing (PC pack print path not checked yet).

### Open questions
- Where the custom task name goes (under the title vs above the status) and
  whether it is set per app, per task, or typed in the app.
- Does the PC ever print this label?
- Real sticker width (photo shows two small side-by-side labels).

## Mono photo app for glare (idea, not started)

Status: **idea only, not built.** Decided 2026-11-27.

### Why
Reflection makes inspection results bad: under light a black part can look
silver and a silver part can look black.

### Facts we agreed on
- Normal phone sensors only capture color. "Black and white" at capture is a
  gray conversion of a color photo, and even a true mono sensor does not remove
  glare (glare is light bouncing into the lens). Gray cannot be turned back into
  the real colors, but gray can always be made from color.
- So: keep the **color photo**, and use the **mono (gray) version** for the model.

### Plan
- Build a **simple app** that takes the **monochrome photo** and **saves the
  color version in the background**.
- Glare-free steps (locked exposure / ISO / white balance, diffuse light,
  polarizing filter, same angle and distance, 2-3 exposure burst, train on
  glare photos) are added **step by step later, not now**.
- Open questions: standalone app or a mode in the generated app; fixed lighting
  or not; need a sample "silver looks black" photo.

## Hide confidence % while inspecting (built, untested - no Flutter SDK here)

- New Settings switch **Show confidence % while inspecting** (default ON).
- OFF only hides the percentage on the inspection screens (photo and live: result
  list, "PREDICTED ... %", box labels). It is **still saved** in history, local
  storage and on the server, and history still shows it.
- Code: `camera_prefs.dart.j2` (`getShowConfidence`), `settings_screen.dart.j2`
  (switch), `inspection_camera_screen.dart.j2` and
  `live_inspection_camera_screen.dart.j2` (`_showConf`),
  `detection_overlay.dart.j2` (`hideConfidence`). Needs an APK rebuild.

---

# Index of the other notes files (full detail stays in each file)

## NEXT_STEPS.md - lock / unlock inspection app
- Use case: phone in a car bonnet; Max_reg region detected, then a YOLO-cls model says locked / unlocked.
- Built: YOLO26 end-to-end decoder, classifier model kind, per-class Classify region, live decision without voting, live debug log, torch, zoom.
- Backlog (priority): 1) in-APK settings screen to tune on site, 2) detector + classifier agreement for OK, 3) vote gating (blur / steady box), 4) vibrate + beep feedback, 5) shorter timeout, 6) higher live resolution, 7) save on-site frames for retraining, 8) possible third class `unclear`.
- Also in the file: open questions, verification checklist after an APK rebuild, and updates on live decision, detection speed, letterbox, last-scan debug log, and the Task builder in the PC Runner exe (built, untested on hardware).
- Status: nothing in it was compiled or run when written.

## PERF_NOTES.md - speed discussions (not implemented unless noted)
- Manual capture feels slow (traced through `_handleCapture`).
- GPU delegate: tried, deliberately OFF, don't reopen.
- Multi-core CPU inference: not implemented, looks safe.
- OCR char-batch + CRNN sequential loop: not implemented, needs care.
- Rule: delete an entry once it is implemented and note the commit.

## LIVE_FEED_RUNNER_PLAN.md - live feed runner.py (staged)
- Why: on-device TFLite live detection can't match the Python sequence viewer (`.pt` via Ultralytics `predict()`).
- Target architecture, status of step 1 ("Runner Cam") and step 2 (model + stop-on-result, simulated only), staged build order, decisions already made, cross-repo note.
