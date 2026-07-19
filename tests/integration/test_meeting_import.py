from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models
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
