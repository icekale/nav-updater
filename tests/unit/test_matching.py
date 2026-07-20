import pytest

from app.domain.matching import (
    CatalogConflict,
    CatalogRecord,
    match_product,
    normalize_name,
    parse_catalog_csv,
)


def test_normalize_name_removes_spacing_and_full_width_parentheses() -> None:
    assert normalize_name(" 易方达（环保主题） A ") == "易方达(环保主题)a"


def test_normalize_name_removes_ocr_footnote_suffix() -> None:
    assert normalize_name("仁桥金选泽源5B[1]") == normalize_name("仁桥金选泽源5B")
    assert normalize_name("开思金选港股通1号B[]") == normalize_name("开思金选港股通1号B")


def test_catalog_requires_exact_three_columns() -> None:
    records = parse_catalog_csv("product_name,product_code,product_type\n基金 A,001856,public\n")
    assert records == [CatalogRecord("基金 A", "001856", "public")]


def test_catalog_rejects_duplicate_codes() -> None:
    with pytest.raises(CatalogConflict):
        parse_catalog_csv(
            "product_name,product_code,product_type\n基金 A,001856,public\n基金 B,001856,public\n"
        )


def test_matching_prefers_code_then_exact_name() -> None:
    records = [
        CatalogRecord("易方达环保主题混合A", "001856", "public"),
        CatalogRecord("私募策略 B", "P-002", "private"),
    ]
    assert match_product(product_code="001856", product_name="其他", products=records) == records[0]
    assert (
        match_product(product_code=None, product_name="私募 策略 B", products=records) == records[1]
    )
    assert match_product(product_code=None, product_name="私募", products=records) is None
