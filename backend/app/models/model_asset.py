import uuid
from datetime import datetime
from sqlalchemy import String, JSON, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ModelAsset(Base):
    __tablename__ = "model_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    vision_project_id: Mapped[str] = mapped_column(String, nullable=False)
    vision_project_name: Mapped[str] = mapped_column(String, nullable=False)
    model_type: Mapped[str] = mapped_column(String, nullable=False)  # "seed" or "main"
    classes: Mapped[list] = mapped_column(JSON, default=list)
    pt_path: Mapped[str] = mapped_column(String, nullable=True)
    tflite_path: Mapped[str] = mapped_column(String, nullable=True)
    labels_path: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    error_message: Mapped[str] = mapped_column(String, nullable=True)
    conversion_log: Mapped[str] = mapped_column(String, nullable=True, default="")
    input_size: Mapped[int] = mapped_column(Integer, default=640)
    vision_platform_url: Mapped[str] = mapped_column(String, default="http://localhost:8000")
    vision_platform_token: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
