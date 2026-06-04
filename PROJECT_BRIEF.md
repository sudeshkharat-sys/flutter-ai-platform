# Project Brief: Flutter AI Studio

**Prepared by:** Engineering Team  
**Date:** June 4, 2026  
**Status:** Production-Ready (Active Development)

---

## Executive Summary

Flutter AI Studio is an end-to-end platform that automates the creation, configuration, and deployment of AI-powered mobile inspection applications. It enables non-engineers to upload a trained AI model, design a custom inspection app interface, and receive a ready-to-install Android APK — without writing a single line of code.

The primary use case is **vehicle inspection workflows**, where field teams need customized mobile apps that run AI defect detection models on-device.

---

## Problem Being Solved

Building a custom AI-powered mobile app today requires:
- An ML engineer to convert and optimize the model for mobile
- A Flutter developer to build the app UI and integrate inference
- A DevOps/build engineer to compile and distribute the APK

This creates a bottleneck when teams need to iterate quickly on different inspection workflows, vehicle platforms, or AI model versions. Flutter AI Studio eliminates all three dependencies by automating the full pipeline from model to APK.

---

## What the Platform Does

The platform has three core workflows:

**1. Model Management**
- Upload PyTorch (YOLO) models via the UI
- Platform automatically converts them to TFLite format optimized for Android
- Extracts class labels, stores model metadata

**2. App Builder**
- Visual drag-and-drop interface to design inspection app screens
- Configure inspection tasks, components, and app settings
- Preview layout in a device mockup before building

**3. APK Export**
- One-click APK build that generates a complete Flutter project from the configured layout
- Live build log streaming so users see progress in real time
- Download the finished APK when ready

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI (Python) + PostgreSQL + SQLAlchemy |
| Task Queue | Celery + Redis (for model conversion & APK builds) |
| Frontend | React 18 + Puck Editor (drag-and-drop) |
| Model Conversion | Ultralytics / ONNX / TensorFlow (PT → TFLite) |
| Code Generation | Jinja2 templates → Flutter/Dart source |
| Mobile Build | Flutter SDK + Android Build Tools + JDK 17 |
| Distribution | Windows executable via PyInstaller |

---

## Current State

### Completed & Production-Ready
- Full REST API with model, app, export, and master-data endpoints
- Model conversion pipeline (PyTorch → TFLite) with async progress tracking
- Drag-and-drop app builder UI
- APK build pipeline with live log streaming
- Vehicle master data management (platform codes, VIN decoding reference data)
- Windows desktop deployment (single-file `.exe`, no install required)
- Auto-restart of background workers on crash

### Recent Work (Last Sprint)
- Fixed Windows subprocess issues causing unwanted terminal windows to spawn
- Improved Celery worker stability and crash recovery
- Better error handling for stuck or failed model conversions
- Schema auto-repair for database migrations

### Known Gaps / Next Steps
- Real-time updates use polling; WebSocket upgrades would improve responsiveness
- iOS and web build targets not yet supported (Android only)
- No model versioning or rollback yet
- Batch model upload not yet available
- Advanced reporting/analytics on inspection results not built

---

## Architecture Highlights

- **Async everything:** Long-running tasks (model conversion, APK builds) run in Celery workers backed by Redis, so the UI never blocks
- **Template-based code generation:** The platform generates full Flutter projects from Jinja2 templates at build time, keeping the generation logic centralized and easy to update
- **Flexible data model:** App configurations and canvas state stored as JSONB in PostgreSQL, allowing schema-free iteration on UI components
- **Self-contained deployment:** The entire stack (FastAPI, React SPA, Celery) bundles into a single Windows executable via PyInstaller for easy on-premise distribution

---

## Key Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Flutter SDK / Android toolchain version drift | Pinned SDK versions in build scripts |
| Ultralytics API changes breaking conversion | Pinned to 8.3.0 in requirements |
| Redis unavailability killing async tasks | Worker auto-restart; tasks retry on failure |
| Large model files causing slow uploads | Handled via async file I/O (aiofiles) |
| Windows-only deployment today | Architecture is cross-platform; packaging is the only Windows-specific layer |

---

## Team & Contacts

| Role | Owner |
|---|---|
| Engineering Lead | sudeshkharat26@gmail.com |

---

## Links

- Repository: `sudeshkharat-sys/flutter-ai-platform`
- Active Branch: `claude/clever-darwin-Ox0JB`
