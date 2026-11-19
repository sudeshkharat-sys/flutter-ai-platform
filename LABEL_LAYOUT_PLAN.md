# Label layout settings (printer) - plan / memory

Status: **idea agreed, not built yet.** Notes so we remember the decisions.

## Why
How the Zebra label prints is trial and error. Today every size is hard-coded
in `backend/app/codegen/templates/print_service.dart.j2` (`_buildZpl`), so each
tweak means a code change + rebuild. Instead: edit the label inside the app
until it looks right, then bake the final values in as the app-builder default.

## Idea
- New **Label layout** option in app **Settings**, next to the existing PC and
  Wi-Fi options / printer setup.
- Shows the label as a list of rows, starting from the current defaults
  (title, VIN, date + time, status, failed tasks, footer).
- **Add row** button: inserts a new row above/below a chosen row (e.g. above
  "DIGITAL EYE INSPECTION REPORT"); user types its text (e.g. a custom task name).
- Per row: **+ / -** buttons for text size.
- Saved on the phone (like printer DPI).

## Decisions (agreed)
1. **Live-value rows cannot be deleted** (VIN, date, time, status, failed
   tasks). They can be moved, resized or hidden. Only **custom rows** can be
   edited or deleted.
2. **Reset to default** button.
3. **VIN fit, not limit:** long values (radiator traceability such as
   `:DH045B :171126: B:51164`) shrink automatically to fit the row width; the
   chosen size is the maximum. No hard character limit that cuts text.
4. **No separate test-print button.** Not needed: do real inspections and print
   with live values to test.

## Later (ideas, not decided)
- Move up / down arrows on each row, show/hide switch, left/center alignment,
  optional line under a row.
- Copy layout between phones; or bake final layout into the app builder default
  (`generator.py` / `AppBuilder.js`) once the right sizes are known.
- Label width / length setting (code assumes 4 in = 1200 dots at 300 dpi).
- Same layout for PC / Wi-Fi printing (PC pack print path not checked yet).

## Open questions
- Where the custom task name goes (under the title vs above the status) and
  whether it is set per app, per task, or typed in the app.
- Does the PC ever print this label?
- Real sticker width (photo shows two small side-by-side labels).
