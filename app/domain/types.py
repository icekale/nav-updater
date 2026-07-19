from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class MetricStatus(StrEnum):
    CALCULATED = "calculated"
    STALE = "stale"
    INSUFFICIENT_DATA = "insufficient_data"
    FAILED = "failed"


@dataclass(frozen=True)
class NavPoint:
    date: date
    value: Decimal
    source: str = "unknown"


@dataclass(frozen=True)
class MetricValue:
    value: Decimal | None
    status: MetricStatus


@dataclass(frozen=True)
class ReturnMetrics:
    cutoff: date
    weekly: MetricValue
    mtd: MetricValue
    ytd: MetricValue
    annual: dict[int, MetricValue]


@dataclass(frozen=True)
class RiskMetric:
    value: Decimal | None
    status: MetricStatus
