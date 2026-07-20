import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.ocr.engine import OCRToken
from app.ocr.table_parser import (
    extract_metric_rows,
    group_rows,
    is_confident,
    parse_number,
    parse_percent,
)


def fixture_tokens() -> list[OCRToken]:
    raw = json.loads(Path("tests/fixtures/ocr_tokens.json").read_text())
    return [
        OCRToken(item["text"], tuple(tuple(p) for p in item["box"]), item["confidence"])
        for item in raw
    ]


def test_group_rows_sorts_cells_and_groups_y_coordinates() -> None:
    rows = group_rows(fixture_tokens())
    assert [[cell.text for cell in row.cells] for row in rows] == [["产品A", "5.20%"], ["产品B"]]


def test_percent_and_number_parsing() -> None:
    assert parse_percent("(1.25%)") == Decimal("-0.0125")
    assert parse_percent("5.20%") == Decimal("0.052")
    assert parse_number("1,234.50") == Decimal("1234.50")


def test_ambiguous_number_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_percent("—")
    assert not is_confident(0.84)
    assert is_confident(0.85)


def test_extract_metric_rows_from_header_and_data_tokens() -> None:
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text, ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)), 0.99
        )

    rows = extract_metric_rows(
        [
            token("产品名称", 10, 10),
            token("近一周(%)", 100, 10),
            token("近一年夏普比", 180, 10),
            token("产品A", 10, 50),
            token("5.20%", 100, 50),
            token("1.25", 180, 50),
        ]
    )
    assert len(rows) == 1
    assert rows[0].product_name == "产品A"
    assert rows[0].metrics == {"weekly": Decimal("0.052"), "sharpe": Decimal("1.25")}


def test_extract_metric_rows_does_not_reuse_one_cell_for_missing_columns() -> None:
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text, ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)), 0.99
        )

    rows = extract_metric_rows(
        [
            token("产品名称", 10, 10),
            token("近一周(%)", 100, 10),
            token("MTD(%)", 200, 10),
            token("产品A", 10, 50),
            token("5.20%", 100, 50),
        ]
    )

    assert rows[0].metrics == {"weekly": Decimal("0.052")}


def test_extract_metric_rows_assigns_a_remaining_cell_to_its_own_column() -> None:
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text, ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)), 0.99
        )

    rows = extract_metric_rows(
        [
            token("产品名称", 10, 10),
            token("近一周(%)", 100, 10),
            token("MTD(%)", 200, 10),
            token("产品A", 10, 50),
            token("0.50%", 200, 50),
        ]
    )

    assert rows[0].metrics == {"mtd": Decimal("0.005")}


def test_extract_metric_rows_uses_each_repeated_header_layout() -> None:
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text, ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)), 0.99
        )

    rows = extract_metric_rows(
        [
            token("产品名称", 10, 10),
            token("近一周(%)", 100, 10),
            token("MTD(%)", 200, 10),
            token("产品A", 10, 50),
            token("1.00%", 100, 50),
            token("2.00%", 200, 50),
            token("产品名称", 710, 100),
            token("近一周(%)", 800, 100),
            token("MTD(%)", 900, 100),
            token("产品B", 710, 140),
            token("3.00%", 800, 140),
            token("4.00%", 900, 140),
        ]
    )

    assert [(row.product_name, row.metrics) for row in rows] == [
        ("产品A", {"weekly": Decimal("0.01"), "mtd": Decimal("0.02")} ),
        ("产品B", {"weekly": Decimal("0.03"), "mtd": Decimal("0.04")} ),
    ]


def test_extract_metric_rows_recovers_ytd_for_each_repeated_header() -> None:
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text, ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)), 0.99
        )

    rows = extract_metric_rows(
        [
            token("产品名称", 10, 10),
            token("近一周(%)", 100, 10),
            token("MTD(%)", 200, 10),
            token("(%)1A", 400, 10),
            token("2025(%)", 500, 10),
            token("产品A", 10, 50),
            token("1.00%", 100, 50),
            token("2.00%", 200, 50),
            token("3.00%", 300, 50),
            token("4.00%", 500, 50),
            token("产品名称", 710, 100),
            token("近一周(%)", 800, 100),
            token("MTD(%)", 900, 100),
            token("(%)1A", 1100, 100),
            token("2025(%)", 1200, 100),
            token("产品B", 710, 140),
            token("5.00%", 800, 140),
            token("6.00%", 900, 140),
            token("7.00%", 1000, 140),
            token("8.00%", 1200, 140),
        ]
    )

    assert [(row.product_name, row.metrics) for row in rows] == [
        (
            "产品A",
            {
                "weekly": Decimal("0.01"),
                "mtd": Decimal("0.02"),
                "ytd": Decimal("0.03"),
                "annual_2025": Decimal("0.04"),
            },
        ),
        (
            "产品B",
            {
                "weekly": Decimal("0.05"),
                "mtd": Decimal("0.06"),
                "ytd": Decimal("0.07"),
                "annual_2025": Decimal("0.08"),
            },
        ),
    ]


