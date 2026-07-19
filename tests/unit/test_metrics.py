from datetime import date, timedelta
from decimal import Decimal

from app.domain.metrics import (
    calculate_max_drawdown,
    calculate_returns,
    calculate_sharpe,
    latest_friday,
)
from app.domain.types import MetricStatus, NavPoint


def points(*values: tuple[str, str]) -> list[NavPoint]:
    return [NavPoint(date.fromisoformat(day), Decimal(value)) for day, value in values]


def test_latest_friday_is_on_or_before_run_date() -> None:
    assert latest_friday(date(2026, 7, 19)) == date(2026, 7, 17)
    assert latest_friday(date(2026, 7, 17)) == date(2026, 7, 17)
    assert latest_friday(date(2026, 7, 13)) == date(2026, 7, 10)


def test_returns_use_previous_period_endpoints() -> None:
    nav = points(
        ("2025-12-31", "100"),
        ("2026-06-30", "110"),
        ("2026-07-10", "105"),
        ("2026-07-17", "115.5"),
    )
    result = calculate_returns(nav, date(2026, 7, 17), annual_years=[2025])
    assert result.weekly.value == Decimal("0.10")
    assert result.mtd.value == Decimal("0.05")
    assert result.ytd.value == Decimal("0.155")
    assert result.annual[2025].status is MetricStatus.INSUFFICIENT_DATA


def test_completed_year_return_is_calculated() -> None:
    nav = points(("2023-12-31", "80"), ("2024-12-31", "100"), ("2025-12-31", "110"))
    result = calculate_returns(nav, date(2026, 7, 17), annual_years=[2024, 2025])
    assert result.annual[2024].value == Decimal("0.25")
    assert result.annual[2025].value == Decimal("0.10")


def test_stale_public_value_is_not_used() -> None:
    nav = points(("2026-06-01", "100"), ("2026-07-17", "110"))
    result = calculate_returns(nav, date(2026, 7, 17), kind="public", annual_years=[])
    assert result.mtd.status is MetricStatus.STALE


def test_sharpe_uses_sample_standard_deviation() -> None:
    start = date(2025, 7, 18)
    nav = [NavPoint(date(2025, 7, 10), Decimal("99"))]
    nav.extend(NavPoint(start + timedelta(days=i), Decimal(100 + i)) for i in range(0, 370, 7))
    result = calculate_sharpe(nav, date(2026, 7, 17))
    assert result.status is MetricStatus.CALCULATED
    assert result.value is not None and result.value > 0


def test_max_drawdown_is_peak_to_trough() -> None:
    nav = points(
        ("2025-07-18", "100"),
        ("2026-01-02", "120"),
        ("2026-03-06", "90"),
        ("2026-07-17", "110"),
    )
    result = calculate_max_drawdown(nav, date(2026, 7, 17))
    assert result.value == Decimal("-0.25")
