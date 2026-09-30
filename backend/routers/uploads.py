import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from dependencies import get_current_manager

router = APIRouter(prefix="/api/v1", tags=["Uploads"])
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/var/lib/teacafe/uploads"))
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {
    "image/jpeg": (".jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/png": (".png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": (".webp", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
}


@router.post("/admin/uploads", status_code=201)
async def upload_image(
    file: UploadFile = File(...),
    _manager: dict = Depends(get_current_manager),
):
    image_type = IMAGE_TYPES.get(file.content_type or "")
    if not image_type:
        raise HTTPException(status_code=415, detail="Upload a PNG, JPEG, or WebP image")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image must be 5 MB or smaller")
    if not image_type[1](content):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid image")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{image_type[0]}"
    (UPLOAD_DIR / filename).write_bytes(content)
    return {"url": f"/api/v1/uploads/{filename}"}


@router.get("/uploads/{filename}")
async def get_uploaded_image(filename: str):
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Image not found")
    path = UPLOAD_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)
