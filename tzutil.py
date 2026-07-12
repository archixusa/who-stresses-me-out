"""Yerel saat yardimcilari. Depolama hep epoch UTC; gosterim yerel (LOCAL_TZ)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config

_TZ = ZoneInfo(config.LOCAL_TZ)


def local_dt(ts):
    return datetime.fromtimestamp(ts, tz=_TZ)


def fmt(ts, pattern="%d/%m %H:%M"):
    return local_dt(ts).strftime(pattern)


def local_day_bounds(ts):
    """ts'nin dahil oldugu YEREL gunun [00:00, 24:00) epoch sinirlari."""
    d = local_dt(ts)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59)
    return int(start.timestamp()), int(end.timestamp()) + 1


def local_hour(ts):
    return local_dt(ts).hour


def now_ts():
    return int(datetime.now(timezone.utc).timestamp())
