from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")
DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_beijing_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TIMEZONE).strftime(DISPLAY_FORMAT)