def test_extract_metric_rows_ignores_noise_before_a_valid_mtd_value() -> None:
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text, ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)), 0.99
        )

    rows = extract_metric_rows(
        [
            token("产品名称", 503, 10),
            token("近一周(%)", 1281, 10),
            token("MTD(%)", 1481, 10),
            token("(%)1A", 1678, 10),
            token("2025(%)", 1864, 10),
            token("仁桥金选泽源5B[1]", 503, 50),
            token("2020-05-22", 1088, 50),
            token("-0.10", 1366, 50),
            token("D", 1504, 50),
            token("2.68", 1564, 50),
            token("-12.95", 1736, 50),
            token("16.83", 1935, 50),
        ]
    )

    assert rows[0].metrics == {
        "weekly": Decimal("-0.001"),
        "mtd": Decimal("0.0268"),
        "ytd": Decimal("-0.1295"),
        "annual_2025": Decimal("0.1683"),
    }


def test_extract_metric_rows_uses_regular_one_a_column_as_ytd() -> None:
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text, ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)), 0.99
        )

    rows = extract_metric_rows(
        [
            token("产品名称", 503, 364),
            token("近一周(%)", 1281, 364),
            token("MTD(%)", 1481, 364),
            token("(%)1A", 1678, 364),
            token("2025(%)", 1864, 364),
            token("仁桥金选泽源5B[1]", 503, 2438),
            token("-0.10", 1366, 2438),
            token("2.68", 1564, 2438),
            token("-12.95", 1736, 2438),
            token("16.83", 1935, 2438),
        ]
    )

    assert rows[0].metrics == {
        "weekly": Decimal("-0.001"),
        "mtd": Decimal("0.0268"),
        "ytd": Decimal("-0.1295"),
        "annual_2025": Decimal("0.1683"),
    }


def test_extract_metric_rows_recovers_split_risk_headers_and_ytd_column() -> None:
    def token(text: str, left: float, top: float) -> OCRToken:
        return OCRToken(
            text, ((left, top), (left + 50, top), (left + 50, top + 20), (left, top + 20)), 0.99
        )

    rows = extract_metric_rows(
        [
            token("近一年", 1400, 80),
            token("近一年", 1600, 80),
            token("历史", 1800, 80),
            token("产品名称", 10, 100),
            token("近一周(%)", 100, 100),
            token("MTD(%)", 200, 100),
            token("(%)1A", 400, 100),
            token("2025(%)", 500, 100),
            token("2024(%)", 600, 100),
            token("2023(%)", 700, 100),
            token("2022(%)", 800, 100),
            token("2021(%)", 900, 100),
            token("2020(%)", 1000, 100),
            token("2019(%)", 1100, 100),
            token("最大回撤(%)", 1600, 130),
            token("最大回撤(%)", 1800, 130),
            token("产品A", 10, 200),
            token("5.20%", 100, 200),
            token("0.50%", 200, 200),
            token("2.68%", 300, 200),
            token("-12.95%", 400, 200),
            token("16.83%", 500, 200),
            token("19.10%", 600, 200),
            token("-4.01%", 700, 200),
            token("0.80%", 800, 200),
            token("37.20%", 900, 200),
            token("34.24%", 1000, 200),
            token("31.52%", 1100, 200),
            token("-0.52", 1400, 200),
            token("-21.28%", 1600, 200),
            token("-32.97%", 1800, 200),
        ]
    )

    assert rows[0].metrics == {
        "weekly": Decimal("0.052"),
        "mtd": Decimal("0.005"),
        "ytd": Decimal("0.0268"),
        "annual_2025": Decimal("0.1683"),
        "annual_2024": Decimal("0.191"),
        "annual_2023": Decimal("-0.0401"),
        "annual_2022": Decimal("0.008"),
        "annual_2021": Decimal("0.372"),
        "annual_2020": Decimal("0.3424"),
        "annual_2019": Decimal("0.3152"),
        "sharpe": Decimal("-0.52"),
        "max_drawdown": Decimal("-0.2128"),
    }
