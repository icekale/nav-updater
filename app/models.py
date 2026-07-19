from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_name: Mapped[str] = mapped_column(String(255))
    product_code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    product_type: Mapped[str] = mapped_column(String(20))
    historical_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NavObservation(Base):
    __tablename__ = "nav_observations"
    __table_args__ = (
        UniqueConstraint("product_id", "nav_date", "source_kind", name="uq_nav_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    nav_date: Mapped[date] = mapped_column(Date)
    cumulative_nav: Mapped[Decimal] = mapped_column(Numeric(24, 12))
    source_kind: Mapped[str] = mapped_column(String(30))
    source_ref: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    product: Mapped[Product] = relationship()


class UpdateRun(Base):
    __tablename__ = "update_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    cutoff_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(40), default="uploaded", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    output_path: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    operator: Mapped[User] = relationship()
    files: Mapped[list[RunFile]] = relationship(back_populates="run", cascade="all, delete-orphan")
    items: Mapped[list[RunItem]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RunFile(Base):
    __tablename__ = "run_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("update_runs.id"), index=True)
    file_type: Mapped[str] = mapped_column(String(20))
    original_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))

    run: Mapped[UpdateRun] = relationship(back_populates="files")


class RunItem(Base):
    __tablename__ = "run_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("update_runs.id"), index=True)
    excel_row: Mapped[int] = mapped_column()
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    match_source: Mapped[str] = mapped_column(String(30), default="none")
    row_status: Mapped[str] = mapped_column(String(30), default="needs_review")
    metric_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metric_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_reason: Mapped[str | None] = mapped_column(Text)
    original_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    run: Mapped[UpdateRun] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[str] = mapped_column(String(100))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
