from datetime import date
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import meetings, models
from app.db import Base


def test_meeting_persists_source_and_team_fields() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    meeting = models.Meeting(
        source_key="a" * 64,
        title="2026陆家嘴论坛",
        date_raw="2026-06-17至2026-06-18",
        date_start=date(2026, 6, 17),
        date_end=date(2026, 6, 18),
        date_parse_status="normalized",
        level="金融高层论坛",
        core_statement="服务高质量发展",
        market_impact="投融资综合改革",
        research_mapping="科技成长",
        follow_up="跟踪改革细则",
        source_link="https://example.test/source",
        source_updated_at="2026-07-18",
        company_tags="券商, 创投",
        industry_tags="金融, 科技",
        attendance_status="planned",
        minutes="安排参会",
        todo="跟踪规则",
        conclusion="长期利好",
    )
    session.add(meeting)
    session.commit()

    stored = session.scalar(select(models.Meeting))

    assert stored is not None
    assert stored.date_end == date(2026, 6, 18)
    assert stored.attendance_status == "planned"


def meeting_workbook(tmp_path: Path, impact: str) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "近期会议更新"
    sheet.append(["近期资本市场相关会议更新"])
    sheet.append(
        [
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
    )
    sheet.append(
        [
            "2026陆家嘴论坛",
            "2026-06-17至2026-06-18",
            "金融高层论坛",
            "服务高质量发展",
            impact,
            "科技成长",
            "跟踪改革细则",
            "https://example.test/source",
            "2026-07-18",
        ]
    )
    output = tmp_path / f"{impact}.xlsx"
    workbook.save(output)
    return output


def test_import_updates_source_but_preserves_team_record(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    first = meetings.import_meetings(session, meeting_workbook(tmp_path, "首次影响"))

    assert (first.created, first.updated) == (1, 0)
    meeting = session.scalar(select(models.Meeting))
    assert meeting is not None
    meeting.company_tags = "券商"
    meeting.minutes = "研究员会议纪要"
    session.commit()

    second = meetings.import_meetings(session, meeting_workbook(tmp_path, "更新影响"))
    refreshed = session.scalar(select(models.Meeting))

    assert (second.created, second.updated) == (0, 1)
    assert refreshed is not None
    assert refreshed.market_impact == "更新影响"
    assert refreshed.company_tags == "券商"
    assert refreshed.minutes == "研究员会议纪要"
