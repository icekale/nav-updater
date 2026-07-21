from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OcrReviewSample, Product, RunItem


class ManualReviewError(ValueError):
    pass


@dataclass(frozen=True)
class MetricField:
    name: str
    label: str
    is_percent: bool = True


METRIC_FIELDS = (
    MetricField("weekly", "近一周（%）"),
    MetricField("mtd", "MTD（%）"),
    MetricField("ytd", "YTD（%）"),
    MetricField("annual_2019", "2019（%）"),
    MetricField("annual_2020", "2020（%）"),
    MetricField("annual_2021", "2021（%）"),
    MetricField("annual_2022", "2022（%）"),
    MetricField("annual_2023", "2023（%）"),
    MetricField("annual_2024", "2024（%）"),
    MetricField("annual_2025", "2025（%）"),
    MetricField("sharpe", "近一年夏普比", is_percent=False),
    MetricField("max_drawdown", "近一年最大回撤（%）"),
)
OCR_QUALITY_SOURCES = {"image", "none"}


def parse_manual_metrics(inputs: Mapping[str, object]) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for field in METRIC_FIELDS:
        raw = str(inputs.get(field.name, "")).strip().replace(",", "")
        if not raw:
            continue
        if raw.endswith("%"):
            raw = raw[:-1].strip()
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ManualReviewError(f"{field.label}不是有效数字") from exc
        if not value.is_finite():
            raise ManualReviewError(f"{field.label}不是有效数字")
        values[field.name] = value / Decimal("100") if field.is_percent else value
    if not values:
        raise ManualReviewError("至少填写一个指标")
    return values


def save_manual_review(
    session: Session,
    *,
    item: RunItem,
    product: Product,
    inputs: Mapping[str, object],
    note: str,
    values: Mapping[str, Decimal] | None = None,
) -> RunItem:
    cleaned_note = note.strip()
    if not cleaned_note:
        raise ManualReviewError("审核说明不能为空")
    if not product.is_active:
        raise ManualReviewError("产品已停用")
    values = dict(values) if values is not None else parse_manual_metrics(inputs)
    if not values:
        raise ManualReviewError("至少填写一个指标")
    metric_status = {
        field.name: "manual" if field.name in values else "stale" for field in METRIC_FIELDS
    }
    item.product_id = product.id
    item.match_source = "manual"
    item.row_status = "ready" if len(values) == len(METRIC_FIELDS) else "stale"
    item.metric_values = {name: str(value) for name, value in values.items()}
    item.metric_status = metric_status
    item.error_reason = f"人工审核：{cleaned_note}"
    session.flush()
    return item


def capture_ocr_review_sample(
    session: Session,
    *,
    run_id: int,
    item: RunItem,
    actor_id: int,
    product: Product,
    values: Mapping[str, Decimal],
    note: str,
) -> OcrReviewSample | None:
    session.flush()
    previous = session.scalar(
        select(OcrReviewSample)
        .where(OcrReviewSample.run_item_id == item.id)
        .order_by(OcrReviewSample.review_version.desc())
        .limit(1)
    )
    if item.match_source in OCR_QUALITY_SOURCES:
        source = item.match_source
        ocr_product_id = item.product_id
        ocr_values = dict(item.metric_values)
        ocr_statuses = dict(item.metric_status)
    elif previous is not None:
        source = previous.ocr_match_source
        ocr_product_id = previous.ocr_product_id
        ocr_values = dict(previous.ocr_metric_values)
        ocr_statuses = dict(previous.ocr_metric_status)
    else:
        return None
    sample = OcrReviewSample(
        run_id=run_id,
        run_item_id=item.id,
        actor_id=actor_id,
        product_id=product.id,
        excel_product_name=str(item.original_values.get("product_name", "")),
        review_version=(previous.review_version if previous is not None else 0) + 1,
        ocr_match_source=source,
        ocr_product_id=ocr_product_id,
        ocr_metric_values=ocr_values,
        ocr_metric_status=ocr_statuses,
        confirmed_metric_values={name: str(value) for name, value in values.items()},
        confirmed_metric_status={
            field.name: "manual" if field.name in values else "unconfirmed"
            for field in METRIC_FIELDS
        },
        review_note=note.strip(),
    )
    session.add(sample)
    return sample


def formatted_metric_values(item: RunItem) -> dict[str, str]:
    formatted: dict[str, str] = {}
    for field in METRIC_FIELDS:
        raw = item.metric_values.get(field.name)
        if raw is None:
            formatted[field.name] = ""
            continue
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            formatted[field.name] = ""
            continue
        if field.is_percent:
            value *= Decimal("100")
        formatted[field.name] = format(value.normalize(), "f")
    return formatted
