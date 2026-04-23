import uuid
from datetime import datetime
from sqlalchemy import String, JSON, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AppProject(Base):
    __tablename__ = "app_projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    package_name: Mapped[str] = mapped_column(String, nullable=False, default="com.studio.myapp")
    model_asset_id: Mapped[str] = mapped_column(String, ForeignKey("model_assets.id"), nullable=True)
    model_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    inspection_tasks: Mapped[list] = mapped_column(JSON, default=list)
    canvas_state: Mapped[list] = mapped_column(JSON, default=list)
    app_settings: Mapped[dict] = mapped_column(
        JSON,
        default=lambda: {
            "confidence_threshold": 0.5,
            "show_labels": True,
            "show_confidence": True,
            "color_scheme": "dark",
        },
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Build status
    build_status: Mapped[str] = mapped_column(String, nullable=True, default="none") # none, building, ready, error
    build_step: Mapped[str] = mapped_column(String, nullable=True)
    build_log: Mapped[str] = mapped_column(String, nullable=True)
    apk_path: Mapped[str] = mapped_column(String, nullable=True)
    build_error: Mapped[str] = mapped_column(String, nullable=True)
