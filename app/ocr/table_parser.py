from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
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
SOURCE_BLANK_MARKERS = {"-", "--", "—", "n/a"}


@dataclass(frozen=True)
class ParsedCell:
    text: str
    confidence: float
    left: float = 0.0
    box: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class MetricCellEvidence:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ParsedRow:
    cells: tuple[ParsedCell, ...]


@dataclass(frozen=True)
class OCRMetricRow:
    product_name: str
    product_code: str | None
    metrics: dict[str, Decimal]
    confidence: float
    blank_metrics: frozenset[str] = frozenset()
    metric_evidence: dict[str, MetricCellEvidence] = field(default_factory=dict)


def group_rows(tokens: Iterable[OCRToken], y_tolerance: float = 12.0) -> list[ParsedRow]:
    rows: list[list[OCRToken]] = []
    for token in sorted(tokens, key=lambda item: (item.center_y, item.left)):
        target = next(
            (
                row
                for row in rows
                if abs(sum(item.center_y for item in row) / len(row) - token.center_y)
                <= y_tolerance
                or any(
                    min(point[1] for point in item.box)
                    < max(point[1] for point in token.box)
                    and min(point[1] for point in token.box)
                    < max(point[1] for point in item.box)
                    for item in row
                )
            ),
            None,
        )
        if target is None:
            rows.append([token])
        else:
            target.append(token)
    return [
        ParsedRow(
            tuple(
                ParsedCell(token.text, token.confidence, token.left, token.box)
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


def _is_source_blank(text: str) -> bool:
    return text.strip().replace(" ", "").lower() in SOURCE_BLANK_MARKERS


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
    if value in {"(%)1a", "(%)ia", "(%)1"}:
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
    header_blocks = _header_blocks(rows)
    if not header_blocks:
        return []

    results: list[OCRMetricRow] = []
    for block_index, (header_index, headers) in enumerate(header_blocks):
        next_header_index = (
            header_blocks[block_index + 1][0]
            if block_index + 1 < len(header_blocks)
            else len(rows)
        )
        for row in rows[header_index + 1 : next_header_index]:
            if not row.cells:
                continue
            product_cell = _nearest_cell(row.cells, headers.get("product_name", row.cells[0].left))
            if not product_cell or _header_key(product_cell.text):
                continue
            code_cell = (
                _nearest_cell(row.cells, headers["product_code"])
                if "product_code" in headers
                else None
            )
            metrics: dict[str, Decimal] = {}
            blank_metrics: set[str] = set()
            metric_evidence: dict[str, MetricCellEvidence] = {}
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
                metric_evidence[key] = MetricCellEvidence(
                    text=cell.text,
                    confidence=cell.confidence,
                    box=cell.box,
                )
                if _is_source_blank(cell.text):
                    blank_metrics.add(key)
                    confidence = min(confidence, cell.confidence)
                    continue
                try:
                    metrics[key] = _parse_metric_cell(key, cell.text)
                except ValueError:
                    continue
                confidence = min(confidence, cell.confidence)
            if metrics or blank_metrics:
                results.append(
                    OCRMetricRow(
                        product_name=product_cell.text,
                        product_code=code_cell.text if code_cell else None,
                        metrics=metrics,
                        confidence=confidence,
                        blank_metrics=frozenset(blank_metrics),
                        metric_evidence=metric_evidence,
                    )
                )
    return results


def _header_blocks(rows: list[ParsedRow]) -> list[tuple[int, dict[str, float]]]:
    blocks: list[tuple[int, dict[str, float]]] = []
    for index, row in enumerate(rows):
        candidates = [(_header_key(cell.text), cell.left) for cell in row.cells]
        known = [(key, left) for key, left in candidates if key]
        known_keys = {key for key, _ in known}
        if "product_name" in known_keys and len(known_keys & METRIC_KEYS) >= 1:
            headers = dict(known)
            headers.update(_supplement_headers(rows, index, headers))
            blocks.append((index, headers))
    return blocks


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
    header_distances = {
        key: min(
            (abs(left - other_left) for other_key, other_left in headers if other_key != key),
            default=None,
        )
        for key, left in headers
    }
    for cell in cells:
        if cell in excluded:
            continue
        key, left = min(headers, key=lambda item: (abs(cell.left - item[1]), item[1]))
        nearest_header_gap = header_distances.get(key)
        if nearest_header_gap is not None and abs(cell.left - left) > max(
            120.0, nearest_header_gap * 0.75
        ):
            continue
        if not _is_source_blank(cell.text):
            try:
                _parse_metric_cell(key, cell.text)
            except ValueError:
                continue
        existing = assigned.get(key)
        if existing is None or abs(cell.left - left) < abs(existing.left - left):
            assigned[key] = cell
    return assigned


def _parse_metric_cell(key: str, text: str) -> Decimal:
    return parse_number(text) if key == "sharpe" else parse_percent(text)
