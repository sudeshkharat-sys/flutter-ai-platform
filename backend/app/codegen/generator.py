import re
import zipfile
import io
import os
import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent / "templates"

_KOTLIN_FALLBACK = "2.1.20"


def _detect_kotlin_version() -> str:
    flutter_roots = [r"C:\flutter", "/flutter", "/usr/local/flutter"]
    env_root = os.environ.get("FLUTTER_ROOT") or os.environ.get("FLUTTER_HOME")
    if env_root:
        flutter_roots.insert(0, env_root)

    for root in flutter_roots:
        candidates = [
            Path(root) / "packages" / "flutter_tools" / "gradle" / "src" / "main" / "groovy" / "flutter.groovy",
            Path(root) / "packages" / "flutter_tools" / "gradle" / "flutter.groovy",
        ]
        for candidate in candidates:
            if candidate.exists():
                text = candidate.read_text(errors="replace")
                m = re.search(r'kotlin[_\-]?version\s*[=:]\s*["\']?([\d.]+)', text, re.IGNORECASE)
                if m:
                    return m.group(1)

    return _KOTLIN_FALLBACK


def _get_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        keep_trailing_newline=True,
    )


def _dart_slug(name: str) -> str:
    """Return a valid Dart package identifier derived from name.

    Dart identifiers must match [a-z][a-z0-9_]* -- they can only contain
    lowercase letters, digits, and underscores, and must start with a
    letter or underscore (not a digit).  Names like '4x4_logo',
    'sc/dc_logo 2.0', or 'my-app' would otherwise break flutter pub get.
    """
    slug = name.lower().replace(" ", "_").replace("-", "_")
    slug = re.sub(r"[^a-z0-9_]", "_", slug)   # replace illegal chars (/, ., etc.)
    slug = re.sub(r"_+", "_", slug).strip("_")  # collapse underscores
    if not slug:
        return "app"
    if slug[0].isdigit():
        slug = "app_" + slug
    return slug


