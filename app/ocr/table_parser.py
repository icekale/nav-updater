from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .engine import OCRToken

METRIC_KEYS = {
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
}


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
    if value in {"(%)1a", "(%)ia"}:
        return "one_year_return"
    if value == "近一年":
        return "near_one_label"
    if "最大回撤" in value:
        return "max_drawdown_label"
    if value.startswith("历史"):
        return "history_label"
    match = re.match(r"^(2019|2020|2021|2022|2023|2024|2025)", value)
    return f"annual_{match.group(1)}" if match else None


def extract_metric_rows(tokens: Iterable[OCRToken]) -> list[OCRMetricRow]:
    rows = group_rows(tokens)
    header_index = -1
    headers: dict[str, float] = {}
    for index, row in enumerate(rows):
        candidates = [(_header_key(cell.text), cell.left) for cell in row.cells]
        known = [(key, left) for key, left in candidates if key]
        known_keys = {key for key, _ in known}
        if "product_name" in known_keys and len(known_keys & METRIC_KEYS) >= 1:
            header_index = index
            headers = dict(known)
            break
    if header_index < 0:
        return []
    headers.update(_supplement_headers(rows, header_index, headers))
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
        metric_cells = _metric_cells_by_header(
            row.cells,
            [(key, left) for key, left in headers.items() if key in METRIC_KEYS],
            {product_cell, code_cell} if code_cell is not None else {product_cell},
        )
        for key, left in headers.items():
            if key not in METRIC_KEYS:
                continue
            cell = metric_cells.get(key)
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


def _supplement_headers(
    rows: list[ParsedRow], header_index: int, headers: dict[str, float]
) -> dict[str, float]:
    supplements: dict[str, float] = {}
    nearby_rows = rows[max(0, header_index - 1) : header_index + 2]
    near_one = [
        cell.left
        for row in nearby_rows
        for cell in row.cells
        if _header_key(cell.text) == "near_one_label"
    ]
    drawdowns = [
        cell.left
        for row in nearby_rows
        for cell in row.cells
        if _header_key(cell.text) == "max_drawdown_label"
    ]
    if "mtd" in headers and "one_year_return" in headers and "ytd" not in headers:
        mtd_left = headers["mtd"]
        one_year_left = headers["one_year_return"]
        next_metric_left = min(
            (
                left
                for key, left in headers.items()
                if key in METRIC_KEYS and left > one_year_left
            ),
            default=None,
        )
        if (
            next_metric_left is not None
            and (next_metric_left - one_year_left) * 4 >= (one_year_left - mtd_left) * 3
        ):
            supplements["ytd"] = one_year_left
        else:
            supplements["ytd"] = (mtd_left + one_year_left) / 2

    paired_near_one = {
        left
        for left in near_one
        if any(abs(left - drawdown) <= 120 for drawdown in drawdowns)
    }
    if "sharpe" not in headers:
        sharpe_columns = [left for left in near_one if left not in paired_near_one]
        if sharpe_columns:
            supplements["sharpe"] = min(sharpe_columns)
    if "max_drawdown" not in headers and paired_near_one:
        supplements["max_drawdown"] = min(paired_near_one)
    return supplements


def _nearest_cell(
    cells: tuple[ParsedCell, ...], left: float, excluded: set[ParsedCell] | None = None
) -> ParsedCell | None:
    excluded = excluded or set()
    candidates = [cell for cell in cells if cell not in excluded]
    return min(candidates, key=lambda cell: abs(cell.left - left), default=None)


def _metric_cells_by_header(
    cells: tuple[ParsedCell, ...],
    headers: list[tuple[str, float]],
    excluded: set[ParsedCell],
) -> dict[str, ParsedCell]:
    assigned: dict[str, ParsedCell] = {}
    for cell in cells:
        if cell in excluded:
            continue
        key, left = min(headers, key=lambda item: (abs(cell.left - item[1]), item[1]))
        existing = assigned.get(key)
        if existing is None or abs(cell.left - left) < abs(existing.left - left):
            assigned[key] = cell
    return assigned
