from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .jobs.review import METRIC_FIELDS
from .models import OcrReviewSample, Product, RunItem, UpdateRun

REVIEWABLE_STATUSES = {"needs_review", "stale", "failed"}


@dataclass(frozen=True)
class QualityBreakdown:
    key: str
    label: str
    confirmed_count: int
    matched_count: int
    missing_count: int
    incorrect_count: int
    accuracy: Decimal | None


@dataclass(frozen=True)
class QualityIssue:
    run_id: int
    run_item_id: int
    product_name: str
    metric_label: str
    outcome: str
    reviewed_at: datetime


@dataclass(frozen=True)
class QualityDashboard:
    field_accuracy: Decimal | None
    pending_review_count: int
    source_blank_count: int
    missing_count: int
    product_matched_count: int
    product_unmatched_count: int
    product_corrected_count: int
    fields: tuple[QualityBreakdown, ...]
    products: tuple[QualityBreakdown, ...]
    recent_issues: tuple[QualityIssue, ...]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _accuracy(matched_count: int, confirmed_count: int) -> Decimal | None:
    if not confirmed_count:
        return None
    return (Decimal(matched_count) / Decimal(confirmed_count)).quantize(Decimal("0.0001"))


def _empty_counts() -> dict[str, int]:
    return {"confirmed": 0, "matched": 0, "missing": 0, "incorrect": 0}


def _metric_outcome(sample: OcrReviewSample, metric: str) -> str | None:
    if sample.confirmed_metric_status.get(metric) != "manual":
        return None
    if metric not in sample.ocr_metric_values:
        return "missing"
    try:
        matches = Decimal(str(sample.ocr_metric_values[metric])) == Decimal(
            str(sample.confirmed_metric_values[metric])
        )
    except (InvalidOperation, ValueError):
        matches = str(sample.ocr_metric_values[metric]) == str(
            sample.confirmed_metric_values[metric]
        )
    if matches:
        return "matched"
    return "incorrect"


def _breakdowns(
    counts: dict[tuple[str, str], dict[str, int]],
) -> tuple[QualityBreakdown, ...]:
    return tuple(
        QualityBreakdown(
            key=key,
            label=label,
            confirmed_count=value["confirmed"],
            matched_count=value["matched"],
            missing_count=value["missing"],
            incorrect_count=value["incorrect"],
            accuracy=_accuracy(value["matched"], value["confirmed"]),
        )
        for (key, label), value in sorted(
            counts.items(),
            key=lambda item: (-item[1]["missing"], -item[1]["incorrect"], item[0][1]),
        )
    )


def build_quality_dashboard(
    session: Session,
    *,
    now: datetime | None = None,
    days: int = 30,
) -> QualityDashboard:
    now = now or _utcnow()
    cutoff = now - timedelta(days=days)
    latest_versions = (
        select(
            OcrReviewSample.run_item_id,
            func.max(OcrReviewSample.review_version).label("review_version"),
        )
        .group_by(OcrReviewSample.run_item_id)
        .subquery()
    )
    samples = session.scalars(
        select(OcrReviewSample)
        .join(
            latest_versions,
            (OcrReviewSample.run_item_id == latest_versions.c.run_item_id)
            & (OcrReviewSample.review_version == latest_versions.c.review_version),
        )
        .where(OcrReviewSample.created_at >= cutoff)
        .order_by(OcrReviewSample.created_at.desc())
    ).all()
    product_ids = {sample.product_id for sample in samples if sample.product_id is not None}
    products_by_id = {
        product.id: product.product_name
        for product in session.scalars(select(Product).where(Product.id.in_(product_ids))).all()
    }
    field_labels = {field.name: field.label for field in METRIC_FIELDS}
    field_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(_empty_counts)
    product_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(_empty_counts)
    issues: list[QualityIssue] = []
    matched_products = 0
    unmatched_products = 0
    corrected_products = 0

    for sample in samples:
        product_name = products_by_id.get(sample.product_id, sample.excel_product_name)
        product_key = str(sample.product_id) if sample.product_id is not None else product_name
        if sample.ocr_product_id == sample.product_id:
            matched_products += 1
        elif sample.ocr_product_id is None:
            unmatched_products += 1
        else:
            corrected_products += 1
        for metric, label in field_labels.items():
            outcome = _metric_outcome(sample, metric)
            if outcome is None:
                continue
            for counts, key in (
                (field_counts, (metric, label)),
                (product_counts, (product_key, product_name)),
            ):
                counts[key]["confirmed"] += 1
                counts[key][outcome] += 1
            if outcome != "matched":
                issues.append(
                    QualityIssue(
                        run_id=sample.run_id,
                        run_item_id=sample.run_item_id,
                        product_name=product_name,
                        metric_label=label,
                        outcome="漏识别" if outcome == "missing" else "值不一致",
                        reviewed_at=sample.created_at,
                    )
                )

    current_items = session.scalars(
        select(RunItem).join(UpdateRun).where(UpdateRun.created_at >= cutoff)
    ).all()
    pending_review_count = sum(item.row_status in REVIEWABLE_STATUSES for item in current_items)
    source_blank_count = sum(
        status == "source_blank"
        for item in current_items
        for status in item.metric_status.values()
    )
    fields = _breakdowns(field_counts)
    products = _breakdowns(product_counts)
    confirmed_count = sum(field.confirmed_count for field in fields)
    matched_count = sum(field.matched_count for field in fields)
    missing_count = sum(field.missing_count for field in fields)
    return QualityDashboard(
        field_accuracy=_accuracy(matched_count, confirmed_count),
        pending_review_count=pending_review_count,
        source_blank_count=source_blank_count,
        missing_count=missing_count,
        product_matched_count=matched_products,
        product_unmatched_count=unmatched_products,
        product_corrected_count=corrected_products,
        fields=fields,
        products=products,
        recent_issues=tuple(sorted(issues, key=lambda issue: issue.reviewed_at, reverse=True)[:10]),
    )
