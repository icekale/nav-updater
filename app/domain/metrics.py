from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal, localcontext
from statistics import median

from .types import MetricStatus, MetricValue, NavPoint, ReturnMetrics, RiskMetric

PUBLIC_MAX_STALE_DAYS = 14
PRIVATE_MAX_STALE_DAYS = 45
_ONE = Decimal("1")
_YEAR_DAYS = Decimal("365.25")


def latest_friday(run_date: date) -> date:
    """Return the Friday on or before run_date (Monday=0, Friday=4)."""
    return run_date - timedelta(days=(run_date.weekday() - 4) % 7)


def _year_before(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def _deduplicate(points: Iterable[NavPoint]) -> list[NavPoint]:
    by_date: dict[date, NavPoint] = {}
    for point in points:
        by_date[point.date] = point
    return sorted(by_date.values(), key=lambda item: item.date)


def _max_stale_days(kind: str) -> int:
    if kind not in {"public", "private"}:
        raise ValueError("kind must be 'public' or 'private'")
    return PUBLIC_MAX_STALE_DAYS if kind == "public" else PRIVATE_MAX_STALE_DAYS


def _effective(
    points: list[NavPoint], target: date, kind: str
) -> tuple[NavPoint | None, MetricStatus]:
    candidate = next((point for point in reversed(points) if point.date <= target), None)
    if candidate is None or candidate.value <= 0:
        return None, MetricStatus.INSUFFICIENT_DATA
    if (target - candidate.date).days > _max_stale_days(kind):
        return None, MetricStatus.STALE
    return candidate, MetricStatus.CALCULATED


def _combine_status(*statuses: MetricStatus) -> MetricStatus:
    if MetricStatus.STALE in statuses:
        return MetricStatus.STALE
    return MetricStatus.INSUFFICIENT_DATA


def _return_between(
    points: list[NavPoint], start: date, end: date, kind: str
) -> MetricValue:
    start_point, start_status = _effective(points, start, kind)
    end_point, end_status = _effective(points, end, kind)
    if not start_point or not end_point:
        return MetricValue(None, _combine_status(start_status, end_status))
    return MetricValue(end_point.value / start_point.value - _ONE, MetricStatus.CALCULATED)


def calculate_returns(
    points: Iterable[NavPoint],
    cutoff: date,
    kind: str = "public",
    annual_years: Iterable[int] | None = None,
) -> ReturnMetrics:
    ordered = _deduplicate(points)
    if annual_years is None:
        annual_years = range(cutoff.year - 7, cutoff.year)
    month_start = cutoff.replace(day=1)
    year_start = cutoff.replace(month=1, day=1)
    annual: dict[int, MetricValue] = {}
    for year in annual_years:
        if year >= cutoff.year:
            annual[year] = MetricValue(None, MetricStatus.INSUFFICIENT_DATA)
            continue
        annual[year] = _return_between(
            ordered,
            date(year - 1, 12, 31),
            date(year, 12, 31),
            kind,
        )
    return ReturnMetrics(
        cutoff=cutoff,
        weekly=_return_between(ordered, cutoff - timedelta(days=7), cutoff, kind),
        mtd=_return_between(ordered, month_start - timedelta(days=1), cutoff, kind),
        ytd=_return_between(ordered, year_start - timedelta(days=1), cutoff, kind),
        annual=annual,
    )


def _annualization_factor(points: list[NavPoint], cutoff: date, kind: str) -> Decimal:
    if kind == "public":
        return Decimal("252")
    intervals = [
        (right.date - left.date).days
        for left, right in zip(points, points[1:])
        if (right.date - left.date).days > 0
    ]
    if not intervals:
        return Decimal("0")
    factor = _YEAR_DAYS / Decimal(str(median(intervals)))
    return min(factor, Decimal("252"))


def calculate_sharpe(
    points: Iterable[NavPoint], cutoff: date, kind: str = "public"
) -> RiskMetric:
    ordered = _deduplicate(points)
    window_start = _year_before(cutoff)
    latest, latest_status = _effective(ordered, cutoff, kind)
    baseline, baseline_status = _effective(ordered, window_start, kind)
    if not latest or not baseline:
        return RiskMetric(None, _combine_status(latest_status, baseline_status))
    window = [
        point for point in ordered if window_start <= point.date <= cutoff and point.value > 0
    ]
    if not window:
        return RiskMetric(None, MetricStatus.INSUFFICIENT_DATA)
    with_baseline = [baseline] + window
    returns = [
        right.value / left.value - _ONE
        for left, right in zip(with_baseline, with_baseline[1:])
    ]
    if len(returns) < 3:
        return RiskMetric(None, MetricStatus.INSUFFICIENT_DATA)
    with localcontext() as context:
        context.prec = 40
        mean = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = sum((item - mean) ** 2 for item in returns) / Decimal(len(returns) - 1)
        if variance == 0:
            return RiskMetric(None, MetricStatus.INSUFFICIENT_DATA)
        factor = _annualization_factor(with_baseline, cutoff, kind)
        if factor <= 0:
            return RiskMetric(None, MetricStatus.INSUFFICIENT_DATA)
        return RiskMetric(mean / variance.sqrt() * factor.sqrt(), MetricStatus.CALCULATED)


def calculate_max_drawdown(
    points: Iterable[NavPoint], cutoff: date, kind: str = "public"
) -> RiskMetric:
    ordered = _deduplicate(points)
    window_start = _year_before(cutoff)
    latest, latest_status = _effective(ordered, cutoff, kind)
    if not latest:
        return RiskMetric(None, latest_status)
    window = [
        point for point in ordered if window_start <= point.date <= cutoff and point.value > 0
    ]
    if len(window) < 2:
        return RiskMetric(None, MetricStatus.INSUFFICIENT_DATA)
    peak = window[0].value
    drawdowns: list[Decimal] = []
    for point in window:
        peak = max(peak, point.value)
        drawdowns.append(point.value / peak - _ONE)
    return RiskMetric(min(drawdowns), MetricStatus.CALCULATED)
