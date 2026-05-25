import re
import zipfile
import io
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent / "templates"


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
            "labels": f"assets/models/labels_{idx}.txt"
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

            # Validate task classes against the model's actual trained classes.
            # If none of the configured classes exist in the model, fall back to
            # the model's full class list to prevent silent NOT FOUND mismatches.
            model_for_task = next(
                (ma for ma in models_list if get_attr(ma, "id") == mid), None
            )
            if model_for_task:
                model_classes = get_attr(model_for_task, "classes", []) or []
                if model_classes:
                    valid = [c for c in task_classes if c in model_classes]
                    if not valid:
                        print(
                            f"[generator] WARNING: task '{task.get('taskName')}' "
                            f"classes {task_classes} not found in model classes "
                            f"{model_classes}. Using model classes as fallback."
                        )
                        task_classes = model_classes

            ref_img = task.get("referenceImage")
            models_manifest.append({
                "name": task.get("taskName") or task.get("modelName"),
                "classes": task_classes,
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

    app_name = get_attr(app_project, "name", "My App")
    package_name = get_attr(app_project, "package_name", "com.example.app")
    canvas_state = get_attr(app_project, "canvas_state") or []
    
    if isinstance(canvas_state, str):
        import json
        try:
            canvas_state = json.loads(canvas_state)
        except:
            canvas_state = []

    ctx = {
        "app_name": app_name,
        "app_name_slug": _dart_slug(app_name),
        "package_name": package_name,
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
        "app_type": settings.get("app_type", "sequential"),
        "scan_type": settings.get("scan_type", "model"),
        "app_settings": settings,
    }

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
        "lib/services/print_service.dart": "print_service.dart.j2",
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

    return buf.getvalue()
