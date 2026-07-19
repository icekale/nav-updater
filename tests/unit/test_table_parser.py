import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.ocr.engine import OCRToken
from app.ocr.table_parser import group_rows, is_confident, parse_number, parse_percent


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