def generate_flutter_project(app_project, model_asset=None, all_model_assets=None) -> bytes:
    """Render all Jinja2 templates and return a ZIP file as bytes."""
    env = _get_jinja_env()
    
    # Handle dict vs object dynamically for backward compatibility if needed, 
    # but primarily expect dicts from the new StateDBConnector
    def get_attr(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    settings = get_attr(app_project, "app_settings") or {}
    
    # Handle multiple models and map them for tasks
    models_list = all_model_assets or ([model_asset] if model_asset else [])
    model_id_to_paths = {}
    for idx, ma in enumerate(models_list):
        ma_id = get_attr(ma, "id")
        model_id_to_paths[ma_id] = {
            "tflite": f"assets/models/model_{idx}.tflite",
            "labels": f"assets/models/labels_{idx}.txt",
            "charset": f"assets/models/charset_{idx}.txt",
            "meta": f"assets/models/meta_{idx}.json",
        }

    models_manifest = []
    inspection_tasks = get_attr(app_project, "inspection_tasks")
    
    if isinstance(inspection_tasks, str):
        import json
        try:
            inspection_tasks = json.loads(inspection_tasks)
        except:
            inspection_tasks = []

    if inspection_tasks:
        for task in inspection_tasks:
            mid = task.get("modelId")
            paths = model_id_to_paths.get(mid, {})
            task_classes = task.get("classes", [])
            if isinstance(task_classes, str):
                try:
                    task_classes = json.loads(task_classes)
                except Exception:
                    task_classes = []

            # Validate task classes against the model's actual trained classes.
            # If none of the configured classes exist in the model, fall back to
            # the model's full class list to prevent silent NOT FOUND mismatches.
            model_for_task = next(
                (ma for ma in models_list if get_attr(ma, "id") == mid), None
            )
            if model_for_task:
                _raw = get_attr(model_for_task, "classes", []) or []
                model_classes = json.loads(_raw) if isinstance(_raw, str) else _raw
                if model_classes:
                    model_classes_lower_set = {c.lower().strip() for c in model_classes}
                    valid = [c for c in task_classes if c.lower().strip() in model_classes_lower_set]
                    if not valid:
                        print(
                            f"[generator] WARNING: task '{task.get('taskName')}' "
                            f"classes {task_classes} not found in model classes "
                            f"{model_classes}. Using model classes as fallback."
                        )
                        task_classes = model_classes

            # Multi-class detection config (only meaningful when detection_method == 'multiclass').
            # mandatoryClasses = the "pass" classes that must be detected for an OK badge.
            # ignoredClasses   = classes that are neither required nor forbidden -- if
            #                    detected or not, they have no effect on the OK/NOT OK result.
            #                    Any model class that is in neither list is still treated as
            #                    a fail class (detecting it forces NOT OK), same as before.
            # classOcrConfig   = per-class OCR verification { className: {ocrEnabled, ocrTargetText} }.
            mandatory_classes = task.get("mandatoryClasses", [])
            if isinstance(mandatory_classes, str):
                try:
                    mandatory_classes = json.loads(mandatory_classes)
                except Exception:
                    mandatory_classes = []
            ignored_classes = task.get("ignoredClasses", [])
            if isinstance(ignored_classes, str):
                try:
                    ignored_classes = json.loads(ignored_classes)
                except Exception:
                    ignored_classes = []
            if model_for_task and mandatory_classes:
                _raw = get_attr(model_for_task, "classes", []) or []
                model_classes = json.loads(_raw) if isinstance(_raw, str) else _raw
                model_classes_lower = {c.lower().strip() for c in model_classes}
                if model_classes_lower:
                    filtered = [c for c in mandatory_classes if c.lower().strip() in model_classes_lower]
                    if filtered:
                        mandatory_classes = filtered
                    # else: names didn't match — keep original selection to avoid an
                    # empty mandatory list (which would fall back to all-classes).
                    ignored_classes = [c for c in ignored_classes if c.lower().strip() in model_classes_lower]

            ref_img = task.get("referenceImage")
            models_manifest.append({
                "name": task.get("taskName") or task.get("modelName"),
                "classes": task_classes,
                "mandatoryClasses": mandatory_classes,
                "ignoredClasses": ignored_classes,
                "classOcrConfig": task.get("classOcrConfig", {}) or {},
                "tflite_path": paths.get("tflite", "assets/models/model_0.tflite"),
                "labels_path": paths.get("labels", "assets/models/labels_0.txt"),
                "vehicle_code": task.get("vehicleCode"),
                "reference_image": f"assets/reference_images/{ref_img}" if ref_img else None,
            })
    else:
        # Fallback to model list if no tasks defined
        for ma in models_list:
            ma_id = get_attr(ma, "id")
            paths = model_id_to_paths.get(ma_id, {})
            models_manifest.append({
                "name": get_attr(ma, "vision_project_name"),
                "classes": get_attr(ma, "classes", []),
                "tflite_path": paths.get("tflite"),
                "labels_path": paths.get("labels")
            })

    # ── OCR app type: resolve the detector + recognizer model bundle ──────
    # An OCR app carries exactly two model_assets: one model_kind="detector"
    # (YOLO plate/char boxes) and one model_kind in ("ocr_cnn", "ocr_crnn")
    # (the recognizer). Both are uploaded pre-built via /models/upload-ocr,
    # so unlike the generic detection app type this resolves by model_kind
    # rather than by inspection_tasks.
    app_type = settings.get("app_type", "sequential")
    # A "combined" app (VIN scan + Chakan/engine scan + OCR all in one app,
    # picked via a capability checklist rather than a single app_type) is a
    # new, separate, opt-in app_type -- app_type == "ocr" (and every other
    # existing app_type) is completely untouched by any of this.
    is_combined_app = app_type == "combined"
    combined_capabilities = settings.get("combined_capabilities") or []
    if isinstance(combined_capabilities, str):
        import json as _json
        try:
            combined_capabilities = _json.loads(combined_capabilities)
        except Exception:
            combined_capabilities = []
    is_ocr_app = app_type == "ocr" or (is_combined_app and "ocr" in combined_capabilities)
    ocr_ctx = {}
    if is_ocr_app:
        ocr_detector = next(
            (ma for ma in models_list if get_attr(ma, "model_kind", "detector") == "detector"), None
        )
        ocr_recognizer = next(
            (ma for ma in models_list if get_attr(ma, "model_kind", "detector") in ("ocr_cnn", "ocr_crnn")), None
        )
        ocr_engine = "crnn"
        if ocr_recognizer is not None:
            ocr_engine = "cnn" if get_attr(ocr_recognizer, "model_kind") == "ocr_cnn" else "crnn"
        else:
            ocr_engine = settings.get("ocr_engine", "crnn")

        det_paths = model_id_to_paths.get(get_attr(ocr_detector, "id"), {}) if ocr_detector else {}
        rec_paths = model_id_to_paths.get(get_attr(ocr_recognizer, "id"), {}) if ocr_recognizer else {}

        ocr_ctx = {
            "ocr_engine": ocr_engine,
            "ocr_detector_tflite": det_paths.get("tflite"),
            "ocr_detector_labels": det_paths.get("labels"),
            "ocr_detector_input_size": get_attr(ocr_detector, "input_size", 640) if ocr_detector else 640,
            "ocr_recognizer_tflite": rec_paths.get("tflite"),
            "ocr_recognizer_charset": rec_paths.get("charset"),
            "ocr_recognizer_meta": rec_paths.get("meta"),
            # The whole-plate/region class name, picked explicitly in the
            # New App UI rather than guessed by naming convention -- used by
            # the CRNN engine when individual character boxes aren't found.
            "ocr_region_class": settings.get("ocr_region_class") or None,
            # Per-app, not hardcoded in the template -- how many characters
            # the engine number should be, used to validate/pick the right
            # substring out of the QR payload and as a sanity check on the
            # OCR read. None (not configured) skips length-based logic.
            "ocr_expected_length": settings.get("ocr_expected_length") or None,
            # If the read doesn't match the truth value, try Google ML Kit
            # on the same crop as a second opinion before giving up.
            "ocr_use_mlkit_fallback": bool(settings.get("ocr_use_mlkit_fallback")),
        }

        # Where the truth value the camera read gets checked against comes
        # from -- 'none' (no truth value, OCR-only like the very first
        # version of this app type), 'qr' (scan the engine's QR/barcode
        # sticker, reusing scan_screen.dart.j2's existing validated
        # engine-code logic), or 'type' (one fixed expected string, typed
        # once at app-build time -- e.g. verifying a stamp that should
        # always read the same thing, not a per-unit serial).
        # ocr_use_qr_truth is kept derived from this (rather than removed)
        # so scan_screen.dart.j2/main.dart.j2's existing checks don't need
        # to change, and apps built before this option existed (which only
        # ever set ocr_use_qr_truth) keep behaving the same way.
        truth_source = settings.get("ocr_truth_source")
        if not truth_source:
            truth_source = "qr" if settings.get("ocr_use_qr_truth") else "none"
        ocr_ctx["ocr_truth_source"] = truth_source
        ocr_ctx["ocr_use_qr_truth"] = truth_source == "qr"
        ocr_ctx["ocr_truth_text"] = settings.get("ocr_truth_text") or None
        # Which barcode format supplies the truth value when truth_source is
        # 'qr' -- 'engine' (PART_NO SERIAL_NO sticker, validated against
        # engine_data.json), 'model' (17-char VIN + model-code suffix,
        # validated against master_data.json, same as a plain VIN scan), or
        # 'chakan' (VIN_MODELCODE_GARBAGE sticker, also validated against
        # master_data.json). Defaults to 'engine' so apps built before this
        # option existed keep behaving exactly the same way.
        ocr_ctx["ocr_truth_scan_type"] = settings.get("ocr_truth_scan_type", "engine")

    app_name = get_attr(app_project, "name", "My App")
    package_name = get_attr(app_project, "package_name", "com.example.app")
    canvas_state = get_attr(app_project, "canvas_state") or []
    
    if isinstance(canvas_state, str):
        import json
        try:
            canvas_state = json.loads(canvas_state)
        except:
            canvas_state = []

    # Falls back to 1 for callers that don't increment/pass a build number
    # (e.g. the plain code export, which isn't installed as an APK update).
    # build_apk_task bumps app_projects.build_number in the DB and passes
    # the new value in here so every built APK gets a strictly-increasing
    # versionCode -- see app_build.gradle.j2 for why that matters.
    version_code = get_attr(app_project, "build_number") or 1

    ctx = {
        "kotlin_version": _detect_kotlin_version(),
        "app_name": app_name,
        "app_name_slug": _dart_slug(app_name),
        "package_name": package_name,
        "version_code": version_code,
        "version_name": f"1.0.{version_code}",
        "classes": get_attr(models_list[0], "classes") if models_list else ["object"],
        "models_manifest": models_manifest,
        "canvas_widgets": canvas_state,
        "confidence_threshold": settings.get("confidence_threshold", 0.5),
        "show_labels": settings.get("show_labels", True),
        "show_confidence": settings.get("show_confidence", True),
        "input_size": get_attr(models_list[0], "input_size") if models_list else 640,
        "model_name": get_attr(models_list[0], "vision_project_name") if models_list else "Custom Model",
        "has_result_list": any(
            w.get("type") == "ResultList" for w in canvas_state
        ),
        "has_confidence_slider": any(
            w.get("type") == "ConfidenceSlider" for w in canvas_state
        ),
        "has_stats_view": any(
            w.get("type") == "StatsView" for w in canvas_state
        ),
        "app_type": app_type,
        "is_ocr_app": is_ocr_app,
        "is_combined_app": is_combined_app,
        # When set, VIN/Engine/Chakan barcode scans skip their "is this code
        # known" check against Master Data/Engine Data entirely and accept
        # whatever was scanned as-is -- an explicit opt-out for apps that
        # don't have that masterdata populated (or don't want the scan
        # rejected), instead of the check being unconditionally required.
        "skip_masterdata_validation": bool(settings.get("skip_masterdata_validation")),
        # OCR apps default to "engine" so the shared history screen shows
        # "Serial"/"Engine Code" labels instead of "VIN"/"Model" -- already
        # built into history_screen.dart.j2, just needs this flag set. A
        # combined app follows the same rule based on which capabilities it
        # actually has (prefers "engine" labels if engine/OCR is present).
        "scan_type": settings.get(
            "scan_type",
            (ocr_ctx.get("ocr_truth_scan_type", "engine") if (is_ocr_app and not is_combined_app)
             else "engine" if (is_ocr_app or (is_combined_app and "engine" in combined_capabilities))
             else "model"),
        ),
        "app_settings": settings,
        # Detection engine selector: 'default' (single-target flow) or
        # 'multiclass' (mandatory-class checklist + per-class OCR verification).
        "detection_method": settings.get("detection_method", "default"),
        **ocr_ctx,
    }

    # OCR codegen is only enabled in multi-class mode AND when at least one
    # mandatory class has OCR reading turned on. Target text is now OPTIONAL
    # (empty = just read and record the text, no pass/fail) -- it used to be
    # required here, which is why this no longer also checks ocrTargetText.
    ctx["ocr_enabled"] = (
        ctx["detection_method"] == "multiclass"
        and any(
            bool((entry.get("classOcrConfig") or {}).get(cls, {}).get("ocrEnabled"))
            for entry in models_manifest
            for cls in (entry.get("mandatoryClasses") or [])
        )
    )

    # Both conditions above are silent all-or-nothing gates: if either fails,
    # every OCR code path is omitted from the generated app entirely, and the
    # build still succeeds. The app then detects boxes normally and simply
    # shows no read text, no truth value and no ML Kit result -- with nothing
    # anywhere explaining why. That's indistinguishable from "OCR is broken",
    # and it's the single easiest way to lose hours on this feature.
    #
    # The most common trip-up is a class with "Read text" ticked that isn't
    # also marked mandatory: ocrEnabled is only ever looked up for classes in
    # mandatoryClasses, so OCR-on-a-non-mandatory-class silently does nothing.
    # Warn loudly whenever OCR was configured somewhere but resolved off.
    if not ctx["ocr_enabled"]:
        _ocr_classes_anywhere = {
            cls
            for entry in models_manifest
            for cls, cfg in (entry.get("classOcrConfig") or {}).items()
            if (cfg or {}).get("ocrEnabled")
        }
        if _ocr_classes_anywhere:
            if ctx["detection_method"] != "multiclass":
                print(
                    "[generator] WARNING: OCR is configured on class(es) "
                    f"{sorted(_ocr_classes_anywhere)} but detection_method is "
                    f"'{ctx['detection_method']}', not 'multiclass' -- ALL OCR code "
                    "(trained recognizer + ML Kit) has been omitted from this build. "
                    "Set Detection Method to 'multiclass' in Add Inspection Profile."
                )
            else:
                _mandatory_anywhere = {
                    cls
                    for entry in models_manifest
                    for cls in (entry.get("mandatoryClasses") or [])
                }
                print(
                    "[generator] WARNING: OCR is configured on class(es) "
                    f"{sorted(_ocr_classes_anywhere)}, but none of them are marked "
                    f"mandatory (mandatory classes are {sorted(_mandatory_anywhere)}) "
                    "-- ALL OCR code has been omitted from this build. A class must be "
                    "mandatory for its 'Read text' setting to take effect."
                )

    # Detection threshold for OCR builds. Character boxes score far lower
    # than whole-object detections, so the app-wide default (0.5) reliably
    # finds the plate and drops its characters -- and with no character boxes
    # the recognizer never receives the tight line crop it was trained on.
    # 0.25 is what the standalone OCR pipeline has always used.
    ctx["ocr_detector_threshold"] = min(float(ctx["confidence_threshold"]), 0.25)
    if ctx["ocr_enabled"] and float(ctx["confidence_threshold"]) > 0.25:
        print(
            "[generator] NOTE: OCR build -- lowering the detector threshold from "
            f"{ctx['confidence_threshold']} to {ctx['ocr_detector_threshold']} so small "
            "character boxes are detected (matches the standalone OCR pipeline)."
        )

    # A class's OCR config can pick the reading engine: 'crnn' (this
    # project's trained model, with ML Kit as a fallback when a target text
    # is set and the CRNN read doesn't match it) or the original 'mlkit'
    # (generic ML Kit only, matched against target text -- kept for any
    # class configured before this option existed, so rebuilding an
    # existing app doesn't silently change its behavior). One CRNN
    # recognizer per app, shared by every CRNN-configured class, resolved
    # the same way the standalone OCR app type resolves its recognizer.
    ctx["ocr_uses_crnn"] = ctx["ocr_enabled"] and any(
        (entry.get("classOcrConfig") or {}).get(cls, {}).get("ocrEngine", "mlkit") == "crnn"
        for entry in models_manifest
        for cls in (entry.get("mandatoryClasses") or [])
        if (entry.get("classOcrConfig") or {}).get(cls, {}).get("ocrEnabled")
    )
    if ctx["ocr_uses_crnn"]:
        _ocr_recognizer = next(
            (ma for ma in models_list if get_attr(ma, "model_kind", "detector") in ("ocr_cnn", "ocr_crnn")), None
        )
        _rec_paths = model_id_to_paths.get(get_attr(_ocr_recognizer, "id"), {}) if _ocr_recognizer else {}
        ctx["ocr_class_engine"] = "cnn" if (_ocr_recognizer and get_attr(_ocr_recognizer, "model_kind") == "ocr_cnn") else "crnn"
        ctx["ocr_class_recognizer_tflite"] = _rec_paths.get("tflite")
        ctx["ocr_class_recognizer_charset"] = _rec_paths.get("charset")
        ctx["ocr_class_recognizer_meta"] = _rec_paths.get("meta")

    # Same "second opinion via ML Kit on a CRNN mismatch" toggle the
    # standalone OCR app type already has (ocr_ctx above) -- reused here for
    # per-class OCR in multiclass/combined apps, which never had a way to
    # turn it off. The ML Kit fallback costs a full extra image-preprocess
    # pass (sometimes two: normal + inverted) plus a synchronous native
    # round trip per box, every time the CRNN's read doesn't match the
    # target -- on an assembly line that's the dominant cost of a capture.
    # Off by default, same as the standalone app's flag, so existing builds
    # don't change behavior on a silent rebuild.
    ctx["ocr_class_use_mlkit_fallback"] = bool(settings.get("ocr_use_mlkit_fallback"))

    # Map of zip path -> template name
    files = {
        "pubspec.yaml": "pubspec.yaml.j2",
        "lib/main.dart": "main.dart.j2",
        "lib/screens/home_screen.dart": "home_screen.dart.j2",
        "lib/screens/scan_screen.dart": "scan_screen.dart.j2",
        "lib/screens/confirmation_screen.dart": "confirmation_screen.dart.j2",
        "lib/screens/component_config_screen.dart": "component_config_screen.dart.j2",
        "lib/screens/inspection_camera_screen.dart": "inspection_camera_screen.dart.j2",
        "lib/screens/history_screen.dart": "history_screen.dart.j2",
        "lib/screens/printer_discovery_screen.dart": "printer_discovery_screen.dart.j2",
        "lib/screens/sync_screen.dart": "sync_screen.dart.j2",
        "lib/services/print_service.dart": "print_service.dart.j2",
        "lib/services/sync_service.dart": "sync_service.dart.j2",
        "lib/services/wifi_service.dart": "wifi_service.dart.j2",
        "lib/database/database.dart": "database.dart.j2",
        "lib/ml/detector.dart": "detector.dart.j2",
        "lib/ml/detection_result.dart": "detection_result.dart.j2",
        "lib/widgets/camera_view.dart": "camera_view.dart.j2",
        "lib/widgets/detection_overlay.dart": "detection_overlay.dart.j2",
        "lib/widgets/result_list.dart": "result_list.dart.j2",
        "lib/widgets/info_card.dart": "info_card.dart.j2",
        "lib/widgets/action_grid.dart": "action_grid.dart.j2",
        "lib/widgets/capture_button.dart": "capture_button.dart.j2",
        "lib/widgets/stats_view.dart": "stats_view.dart.j2",
        "android/app/src/main/AndroidManifest.xml": "AndroidManifest.xml.j2",
        "android/build.gradle": "build.gradle.j2",
        "android/app/build.gradle": "app_build.gradle.j2",
        "android/app/proguard-rules.pro": "proguard-rules.pro.j2",
        # A fixed keystore shared by every generated app, instead of each
        # release build falling back to signingConfigs.debug -- that debug
        # key is auto-generated per build machine/container the first time
        # it's needed, so two builds done on different (or ephemeral) build
        # agents end up signed with different certificates. Android refuses
        # to install a differently-signed APK "update" over an existing
        # install, forcing an uninstall first -- which wipes the app's local
        # storage (paired PC list, device name, unsynced inspections) even
        # though nothing about the app itself actually changed. Signing with
        # this same committed keystore on every build keeps the certificate
        # identical across builds/machines, so installing a new APK over an
        # old one is treated as a normal update and local data survives.
        "android/app/keystore/release.keystore": "keystore/release.keystore.raw",
        "android/settings.gradle": "settings.gradle.j2",
        "android/local.properties": "local.properties.j2",
        "android/gradle.properties": "gradle.properties.j2",
        "android/gradle/wrapper/gradle-wrapper.properties": "gradle-wrapper.properties.j2",
        "android/gradle/wrapper/gradle-wrapper.jar": "gradle-wrapper.jar.raw",
        "android/gradlew": "gradlew.j2",
        "android/gradlew.bat": "gradlew.bat.j2",
        "android/buildSrc/build.gradle": "buildSrc_build.gradle.j2",
        "android/buildSrc/src/main/groovy/FlutterLocalExtension.groovy": "FlutterLocalExtension.groovy.j2",
        f"android/app/src/main/kotlin/{ctx['package_name'].replace('.', '/')}/MainActivity.kt": "MainActivity.kt.j2",
        "android/app/src/main/res/values/styles.xml": "styles.xml.j2",
        "android/app/src/main/res/drawable/launch_background.xml": "launch_background.xml.j2",
    }

    if is_ocr_app:
        files.update({
            "lib/screens/ocr_scan_screen.dart": "ocr_scan_screen.dart.j2",
            "lib/ml/line_normalizer.dart": "line_normalizer.dart.j2",
            "lib/ml/ctc_decoder.dart": "ctc_decoder.dart.j2",
            "lib/ml/ocr_recognizer.dart": "ocr_recognizer.dart.j2",
        })
        # QR truth-value scanning reuses the platform's existing, validated
        # engine-code barcode flow (scan_screen.dart.j2, scan_type="engine")
        # instead of a separate screen -- see that file's "Engine Code Scan
        # Logic" section for the PART_NO/SERIAL_NO parsing + engine_data.json
        # lookup this piggybacks on.

    if ctx["ocr_uses_crnn"] and "lib/ml/line_normalizer.dart" not in files:
        # A plain multiclass app (not app_type "ocr") using the new
        # per-class CRNN OCR option needs these two files too, even though
        # it's not an "is_ocr_app" -- they're not otherwise included.
        files["lib/ml/line_normalizer.dart"] = "line_normalizer.dart.j2"
        files["lib/ml/ctc_decoder.dart"] = "ctc_decoder.dart.j2"

    if is_combined_app:
        # A combined app needs the barcode-scan logic rendered up to THREE
        # times under different class names -- VIN format, Chakan/engine
        # format (leads to the multiclass inspection flow), and OCR's own
        # engine-format QR-truth scan (leads to OcrScanScreen instead) --
        # since scan_type/is_ocr_app are Jinja-time constants that decide
        # which code exists at all, not a runtime value. The single default
        # "lib/screens/scan_screen.dart" entry doesn't apply here; each
        # variant is rendered separately below, after the main files loop,
        # with its own ctx override.
        files.pop("lib/screens/scan_screen.dart", None)
        files["lib/screens/capability_menu_screen.dart"] = "capability_menu_screen.dart.j2"
        ctx["combined_capabilities"] = combined_capabilities

    # Android mipmap icon sizes: density -> (width, height)
    MIPMAP_SIZES = {
        "mipmap-mdpi":    (48,  48),
        "mipmap-hdpi":    (72,  72),
        "mipmap-xhdpi":   (96,  96),
        "mipmap-xxhdpi":  (144, 144),
        "mipmap-xxxhdpi": (192, 192),
    }

    # Fetch all master mappings for generic VIN decoding
    from app.queries import MasterDataQueries
    from app.connectors.state_db import StateDBConnector
    
    master_data_manifest = []
    try:
        db = StateDBConnector()
        master_mappings = db.execute_query(MasterDataQueries.GET_ALL_MAPPINGS)
        for m in master_mappings:
            master_data_manifest.append({
                "platform_name": str(m["platform_name"]),
                "model_code": str(m["model_code"]),
                "description": str(m.get("description", "")) if m.get("description") else ""
            })
    except Exception as e:
        print(f"Warning: Could not fetch master mappings: {e}")

    from app.queries import EngineDataQueries
    engine_data_manifest = []
    try:
        db = StateDBConnector()
        engine_mappings = db.execute_query(EngineDataQueries.GET_ALL_MAPPINGS)
        for m in engine_mappings:
            engine_data_manifest.append({
                "sheet_name": str(m["sheet_name"]),
                "part_no": str(m["part_no"]),
                "model_name": str(m.get("model_name", "")) if m.get("model_name") else "",
                "description": str(m.get("description", "")) if m.get("description") else ""
            })
    except Exception as e:
        print(f"Warning: Could not fetch engine mappings: {e}")

    buf = io.BytesIO()
    root = ctx["app_name_slug"]

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for zip_path, template_name in files.items():
            full_path = f"{root}/{zip_path}"
            if template_name is None:
                zf.writestr(full_path, "")
            elif template_name.endswith(".raw"):
                raw_path = TEMPLATES_DIR / template_name
                if raw_path.exists():
                    zf.writestr(full_path, raw_path.read_bytes())
            else:
                tmpl = env.get_template(template_name)
                content = tmpl.render(**ctx)
                zf.writestr(full_path, content)

        if is_combined_app:
            # Render scan_screen.dart.j2 once per barcode-format capability
            # this app actually has, each as its own class so they can
            # coexist -- see the comment on files.pop(...) above for why.
            scan_tmpl = env.get_template("scan_screen.dart.j2")
            if "vin" in combined_capabilities:
                vin_ctx = {**ctx, "scan_type": "model", "scan_screen_class_name": "VinScanScreen", "is_ocr_app": False}
                zf.writestr(f"{root}/lib/screens/vin_scan_screen.dart", scan_tmpl.render(**vin_ctx))
            if "engine" in combined_capabilities:
                engine_ctx = {**ctx, "scan_type": "engine", "scan_screen_class_name": "EngineScanScreen", "is_ocr_app": False}
                zf.writestr(f"{root}/lib/screens/engine_scan_screen.dart", scan_tmpl.render(**engine_ctx))
            if "chakan" in combined_capabilities:
                chakan_ctx = {**ctx, "scan_type": "chakan", "scan_screen_class_name": "ChakanScanScreen", "is_ocr_app": False}
                zf.writestr(f"{root}/lib/screens/chakan_scan_screen.dart", scan_tmpl.render(**chakan_ctx))
            if "ocr" in combined_capabilities and ocr_ctx.get("ocr_truth_source") == "qr":
                ocr_truth_ctx = {**ctx, "scan_type": ocr_ctx.get("ocr_truth_scan_type", "engine"), "scan_screen_class_name": "OcrTruthScanScreen", "is_ocr_app": True}
                zf.writestr(f"{root}/lib/screens/ocr_truth_scan_screen.dart", scan_tmpl.render(**ocr_truth_ctx))

        import json
        zf.writestr(f"{root}/assets/models_manifest.json", json.dumps(models_manifest, indent=2))
        zf.writestr(f"{root}/assets/master_data.json", json.dumps(master_data_manifest, indent=2))
        zf.writestr(f"{root}/assets/engine_data.json", json.dumps(engine_data_manifest, indent=2))

        icon_src = TEMPLATES_DIR / "icons" / "ic_launcher.png"
        if icon_src.exists():
            try:
                from PIL import Image
                import io as _io
                with Image.open(icon_src) as img:
                    img = img.convert("RGBA")
                    for density, (w, h) in MIPMAP_SIZES.items():
                        resized = img.resize((w, h), Image.LANCZOS)
                        buf_icon = _io.BytesIO()
                        resized.save(buf_icon, format="PNG")
                        zf.writestr(
                            f"{root}/android/app/src/main/res/{density}/ic_launcher.png",
                            buf_icon.getvalue(),
                        )
            except Exception as e:
                print(f"Warning: Could not process app icon: {e}")

        from app.config import settings as app_settings
        for entry in models_manifest:
            ref = entry.get("reference_image")
            if ref:
                filename = ref.split("/")[-1]
                img_path = app_settings.reference_images_dir / filename
                if img_path.exists():
                    zf.writestr(f"{root}/assets/reference_images/{filename}", img_path.read_bytes())

        # pubspec.yaml always declares assets/reference_images/ as a required
        # asset directory, but a zip has no real notion of an empty directory
        # -- one never gets created here unless at least one task actually
        # has a reference image. OCR apps (and any app with none configured)
        # never write anything above, so `flutter pub get` can't find the
        # directory pubspec.yaml promised and logs "unable to find directory
        # entry" (non-fatal on its own, but worth not shipping broken).
        # A trivial always-present placeholder guarantees the directory
        # exists in the zip regardless.
        zf.writestr(f"{root}/assets/reference_images/.gitkeep", "")

    return buf.getvalue()
