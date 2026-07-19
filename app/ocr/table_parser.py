from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .engine import OCRToken


@dataclass(frozen=True)
class ParsedCell:
    text: str
    confidence: float


@dataclass(frozen=True)
class ParsedRow:
    cells: tuple[ParsedCell, ...]


def group_rows(tokens: Iterable[OCRToken], y_tolerance: float = 12.0) -> list[ParsedRow]:
    rows: list[list[OCRToken]] = []
    for token in sorted(tokens, key=lambda item: (item.center_y, item.left)):
        target = next(
            (row for row in rows if abs(row[0].center_y - token.center_y) <= y_tolerance),
            None,
        )
        if target is None:
            rows.append([token])
        else:
            target.append(token)
    return [
        ParsedRow(
            tuple(
                ParsedCell(token.text, token.confidence)
                for token in sorted(row, key=lambda item: item.left)
            )
        )
        for row in rows
    ]


def parse_percent(text: str) -> Decimal:
    value = text.strip().replace(",", "").replace(" ", "")
    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("()%")
    if value in {"", "-", "--", "—", "N/A", "n/a"}:
        raise ValueError("percent value is unavailable")
    try:
        parsed = Decimal(value) / Decimal("100")
    except InvalidOperation as exc:
        raise ValueError(f"invalid percent value: {text}") from exc
    return -parsed if negative else parsed


def parse_number(text: str) -> Decimal:
    value = text.strip().replace(",", "").replace(" ", "")
    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("()")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric value: {text}") from exc
    return -parsed if negative else parsed


def is_confident(confidence: float, threshold: float = 0.85) -> bool:
    return confidence >= threshold
