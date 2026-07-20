from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..domain.metrics import calculate_max_drawdown, calculate_returns, calculate_sharpe
from ..domain.types import MetricStatus, NavPoint
from ..excel.template_adapter import TemplateAdapter
from ..models import RunFile, RunItem, UpdateRun

RUN_READY = "uploaded"
RUN_PROCESSING = "processing"
RUN_COMPLETED = "completed"
RUN_COMPLETED_WARNINGS = "completed_with_warnings"
RUN_FAILED = "failed"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class ExtractedRow:
    product_name: str
    product_code: str | None
    metrics: Mapping[str, Decimal]
    confidence: float = 1.0
    report_date: date | None = None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_run(
    session: Session,
    *,
    operator_id: int,
    cutoff_date: date,
    workbook_path: str | Path,
    image_paths: Iterable[str | Path],
    image_original_names: Mapping[str, str] | None = None,
    template: TemplateAdapter | None = None,
) -> UpdateRun:
    template = template or TemplateAdapter()
    workbook_path = Path(workbook_path)
    run = UpdateRun(operator_id=operator_id, cutoff_date=cutoff_date, status=RUN_READY)
    session.add(run)
    session.flush()
    session.add(
        RunFile(
            run_id=run.id,
            file_type="workbook",
            original_name=workbook_path.name,
            storage_path=str(workbook_path),
            sha256=sha256_file(workbook_path),
        )
    )
    for image_path in image_paths:
        image_path = Path(image_path)
        original_name = (image_original_names or {}).get(str(image_path), image_path.name)
        session.add(
            RunFile(
                run_id=run.id,
                file_type="image",
                original_name=original_name,
                storage_path=str(image_path),
                sha256=sha256_file(image_path),
            )
        )
    for product_row in template.inspect_products(workbook_path):
        if product_row.product_name:
            session.add(
                RunItem(
                    run_id=run.id,
                    excel_row=product_row.row_number,
                    original_values={"product_name": product_row.product_name},
                    metric_values={},
                    metric_status={},
                )
            )
    session.flush()
    return run


def claim_next_run(session: Session, now: datetime | None = None) -> UpdateRun | None:
    now = now or utcnow()
    stale_before = now - timedelta(minutes=30)
    query = (
        select(UpdateRun)
        .where(
            or_(
                UpdateRun.status == RUN_READY,
                (UpdateRun.status == RUN_PROCESSING)
                & (UpdateRun.heartbeat_at.is_(None) | (UpdateRun.heartbeat_at < stale_before)),
            )
        )
        .order_by(UpdateRun.id)
        .with_for_update(skip_locked=True)
    )
    run = session.scalars(query).first()
    if run is None:
        return None
    run.status = RUN_PROCESSING
    run.started_at = run.started_at or now
    run.heartbeat_at = now
    session.commit()
    return run


def requeue_run(session: Session, run_id: int) -> UpdateRun | None:
    result = session.execute(
        update(UpdateRun)
        .where(UpdateRun.id == run_id, UpdateRun.status != RUN_PROCESSING)
        .values(
            status=RUN_READY,
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            output_path=None,
            error_message=None,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return session.get(UpdateRun, run_id)


def lock_run_item(session: Session, run_id: int, item_id: int) -> tuple[UpdateRun, RunItem] | None:
    run = session.scalar(select(UpdateRun).where(UpdateRun.id == run_id).with_for_update())
    if run is None:
        return None
    item = session.scalar(
        select(RunItem)
        .where(RunItem.id == item_id, RunItem.run_id == run_id)
        .with_for_update()
    )
    return (run, item) if item is not None else None


def heartbeat(session: Session, run_id: int, now: datetime | None = None) -> None:
    run = session.get(UpdateRun, run_id)
    if run is None or run.status != RUN_PROCESSING:
        raise ValueError(f"run {run_id} is not processing")
    run.heartbeat_at = now or utcnow()
    session.commit()


def resolve_item(
    session: Session,
    item_id: int,
    *,
    product_id: int | None,
    match_source: str,
    row_status: str,
    metric_values: Mapping[str, Decimal | None],
    metric_status: Mapping[str, str],
    error_reason: str | None = None,
) -> RunItem:
    item = session.get(RunItem, item_id)
    if item is None:
        raise ValueError(f"run item {item_id} not found")
    item.product_id = product_id
    item.match_source = match_source
    item.row_status = row_status
    item.metric_values = {
        key: str(value) if value is not None else None for key, value in metric_values.items()
    }
    item.metric_status = dict(metric_status)
    item.error_reason = error_reason
    session.commit()
    return item


def metric_values_from_nav(
    points: Iterable[NavPoint], cutoff: date, kind: str
) -> tuple[dict[str, Decimal], dict[str, str]]:
    returns = calculate_returns(points, cutoff, kind=kind)
    sharpe = calculate_sharpe(points, cutoff, kind=kind)
    drawdown = calculate_max_drawdown(points, cutoff, kind=kind)
    values: dict[str, Decimal] = {}
    statuses: dict[str, str] = {}
    metrics = {"weekly": returns.weekly, "mtd": returns.mtd, "ytd": returns.ytd}
    metrics.update({f"annual_{year}": metric for year, metric in returns.annual.items()})
    metrics["sharpe"] = sharpe
    metrics["max_drawdown"] = drawdown
    for name, result in metrics.items():
        value = getattr(result, "value", None)
        status = getattr(result, "status", MetricStatus.INSUFFICIENT_DATA)
        if value is not None:
            values[name] = value
        statuses[name] = status.value
    return values, statuses


def finish_run(
    session: Session,
    run_id: int,
    *,
    output_path: str | None,
    warnings: bool = False,
    error_message: str | None = None,
) -> UpdateRun:
    run = session.get(UpdateRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    run.status = RUN_COMPLETED_WARNINGS if warnings else RUN_COMPLETED
    run.output_path = output_path
    run.error_message = error_message
    run.finished_at = utcnow()
    session.commit()
    return run


def fail_run(session: Session, run_id: int, error_message: str) -> UpdateRun:
    run = session.get(UpdateRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    run.status = RUN_FAILED
    run.error_message = error_message
    run.finished_at = utcnow()
    session.commit()
    return run
