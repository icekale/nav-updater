from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from app.excel.template_adapter import TemplateAdapter

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
FIXTURE = Path("tests/fixtures/net_value_template.xlsx")


def test_inspect_products_keeps_blank_rows() -> None:
    rows = TemplateAdapter().inspect_products(FIXTURE)
    assert rows[0].row_number == 2
    assert rows[0].product_name == "仁桥金选泽源5B"
    assert any(row.row_number == 8 and row.product_name is None for row in rows)


def test_apply_updates_preserves_package_and_marks_stale(tmp_path: Path) -> None:
    output = tmp_path / "updated.xlsx"
    with ZipFile(FIXTURE) as source:
        original = {name: source.read(name) for name in source.namelist()}
    TemplateAdapter().apply_updates(
        FIXTURE,
        output,
        {
            2: {
                "weekly": Decimal("0.1234"),
                "sharpe": Decimal("1.25"),
                "max_drawdown": Decimal("-0.10"),
            }
        },
        {2: {"max_drawdown"}},
    )
    with ZipFile(output) as result:
        updated = {name: result.read(name) for name in result.namelist()}
    assert set(original) == set(updated)
    for name in original:
        if name not in {"xl/worksheets/sheet1.xml", "xl/styles.xml"}:
            assert updated[name] == original[name]
    sheet = etree.fromstring(updated["xl/worksheets/sheet1.xml"])
    assert sheet.xpath('string(.//x:c[@r="F2"]/x:v)', namespaces=NS) == "12.34"
    assert sheet.xpath('string(.//x:c[@r="P2"]/x:v)', namespaces=NS) == "1.25"
    assert sheet.xpath('string(.//x:c[@r="Q2"]/x:v)', namespaces=NS) == "-10.00"
    assert sheet.xpath('string(.//x:c[@r="F8"]/x:v)', namespaces=NS) == ""


def test_blank_product_row_is_not_written(tmp_path: Path) -> None:
    output = tmp_path / "updated.xlsx"
    TemplateAdapter().apply_updates(FIXTURE, output, {8: {"weekly": Decimal("0.50")}})
    with ZipFile(output) as archive:
        sheet = etree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    assert not sheet.xpath('.//x:c[@r="F8"]', namespaces=NS)


def test_stale_without_new_value_keeps_existing_cell(tmp_path: Path) -> None:
    output = tmp_path / "updated.xlsx"
    TemplateAdapter().apply_updates(
        FIXTURE,
        output,
        {2: {"weekly": None}},
        {2: {"weekly"}},
    )
    with ZipFile(output) as archive:
        sheet = etree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    cell = sheet.xpath('.//x:c[@r="F2"]', namespaces=NS)[0]
    assert cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v") is None


def test_stale_metric_without_an_update_is_marked_in_the_template(tmp_path: Path) -> None:
    output = tmp_path / "updated.xlsx"
    TemplateAdapter().apply_updates(
        FIXTURE,
        output,
        {2: {"weekly": Decimal("0.50")}},
        {2: {"mtd"}},
    )
    with ZipFile(output) as archive:
        sheet = etree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    cell = sheet.xpath('.//x:c[@r="G2"]', namespaces=NS)[0]
    assert cell.get("s") is not None
    assert cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v") is None
