# Flutter AI Studio Platform

Flutter AI Studio is an end-to-end platform designed to automate the creation, configuration, and deployment of Flutter-based AI inspection applications. It enables users to convert AI models to mobile-ready formats, design custom app interfaces via a drag-and-drop builder, and automatically compile ready-to-use Android APKs.

---

## Project Architecture

```text
flutter-ai-platform/
├── backend/                   # Python FastAPI & Celery
│   ├── app/                   # Core Application Logic
│   │   ├── api/               # REST API Endpoints (FastAPI)
│   │   ├── codegen/           # Flutter Code Generation Engine
│   │   │   └── templates/     # Jinja2 Templates for Flutter code
│   │   ├── connectors/        # PostgreSQL Database Connectors
│   │   ├── models/            # SQLAlchemy Database Schemas
│   │   ├── tasks/             # Celery Background Tasks (APK Build, Conversion)
│   │   └── queries.py         # SQL Query Definitions
│   └── requirements.txt       # Backend Dependencies
└── frontend/                  # React.js Web Dashboard
    ├── src/
    │   ├── components/        # React UI Components (AppBuilder, Dashboard)
    │   ├── api.js             # API Client Configuration
    │   └── puckConfig.js      # App Builder Configuration (Puck Editor)
    └── package.json           # Frontend Dependencies
```

---

## Prerequisites & Installation

### 1. Flutter & Android Environment
To build APKs, the server (or development machine) must have the following tools installed and added to the System PATH:

- **Flutter SDK**: [Download Flutter](https://docs.flutter.dev/get-started/install)
- **Java Development Kit (JDK) 17**: Required for Android builds.
- **Android SDK & Command Line Tools**:
  - `cmdline-tools` (latest)
  - `platform-tools` (adb)
  - `build-tools`
  - Android SDK Platform (e.g., API 34)

**Configuration Check:**
Run `flutter doctor` to ensure your environment is correctly set up.

### 2. Databases & Services
- **PostgreSQL**: Primary data store for projects, models, and master data.
- **Redis**: Required for Celery as a message broker and result backend.

### 3. Python Environment
- **Python 3.10+**
- Install dependencies:
  ```bash
  cd backend
  pip install -r requirements.txt
  ```

### 4. Node.js Environment
- **Node.js 18+**
- Install dependencies:
  ```bash
  cd frontend
  npm install
  ```

---

## Running the Application

### Backend (Port 8001)
Start the FastAPI server with auto-reload for development:
```bash
cd backend
uvicorn app.main:app --host 0.0.0.1 --port 8001 --reload
```

### Celery Worker
Start the Celery worker to process model conversions and APK builds:
```bash
cd backend
celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
```

### Frontend (Port 3001)
Start the React development server:
```bash
cd frontend
npm start
```

---

## Workflow Guide

1. **Master Data Setup**:
   - Navigate to the **Master Data** section.
   - Add vehicle models, codes, and variants. This data is used for VIN decoding and filtering within the mobile app.
2. **AI Model Management**:
   - Go to **Models Browser**.
   - Upload or import your AI model (`.pt` YOLO format).
   - Click **Convert** to transform the model into `.tflite` (mobile format).
3. **App Creation**:
   - Create a **New App**.
   - Select the converted TFLite model(s).

4. **Build APK**:
   - Once configured, click the **Build** button.
   - The backend will:
     - Generate a full Flutter project from Jinja2 templates.
     - Run `flutter pub get`.
     - Run `dart run build_runner build` for database code generation.
     - Compile the project using `flutter build apk --release`.
   - Once complete, download the generated APK directly from the dashboard.

---

## Modifying Mobile Templates

If you want to add new screens or modify existing mobile UI components, navigate to:
`backend/app/codegen/templates/`

- **Adding a New Screen**:
  1. Create a new `.dart.j2` file (e.g., `my_custom_screen.dart.j2`).
  2. Register the file path and template name in `backend/app/codegen/generator.py` within the `files` dictionary.
  3. Reference the screen in `main.dart.j2` for routing.

## Environment Variables (.env)
Create a `.env` file in the `backend/` directory based on `.env.example`:
```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=flutter_studio
REDIS_URL=redis://localhost:6379/0
```
