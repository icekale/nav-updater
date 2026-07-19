from datetime import date
from importlib import import_module
from pathlib import Path

import pytest
from openpyxl import Workbook

HEADERS = [
    "会议/事件",
    "日期",
    "性质/层级",
    "核心表述",
    "资本市场影响",
    "投研映射",
    "后续跟踪",
    "来源链接",
    "更新时间",
]


def meeting_module():
    return import_module("app.meetings")


def workbook_with_headers(tmp_path: Path, headers: list[str]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "近期会议更新"
    sheet.append(["近期资本市场相关会议更新"])
    sheet.append(headers)
    output = tmp_path / "meetings.xlsx"
    workbook.save(output)
    return output


def test_parse_date_range_supports_single_date_and_chinese_range() -> None:
    meetings = meeting_module()

    assert meetings.parse_date_range("2026-06-06") == (date(2026, 6, 6), date(2026, 6, 6))
    assert meetings.parse_date_range("2026-06-17至2026-06-18") == (
        date(2026, 6, 17),
        date(2026, 6, 18),
    )
    assert meetings.parse_date_range("待定") == (None, None)


def test_source_key_normalizes_title_whitespace_without_merging_dates() -> None:
    meetings = meeting_module()

    assert meetings.source_key(" 2026 陆家嘴论坛 ", "2026-06-17") == meetings.source_key(
        "2026陆家嘴论坛", "2026-06-17"
    )
    assert meetings.source_key("2026陆家嘴论坛", "2026-06-17") != meetings.source_key(
        "2026陆家嘴论坛", "2026-06-18"
    )


def test_read_meeting_rows_rejects_missing_required_header(tmp_path: Path) -> None:
    meetings = meeting_module()
    workbook = workbook_with_headers(tmp_path, ["会议/事件", "日期"])

    with pytest.raises(meetings.MeetingImportError, match="缺少列"):
        meetings.read_meeting_rows(workbook)
