from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.jobs.processor import utcnow as processor_now
from app.jobs.service import utcnow as service_now
from app.models import utcnow as model_now


def test_timestamp_factories_write_china_local_naive_times() -> None:
    expected = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

    for timestamp in (model_now(), processor_now(), service_now()):
        assert timestamp.tzinfo is None
        assert abs(timestamp - expected) < timedelta(seconds=2)
