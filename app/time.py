from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

CHINA_TIME_ZONE = ZoneInfo("Asia/Shanghai")


def china_now() -> datetime:
    return datetime.now(CHINA_TIME_ZONE).replace(tzinfo=None)
