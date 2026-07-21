from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .time import china_now


def utcnow() -> datetime:
    return china_now()


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
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
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
    quality_samples: Mapped[list[OcrReviewSample]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


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
    ocr_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_reason: Mapped[str | None] = mapped_column(Text)
    original_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    run: Mapped[UpdateRun] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()
    quality_samples: Mapped[list[OcrReviewSample]] = relationship(
        back_populates="run_item", cascade="all, delete-orphan"
    )


class OcrReviewSample(Base):
    __tablename__ = "ocr_review_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("update_runs.id"), index=True)
    run_item_id: Mapped[int] = mapped_column(ForeignKey("run_items.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    excel_product_name: Mapped[str] = mapped_column(String(255))
    review_version: Mapped[int] = mapped_column()
    ocr_match_source: Mapped[str] = mapped_column(String(30))
    ocr_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    ocr_metric_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ocr_metric_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confirmed_metric_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confirmed_metric_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    review_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    run: Mapped[UpdateRun] = relationship(back_populates="quality_samples")
    run_item: Mapped[RunItem] = relationship(back_populates="quality_samples")
    product: Mapped[Product | None] = relationship(foreign_keys=[product_id])


class OcrRegressionSample(Base):
    __tablename__ = "ocr_regression_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    image_path: Mapped[str] = mapped_column(Text)
    image_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("update_runs.id", ondelete="SET NULL"), index=True
    )
    source_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_items.id", ondelete="SET NULL"), index=True
    )
    source_label: Mapped[str] = mapped_column(String(80))
    excel_product_name: Mapped[str] = mapped_column(String(255))
    candidate_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    expected_product_code: Mapped[str | None] = mapped_column(String(100))
    expected_metric_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected_metric_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)

    source_run: Mapped[UpdateRun | None] = relationship(foreign_keys=[source_run_id])
    source_item: Mapped[RunItem | None] = relationship(foreign_keys=[source_item_id])


class OcrRegressionRun(Base):
    __tablename__ = "ocr_regression_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    total_count: Mapped[int] = mapped_column(default=0)
    passed_count: Mapped[int] = mapped_column(default=0)
    failed_count: Mapped[int] = mapped_column(default=0)
    skipped_count: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    results: Mapped[list[OcrRegressionResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class OcrRegressionResult(Base):
    __tablename__ = "ocr_regression_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ocr_regression_runs.id", ondelete="CASCADE"), index=True
    )
    sample_id: Mapped[int] = mapped_column(ForeignKey("ocr_regression_samples.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(30))
    expected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actual: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    run: Mapped[OcrRegressionRun] = relationship(back_populates="results")
    sample: Mapped[OcrRegressionSample] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[str] = mapped_column(String(100))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    date_raw: Mapped[str] = mapped_column(String(100))
    date_start: Mapped[date | None] = mapped_column(Date, index=True)
    date_end: Mapped[date | None] = mapped_column(Date, index=True)
    date_parse_status: Mapped[str] = mapped_column(String(30), default="unparsed")
    level: Mapped[str] = mapped_column(Text)
    core_statement: Mapped[str] = mapped_column(Text)
    market_impact: Mapped[str] = mapped_column(Text)
    research_mapping: Mapped[str] = mapped_column(Text)
    follow_up: Mapped[str] = mapped_column(Text)
    source_link: Mapped[str] = mapped_column(Text)
    source_updated_at: Mapped[str] = mapped_column(String(100))
    company_tags: Mapped[str] = mapped_column(Text, default="")
    industry_tags: Mapped[str] = mapped_column(Text, default="")
    attendance_status: Mapped[str] = mapped_column(String(20), default="unplanned")
    minutes: Mapped[str] = mapped_column(Text, default="")
    todo: Mapped[str] = mapped_column(Text, default="")
    conclusion: Mapped[str] = mapped_column(Text, default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
