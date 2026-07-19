from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from ..models import Product, RunItem


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
) -> RunItem:
    cleaned_note = note.strip()
    if not cleaned_note:
        raise ManualReviewError("审核说明不能为空")
    if not product.is_active:
        raise ManualReviewError("产品已停用")
    values = parse_manual_metrics(inputs)
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
