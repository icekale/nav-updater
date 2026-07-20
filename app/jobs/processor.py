from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog import ensure_public_product
from ..domain.matching import CatalogRecord, match_product, normalize_name
from ..domain.types import MetricStatus, NavPoint
from ..excel.template_adapter import TemplateAdapter
from ..models import AuditLog, NavObservation, Product, RunFile, RunItem, UpdateRun
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
    records: list[CatalogRecord] = []
    for product in products:
        records.append(
            CatalogRecord(product.product_name, product.product_code, product.product_type)
        )
        records.extend(
            CatalogRecord(name, product.product_code, product.product_type)
            for name in product.historical_names or []
        )
    return records


def _find_product(products: Iterable[Product], record: CatalogRecord | None) -> Product | None:
    if record is None:
        return None
    return next((item for item in products if item.product_code == record.product_code), None)


def _product_by_name(products: Iterable[Product], name: str) -> Product | None:
    normalized = normalize_name(name)
    return next(
        (
            product
            for product in products
            if normalize_name(product.product_name) == normalized
            or any(normalize_name(alias) == normalized for alias in product.historical_names or [])
        ),
        None,
    )


def _find_image_row(
    item_name: str,
    rows: list[OCRMetricRow],
    products: list[Product],
    item_names: list[str],
) -> OCRMetricRow | None:
    catalog = _product_records(products)
    for row in rows:
        record = match_product(
            product_code=row.product_code, product_name=row.product_name, products=catalog
        )
        if record:
            if normalize_name(record.product_name) == normalize_name(item_name):
                return row
            continue
        if normalize_name(row.product_name) == normalize_name(item_name):
            return row
        if _is_unique_truncated_chinese_name(item_name, row.product_name, item_names):
            return row
    return None


def _leading_chinese(value: str) -> str:
    match = re.match(r"[\u4e00-\u9fff]+", value.strip())
    return match.group(0) if match else ""


def _is_unique_truncated_chinese_name(
    item_name: str,
    ocr_name: str,
    item_names: list[str],
) -> bool:
    ocr_prefix = _leading_chinese(ocr_name)
    item_prefix = _leading_chinese(item_name)
    if len(ocr_prefix) < 4 or len(ocr_prefix) >= len(item_prefix):
        return False
    candidates = {
        normalize_name(name) for name in item_names if _leading_chinese(name).startswith(ocr_prefix)
    }
    return candidates == {normalize_name(item_name)}


def _store_observations(session: Session, product: Product, points) -> None:
    points = list(points)
    if not points:
        return
    existing_by_date: dict = {}
    dates = list({point.date for point in points})
    for index in range(0, len(dates), 500):
        existing_by_date.update(
            {
                observation.nav_date: observation
                for observation in session.scalars(
                    select(NavObservation).where(
                        NavObservation.product_id == product.id,
                        NavObservation.nav_date.in_(dates[index : index + 500]),
                        NavObservation.source_kind == "eastmoney",
                    )
                ).all()
            }
        )
    for point in points:
        existing = existing_by_date.get(point.date)
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


def _public_observations(session: Session, product: Product) -> list[NavPoint]:
    observations = session.scalars(
        select(NavObservation)
        .where(
            NavObservation.product_id == product.id,
            NavObservation.source_kind == "eastmoney",
        )
        .order_by(NavObservation.nav_date)
    ).all()
    return [
        NavPoint(
            observation.nav_date,
            observation.cumulative_nav,
            observation.source_ref or "eastmoney:cached",
        )
        for observation in observations
    ]


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
    actor_id: int | None = None,
) -> UpdateRun:
    run = session.get(UpdateRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    run.status = RUN_PROCESSING
    run.started_at = run.started_at or utcnow()
    run.heartbeat_at = utcnow()
    session.commit()
    try:
        actor_id = actor_id or run.operator_id
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
            screenshot_rows.extend(extract_metric_rows(ocr_service.recognize_tiled(image.storage_path)))

        updates: dict[int, dict[str, Decimal | None]] = {}
        stale: dict[int, set[str]] = {}
        warnings = False
        items = session.scalars(select(RunItem).where(RunItem.run_id == run_id)).all()
        item_names = [str(item.original_values.get("product_name", "")) for item in items]
        for item in items:
            name = str(item.original_values.get("product_name", ""))
            if item.match_source == "manual":
                updates[item.excel_row] = _manual_values(item)
                stale[item.excel_row] = _stale_metrics(item.metric_status)
                warnings = warnings or item.row_status != "ready" or bool(stale[item.excel_row])
                continue
            image_row = _find_image_row(name, screenshot_rows, products, item_names)
            product = _product_by_name(products, name)
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
            if product is None:
                try:
                    record = provider.resolve_by_name(name)
                    if record is not None:
                        product, created = ensure_public_product(session, record, name)
                        products.append(product)
                        if created:
                            session.add(
                                AuditLog(
                                    actor_id=actor_id,
                                    action="resolve_public_product",
                                    object_type="product",
                                    object_id=str(product.id),
                                    context={
                                        "product_code": product.product_code,
                                        "product_name": product.product_name,
                                        "source_name": name,
                                    },
                                )
                            )
                except Exception as exc:
                    statuses = {key: MetricStatus.FAILED.value for key in ALL_METRICS}
                    _set_item(
                        item,
                        product=None,
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
            if product and product.product_type == "public":
                try:
                    cached_points = _public_observations(session, product)
                    start_date = max((point.date for point in cached_points), default=None)
                    fetched_points = provider.fetch_history(
                        product.product_code,
                        start_date=start_date,
                    )
                    _store_observations(session, product, fetched_points)
                    points_by_date = {point.date: point for point in cached_points}
                    points_by_date.update({point.date: point for point in fetched_points})
                    points = list(points_by_date.values())
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
                error="截图未找到对应产品，公募名称未能唯一确认基金代码",
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
