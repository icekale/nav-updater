from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


class CatalogConflict(ValueError):
    pass


@dataclass(frozen=True)
class CatalogRecord:
    product_name: str
    product_code: str
    product_type: str


def normalize_name(value: str) -> str:
    text = value.strip().replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"\[(?:\d+|[il]*)\]$", "", text, flags=re.IGNORECASE)
    return text.casefold()


def normalize_ocr_name(value: str) -> str:
    text = normalize_name(value)
    text = re.sub(r"[\]\}]+$", "", text)
    return text


def is_unique_ocr_name_match(
    expected_name: str, ocr_name: str, candidate_names: Iterable[str]
) -> bool:
    expected = normalize_name(expected_name)
    if normalize_ocr_name(ocr_name) == expected:
        return True
    ocr_prefix = _leading_chinese(normalize_ocr_name(ocr_name))
    if len(ocr_prefix) < 4:
        return False
    candidates = {
        normalize_name(name)
        for name in candidate_names
        if _leading_chinese(name).startswith(ocr_prefix)
    }
    return candidates == {expected}


def _leading_chinese(value: str) -> str:
    match = re.match(r"[\u4e00-\u9fff]+", value.strip())
    return match.group(0) if match else ""


def parse_catalog_rows(rows: Iterable[Mapping[str, str]]) -> list[CatalogRecord]:
    records: list[CatalogRecord] = []
    seen_codes: set[str] = set()
    for index, row in enumerate(rows, start=2):
        try:
            name = row["product_name"].strip()
            code = row["product_code"].strip()
            product_type = row["product_type"].strip().lower()
        except KeyError as exc:
            raise CatalogConflict(
                "catalog must contain product_name, product_code, product_type"
            ) from exc
        if not name or not code or product_type not in {"public", "private"}:
            raise CatalogConflict(f"invalid catalog row {index}")
        if code in seen_codes:
            raise CatalogConflict(f"duplicate product_code at row {index}: {code}")
        seen_codes.add(code)
        records.append(CatalogRecord(name, code, product_type))
    return records


def parse_catalog_csv(text: str) -> list[CatalogRecord]:
    reader = csv.DictReader(io.StringIO(text))
    return parse_catalog_rows(reader)


def match_product(
    *, product_code: str | None, product_name: str, products: Iterable[CatalogRecord]
) -> CatalogRecord | None:
    records = list(products)
    if product_code:
        exact_code = [record for record in records if record.product_code == product_code.strip()]
        if len(exact_code) == 1:
            return exact_code[0]
    normalized = normalize_name(product_name)
    exact_name = [record for record in records if normalize_name(record.product_name) == normalized]
    return exact_name[0] if len(exact_name) == 1 else None
