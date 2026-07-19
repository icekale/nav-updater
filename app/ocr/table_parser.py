from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .engine import OCRToken


@dataclass(frozen=True)
class ParsedCell:
    text: str
    confidence: float
    left: float = 0.0


@dataclass(frozen=True)
class ParsedRow:
    cells: tuple[ParsedCell, ...]


@dataclass(frozen=True)
class OCRMetricRow:
    product_name: str
    product_code: str | None
    metrics: dict[str, Decimal]
    confidence: float


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
                ParsedCell(token.text, token.confidence, token.left)
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


def _header_key(text: str) -> str | None:
    value = text.replace(" ", "").replace("（", "(").replace("）", ")").lower()
    if value in {"产品名称", "产品", "名称"}:
        return "product_name"
    if value in {"代码", "产品代码", "基金代码"}:
        return "product_code"
    if value.startswith("mtd"):
        return "mtd"
    if value.startswith("ytd"):
        return "ytd"
    if value.startswith("近一周"):
        return "weekly"
    if value.startswith("近一年") and "夏普" in value:
        return "sharpe"
    if value.startswith("近一年") and "回撤" in value:
        return "max_drawdown"
    match = re.match(r"^(2019|2020|2021|2022|2023|2024|2025)", value)
    return f"annual_{match.group(1)}" if match else None


def extract_metric_rows(tokens: Iterable[OCRToken]) -> list[OCRMetricRow]:
    rows = group_rows(tokens)
    header_index = -1
    headers: dict[str, float] = {}
    for index, row in enumerate(rows):
        candidates = [(_header_key(cell.text), cell.left) for cell in row.cells]
        known = [(key, left) for key, left in candidates if key]
        if len(known) >= 2:
            header_index = index
            headers = dict(known)
            break
    if header_index < 0:
        return []
    results: list[OCRMetricRow] = []
    for row in rows[header_index + 1 :]:
        if not row.cells:
            continue
        product_cell = _nearest_cell(row.cells, headers.get("product_name", row.cells[0].left))
        if not product_cell or _header_key(product_cell.text):
            continue
        code_cell = (
            _nearest_cell(row.cells, headers["product_code"]) if "product_code" in headers else None
        )
        metrics: dict[str, Decimal] = {}
        confidence = product_cell.confidence
        for key, left in headers.items():
            if key in {"product_name", "product_code"}:
                continue
            cell = _nearest_cell(row.cells, left, excluded={product_cell, code_cell})
            if not cell:
                continue
            try:
                metrics[key] = (
                    parse_number(cell.text) if key == "sharpe" else parse_percent(cell.text)
                )
            except ValueError:
                continue
            confidence = min(confidence, cell.confidence)
        if metrics:
            results.append(
                OCRMetricRow(
                    product_name=product_cell.text,
                    product_code=code_cell.text if code_cell else None,
                    metrics=metrics,
                    confidence=confidence,
                )
            )
    return results


def _nearest_cell(
    cells: tuple[ParsedCell, ...], left: float, excluded: set[ParsedCell] | None = None
) -> ParsedCell | None:
    excluded = excluded or set()
    candidates = [cell for cell in cells if cell not in excluded]
    return min(candidates, key=lambda cell: abs(cell.left - left), default=None)
