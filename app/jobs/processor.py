from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.matching import CatalogRecord, match_product, normalize_name
from ..domain.types import MetricStatus
from ..excel.template_adapter import TemplateAdapter
from ..models import NavObservation, Product, RunFile, RunItem, UpdateRun
from ..ocr.engine import OCRService
from ..ocr.table_parser import OCRMetricRow, extract_metric_rows
from ..providers.public_fund import PublicFundProvider
from .service import (
    RUN_PROCESSING,
    fail_run,
    finish_run,
    metric_values_from_nav,
)

ALL_METRICS = (
    "weekly",
    "mtd",
    "ytd",
    "annual_2019",
    "annual_2020",
    "annual_2021",
    "annual_2022",
    "annual_2023",
    "annual_2024",
    "annual_2025",
    "sharpe",
    "max_drawdown",
)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _product_records(products: Iterable[Product]) -> list[CatalogRecord]:
    return [
        CatalogRecord(item.product_name, item.product_code, item.product_type) for item in products
    ]


def _find_product(products: Iterable[Product], record: CatalogRecord | None) -> Product | None:
    if record is None:
        return None
    return next((item for item in products if item.product_code == record.product_code), None)


def _find_image_row(
    item_name: str, rows: list[OCRMetricRow], products: list[Product]
) -> OCRMetricRow | None:
    catalog = _product_records(products)
    for row in rows:
        record = match_product(
            product_code=row.product_code, product_name=row.product_name, products=catalog
        )
        if record and normalize_name(record.product_name) == normalize_name(item_name):
            return row
        if normalize_name(row.product_name) == normalize_name(item_name):
            return row
    return None


def _store_observations(session: Session, product: Product, points) -> None:
    for point in points:
        existing = session.scalar(
            select(NavObservation).where(
                NavObservation.product_id == product.id,
                NavObservation.nav_date == point.date,
                NavObservation.source_kind == "eastmoney",
            )
        )
        if existing:
            existing.cumulative_nav = point.value
            existing.source_ref = point.source
        else:
            session.add(
                NavObservation(
                    product_id=product.id,
                    nav_date=point.date,
                    cumulative_nav=point.value,
                    source_kind="eastmoney",
                    source_ref=point.source,
                )
            )


def _stale_metrics(statuses: dict[str, str]) -> set[str]:
    return {
        key
        for key, value in statuses.items()
        if value
        in {
            MetricStatus.STALE.value,
            MetricStatus.INSUFFICIENT_DATA.value,
            MetricStatus.FAILED.value,
        }
    }


def _manual_values(item: RunItem) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for metric in ALL_METRICS:
        raw = item.metric_values.get(metric)
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            continue
        if value.is_finite():
            values[metric] = value
    return values


def _set_item(
    item: RunItem,
    *,
    product: Product | None,
    source: str,
    row_status: str,
    values: dict[str, Decimal],
    statuses: dict[str, str],
    error: str | None = None,
) -> None:
    item.product_id = product.id if product else None
    item.match_source = source
    item.row_status = row_status
    item.metric_values = {key: str(value) for key, value in values.items()}
    item.metric_status = statuses
    item.error_reason = error


def process_run(
    session: Session,
    run_id: int,
    *,
    ocr_service: OCRService | None = None,
    provider: PublicFundProvider | None = None,
    adapter: TemplateAdapter | None = None,
) -> UpdateRun:
    run = session.get(UpdateRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    run.status = RUN_PROCESSING
    run.started_at = run.started_at or utcnow()
    run.heartbeat_at = utcnow()
    session.commit()
    try:
        adapter = adapter or TemplateAdapter()
        provider = provider or PublicFundProvider()
        ocr_service = ocr_service or OCRService()
        files = session.scalars(select(RunFile).where(RunFile.run_id == run_id)).all()
        workbook = next((item for item in files if item.file_type == "workbook"), None)
        if workbook is None:
            raise ValueError("run is missing workbook input")
        products = session.scalars(select(Product).where(Product.is_active.is_(True))).all()
        screenshot_rows: list[OCRMetricRow] = []
        for image in (item for item in files if item.file_type == "image"):
            screenshot_rows.extend(extract_metric_rows(ocr_service.recognize(image.storage_path)))

        updates: dict[int, dict[str, Decimal | None]] = {}
        stale: dict[int, set[str]] = {}
        warnings = False
        items = session.scalars(select(RunItem).where(RunItem.run_id == run_id)).all()
        for item in items:
            name = str(item.original_values.get("product_name", ""))
            if item.match_source == "manual":
                updates[item.excel_row] = _manual_values(item)
                stale[item.excel_row] = _stale_metrics(item.metric_status)
                warnings = warnings or item.row_status != "ready" or bool(stale[item.excel_row])
                continue
            image_row = _find_image_row(name, screenshot_rows, products)
            product = next(
                (p for p in products if normalize_name(p.product_name) == normalize_name(name)),
                None,
            )
            if image_row is not None:
                statuses = {key: "extracted" for key in image_row.metrics}
                row_status = "ready" if image_row.confidence >= 0.85 else "needs_review"
                _set_item(
                    item,
                    product=product,
                    source="image",
                    row_status=row_status,
                    values=image_row.metrics,
                    statuses=statuses,
                    error=None if row_status == "ready" else "OCR confidence below threshold",
                )
                updates[item.excel_row] = dict(image_row.metrics)
                if row_status != "ready":
                    warnings = True
                continue
            if product and product.product_type == "public":
                try:
                    points = provider.fetch_history(product.product_code)
                    _store_observations(session, product, points)
                    values, statuses = metric_values_from_nav(points, run.cutoff_date, "public")
                    row_status = (
                        "ready"
                        if all(value == "calculated" for value in statuses.values())
                        else "stale"
                    )
                    _set_item(
                        item,
                        product=product,
                        source="public_provider",
                        row_status=row_status,
                        values=values,
                        statuses=statuses,
                        error=None
                        if row_status == "ready"
                        else "one or more public metrics unavailable",
                    )
                    updates[item.excel_row] = values
                    stale[item.excel_row] = _stale_metrics(statuses)
                    warnings = warnings or row_status != "ready"
                except Exception as exc:
                    statuses = {key: MetricStatus.FAILED.value for key in ALL_METRICS}
                    _set_item(
                        item,
                        product=product,
                        source="public_provider",
                        row_status="failed",
                        values={},
                        statuses=statuses,
                        error=str(exc),
                    )
                    updates[item.excel_row] = {}
                    stale[item.excel_row] = set(ALL_METRICS)
                    warnings = True
                continue
            statuses = {key: MetricStatus.STALE.value for key in ALL_METRICS}
            _set_item(
                item,
                product=product,
                source="none",
                row_status="stale",
                values={},
                statuses=statuses,
                error="没有截图匹配，也没有可用公募产品代码",
            )
            updates[item.excel_row] = {}
            stale[item.excel_row] = set(ALL_METRICS)
            warnings = True

        session.commit()
        output_path = Path(workbook.storage_path).with_name("updated.xlsx")
        adapter.apply_updates(workbook.storage_path, output_path, updates, stale)
        return finish_run(
            session,
            run_id,
            output_path=str(output_path),
            warnings=warnings,
        )
    except Exception as exc:
        session.rollback()
        return fail_run(session, run_id, str(exc))
