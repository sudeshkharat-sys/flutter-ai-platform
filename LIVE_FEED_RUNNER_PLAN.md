# Live feed runner.py -- plan (staged, step by step)

Why this exists: on-device (Dart/TFLite) live-tick detection cannot match the
accuracy of the Python sequence viewer in `ai-vision-platform`
(`scripts/sequence_viewer.py`, branch `claude/exe-update-data-safety-plk61b`).
Root cause, not a tuning gap:
- The phone runs a `.tflite` conversion of the `.pt` (Ultralytics YOLOv8)
  weights -- conversion/quantization changes precision and can shift NMS/box
  behavior vs. the original weights.
- `sequence_viewer.py` runs the unconverted `.pt` model through Ultralytics'
  own `predict()` (preprocessing + NMS included), the same code path the
  model was validated with.
- Phone CPU is also far weaker than a PC for repeated inference, which is
  part of why quantization was needed on-device in the first place.

Decision: stop trying to make on-device match Python. Use the phone as a
**camera feed only** (like a CCTV camera) and move real inference to a new
PC-side script, `runner.py`, which reuses `sequence_viewer.py`'s existing
detector + segmenter + checklist logic unchanged.

This is a staged build. Do not skip ahead to model inference before the
connectivity step below is proven end-to-end.

## Target architecture (once fully built)

- **Phone (Flutter app)**: camera capture only, no on-device inference for
  this path. Streams JPEG frames over a WebSocket to the PC.
- **PC (`runner.py`)**: new script, separate process/port from
  `pc_receiver/receiver.py` (that one is the existing "Send to PC" batch
  upload + pairing/vault system -- different purpose, do not merge).
  Receives frames, decodes with `cv2.imdecode`, feeds them into:
  - `detect_frame(detector, segmenter, frame)` from
    `ai-vision-platform/scripts/sequence_viewer.py:713` -- reused as-is,
    both detector and segmenter models loaded (`YOLO(main_best.pt)`,
    `YOLO(seg_main_best.pt)`).
  - `SequenceState` / `evaluate_step` (same file, ~lines 176-493) for the
    pass/fail checklist logic, also reused as-is.
  - `draw_overlay` / `draw_checklist` (same file) to render results, same
    look as the existing viewer.

## Staged build order

1. **[CURRENT STEP] Basic connectivity check, phone camera -> PC.**
   No YOLO models involved yet. Goal: prove a phone can stream raw camera
   frames over the local network to a PC process and the PC can display
   them, reliably, before any inference is layered on top. This isolates
   networking/streaming problems from model problems.
   - PC side: minimal WebSocket server, receives JPEG bytes, decodes with
     OpenCV, shows frame count + `cv2.imshow` (no model).
   - Phone side: minimal camera capture + WebSocket send loop, no on-device
     inference.
   - Done when: sustained live feed visible on PC with no drops for a
     multi-minute session, over both home Wi-Fi and phone hotspot.

2. **Wire in the detector only** (`main_best.pt` via `YOLO()`), draw raw
   boxes on the streamed frames using `detect_frame`. No segmenter, no
   checklist yet.

3. **Add the segmenter** (`seg_main_best.pt`), same `detect_frame` call
   already supports both -- confirm mask overlay renders correctly on
   streamed (compressed/network) frames, not just local webcam frames.

4. **Add checklist/sequence logic** (`SequenceState`, `evaluate_step`) so
   `runner.py` behaves like `sequence_viewer.py`, just fed by network
   frames instead of `cv2.VideoCapture`.

5. (Later, not yet scoped) Recording/export of flagged frames, reusing
   patterns from `pc_receiver`/`pc_receiver_viewer` if needed.

## Open decisions already made

- Both detector and segmenter: yes, load both from the start (step 2/3).
- Checklist logic: yes, wanted eventually (step 4) -- just not in step 1.
- Transport: WebSocket, raw JPEG bytes per frame (not MJPEG-over-HTTP or
  raw TCP) -- simplest to extend later with control messages if needed.

## Cross-repo note

The model + inference logic lives in `ai-vision-platform`
(`claude/exe-update-data-safety-plk61b`), attached read-only in this
session. `runner.py` will need to either import from that repo's
`scripts/sequence_viewer.py` (if both repos are available together at
deploy time) or have the relevant functions vendored/duplicated into
`flutter-ai-platform`. Not yet decided -- revisit before step 2.
