from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class WorkerStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    STOPPED = "STOPPED"


class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (
        Index("ix_workers_status_heartbeat", "status", "last_heartbeat_at"),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[WorkerStatus] = mapped_column(
        SqlEnum(WorkerStatus, name="worker_status"),
        nullable=False,
        default=WorkerStatus.ACTIVE,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
