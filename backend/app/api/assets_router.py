import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from app.config import settings

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("/reference-image")
async def upload_reference_image(file: UploadFile = File(...)):
    """Upload a reference/master image for a task. Returns the stored filename."""
    settings.reference_images_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower() if file.filename else ".jpg"
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="Only image files are allowed (jpg, png, webp).")

    filename = f"{uuid.uuid4()}{ext}"
    dest = settings.reference_images_dir / filename

    content = await file.read()
    dest.write_bytes(content)

    return {"filename": filename}


@router.get("/reference-image/{filename}")
def get_reference_image(filename: str):
    """Serve a stored reference image by filename."""
    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    path = settings.reference_images_dir / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found.")

    return FileResponse(str(path))
