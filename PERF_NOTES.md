# Performance notes / open discussions

Running log of speed-related investigations that were discussed but not yet
implemented, so we don't have to re-derive them from scratch next time.
Delete an entry once it's actually implemented (and note the commit).

## Manual capture feels slow (discussed, NOT implemented)

Where: `backend/app/codegen/templates/live_inspection_camera_screen.dart.j2`,
`_handleCapture()` (and its twin in `inspection_camera_screen.dart.j2`),
plus `detector.dart.j2`'s `predictImage()`/model loading.

**What's actually happening per capture** (traced through the code):
1. `_takeFullQualityPicture()` — stop stream, `takePicture()`. Camera stays
   on `ResolutionPreset.low` throughout (capture included) -- so the photo
   itself is small; size isn't the bottleneck.
2. `readAsBytes()`.
3. Isolate #1 -- `_decodeAndCheckBlur`: decode, auto-orient, resize to
   150x150 + manual Laplacian-variance blur check, re-encode oriented image
   as JPEG q92 (`orientedPngBytes`).
4. **Main UI thread (not backgrounded!)**: `img.decodeImage(orientedPngBytes)`
   just to read width/height for `imageSize`. This decode's result
   (`orientedImage`) is passed into `predictImage()` but is **never actually
   used there** when `orientedPngBytes` is already supplied and OCR is off
   (this live screen has no OCR) -- confirmed by reading `predictImage()`'s
   body in `detector.dart.j2`. So this is a full JPEG decode, done
   synchronously on the UI thread, for a value nothing downstream uses.
5. Isolate #2 -- inside `predictImage()`: decode the JPEG again, resize to
   model input size, run TFLite inference.

**Proposed fixes (all zero accuracy impact -- same bytes into inference,
just less redundant work):**
- Return width/height as numbers directly from the blur-check isolate
  instead of re-decoding the JPEG on the main thread in step 4. Kills a
  wasted full decode that currently blocks the UI thread.
- Run the blur-check isolate and inference concurrently (`Future.wait`)
  instead of strictly sequentially, discarding the inference result if the
  blur check comes back bad. Same total work, less wall-clock time since
  independent isolates can run on separate cores instead of back-to-back.

**Do NOT touch:** the blur-check gate itself (real accuracy protection,
not overhead), model input size/resolution, detection thresholds.

## GPU delegate -- already tried, deliberately OFF, don't reopen

`detector.dart.j2` around `Interpreter.fromAsset(modelPath)`:
```
// Force CPU inference on all devices for consistent results.
// GpuDelegateV2 silently produces wrong output on many mid/low-range
// Android GPUs -- all class predictions flip -- so we avoid it entirely.
```
Tested on real hardware, silently flips predictions on some phones. This is
a closed door, not a missed optimization -- leave CPU-only.

## Multi-core CPU inference -- NOT implemented, looks safe

`Interpreter.fromAsset(modelPath)` is called with no `InterpreterOptions`
anywhere in `detector.dart.j2` (main detector, char-only detector, CRNN),
so inference effectively runs single-threaded even on multi-core test
phones. Setting `InterpreterOptions()..threads = N` (e.g. 4) lets TFLite's
CPU (XNNPACK) backend split the same math across cores -- same arithmetic,
just parallelized, unlike the GPU delegate (different hardware/code path,
proven to give wrong answers). Worth trying on all three interpreters
(main, char-only, CRNN) since none currently sets it.

## OCR (CNN char-batch + CRNN) sequential loop -- NOT implemented, needs care

`detector.dart.j2` `predictImage()`/`predictBytes()`, the `for (final det
in detections) { ... await _runOcrOnBbox(...) }` loop (~line 1163 and
~1201): each detection's OCR runs one at a time, each paying its own
isolate hand-off cost before the next starts, even though they're
independent work. OCR being slow is partly expected (clear-text reading is
genuinely heavier than a presence check) -- this is about the *sequential*
part specifically, not the OCR cost itself.

**Caution before touching this:** TFLite interpreters are generally not
safe to call concurrently from multiple isolates on the *same* interpreter
instance -- naively wrapping this loop in `Future.wait` risks corrupting
results, which is worse than slow. Needs either a separate interpreter
instance per concurrent call, or confirming what's actually safe to
parallelize, before changing anything here. Investigate as its own task,
not bundled with the other fixes above.
