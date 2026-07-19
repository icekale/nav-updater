from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NSMAP = {"x": NS}
SHEET_PATH = "xl/worksheets/sheet1.xml"
STYLES_PATH = "xl/styles.xml"
PERCENT_METRICS = {
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
    "max_drawdown",
}
HEADER_TO_METRIC = {
    "近一周（%）": "weekly",
    "MTD（%）": "mtd",
    "YTD（%）": "ytd",
    "2019（%）": "annual_2019",
    "2020（%）": "annual_2020",
    "2021（%）": "annual_2021",
    "2022（%）": "annual_2022",
    "2023（%）": "annual_2023",
    "2024（%）": "annual_2024",
    "2025（%）": "annual_2025",
    "近一年\n夏普比": "sharpe",
    "近一年\n最大回撤（%）": "max_drawdown",
}


@dataclass(frozen=True)
class ProductRow:
    row_number: int
    product_name: str | None


def _qname(local: str) -> str:
    return f"{{{NS}}}{local}"


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = etree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.xpath(".//x:t/text()", namespaces=NSMAP))
        for node in root.xpath("x:si", namespaces=NSMAP)
    ]


def _cell_text(cell: etree._Element, strings: list[str]) -> str:
    value = cell.find(_qname("v"))
    if value is None or value.text is None:
        inline = cell.xpath("string(x:is//x:t)", namespaces=NSMAP)
        return inline
    if cell.get("t") == "s":
        return strings[int(value.text)]
    return value.text


def _cell_by_ref(sheet: etree._Element, ref: str) -> etree._Element | None:
    found = sheet.xpath(f'.//x:c[@r="{ref}"]', namespaces=NSMAP)
    return found[0] if found else None


def _column_number(ref: str) -> int:
    number = 0
    for char in ref:
        if char.isalpha():
            number = number * 26 + ord(char.upper()) - ord("A") + 1
    return number


def _insert_cell(row: etree._Element, cell: etree._Element) -> None:
    target = _column_number(cell.get("r", "A1"))
    for index, child in enumerate(row):
        if child.tag != _qname("c"):
            continue
        if _column_number(child.get("r", "A1")) > target:
            row.insert(index, cell)
            return
    row.append(cell)


def _find_or_create_cell(
    sheet: etree._Element, row_number: int, column: str, style: str | None
) -> etree._Element:
    ref = f"{column}{row_number}"
    existing = _cell_by_ref(sheet, ref)
    if existing is not None:
        if style is not None and existing.get("s") is None:
            existing.set("s", style)
        return existing
    row_nodes = sheet.xpath(f'.//x:row[@r="{row_number}"]', namespaces=NSMAP)
    if not row_nodes:
        raise ValueError(f"template is missing row {row_number}")
    cell = etree.Element(_qname("c"), r=ref)
    if style is not None:
        cell.set("s", style)
    _insert_cell(row_nodes[0], cell)
    return cell


def _set_numeric(cell: etree._Element, value: Decimal | None) -> None:
    cell.attrib.pop("t", None)
    old = cell.find(_qname("v"))
    if old is not None:
        cell.remove(old)
    if value is not None:
        node = etree.SubElement(cell, _qname("v"))
        node.text = format(value, "f")


def _append_error_style(styles: etree._Element) -> int:
    fills = styles.find(_qname("fills"))
    cell_xfs = styles.find(_qname("cellXfs"))
    if fills is None or cell_xfs is None:
        raise ValueError("template styles.xml is missing fills or cellXfs")
    base = cell_xfs[-1]
    fill = etree.SubElement(fills, _qname("fill"))
    pattern = etree.SubElement(fill, _qname("patternFill"), patternType="solid")
    etree.SubElement(pattern, _qname("fgColor"), rgb="FFFFC7CE")
    etree.SubElement(pattern, _qname("bgColor"), indexed="64")
    fills.set("count", str(len(fills)))

    error = etree.fromstring(etree.tostring(base))
    error.set("fillId", str(len(fills) - 1))
    error.set("applyFill", "1")
    cell_xfs.append(error)
    cell_xfs.set("count", str(len(cell_xfs)))
    return len(cell_xfs) - 1


class TemplateAdapter:
    def inspect_products(self, path: str | Path) -> list[ProductRow]:
        with ZipFile(path) as archive:
            strings = _shared_strings(archive)
            sheet = etree.fromstring(archive.read(SHEET_PATH))
            rows: list[ProductRow] = []
            for row in sheet.xpath(".//x:row", namespaces=NSMAP):
                number = int(row.get("r", "0"))
                if number <= 1:
                    continue
                cell = _cell_by_ref(sheet, f"E{number}")
                name = _cell_text(cell, strings).strip() if cell is not None else ""
                rows.append(ProductRow(number, name or None))
            return rows

    def apply_updates(
        self,
        input_path: str | Path,
        output_path: str | Path,
        updates: Mapping[int, Mapping[str, Decimal | None]],
        stale: Mapping[int, set[str]] | None = None,
    ) -> None:
        stale = stale or {}
        with ZipFile(input_path) as source:
            parts = {name: source.read(name) for name in source.namelist()}
            sheet = etree.fromstring(parts[SHEET_PATH])
            styles = etree.fromstring(parts[STYLES_PATH])
            strings = _shared_strings(source)
            headers: dict[str, str] = {}
            for cell in sheet.xpath('.//x:row[@r="1"]/x:c', namespaces=NSMAP):
                header = _cell_text(cell, strings).replace("\r\n", "\n")
                if header in HEADER_TO_METRIC:
                    headers[HEADER_TO_METRIC[header]] = cell.get("r", "A1").rstrip("0123456789")
            missing = set(HEADER_TO_METRIC.values()) - set(headers)
            if missing:
                raise ValueError(f"template is missing target headers: {sorted(missing)}")
            error_style = _append_error_style(styles) if any(stale.values()) else None
            for row_number, values in updates.items():
                product_cell = _cell_by_ref(sheet, f"E{row_number}")
                if product_cell is None or not _cell_text(product_cell, strings).strip():
                    continue
                row_style = product_cell.get("s")
                for metric, raw_value in values.items():
                    if metric not in headers:
                        raise ValueError(f"unknown metric: {metric}")
                    value = raw_value
                    if metric in PERCENT_METRICS and value is not None:
                        value *= Decimal("100")
                    cell = _find_or_create_cell(sheet, row_number, headers[metric], row_style)
                    if value is not None:
                        _set_numeric(cell, value.quantize(Decimal("0.01")))
                    if metric in stale.get(row_number, set()) and error_style is not None:
                        cell.set("s", str(error_style))
            parts[SHEET_PATH] = etree.tostring(
                sheet, xml_declaration=True, encoding="UTF-8", standalone=True
            )
            parts[STYLES_PATH] = etree.tostring(
                styles, xml_declaration=True, encoding="UTF-8", standalone=True
            )
            with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as target:
                for name, content in parts.items():
                    target.writestr(name, content)
