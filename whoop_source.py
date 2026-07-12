"""Whoop gayriresmi dahili API'sinden dakikalik nabiz ceker (whoop-data kutuphanesi).

NOT: Bu kutuphane Whoop web uygulamasinin ic API'sini tersine muhendislikle kullanir.
Whoop ToS acisindan gri alandir; arayuz degisirse kirilir (2025'te bir kez kirilmis).
Kirilirsa SADECE bu dosya guncellenir; botun geri kalani (loglama) etkilenmez.

Onemli: dahili HR sorgusu ~7 gunluk pencere limitine sahiptir -> istekleri chunk'lariz.
"""
import json
from datetime import UTC, datetime, timedelta

import config

_DEBUG_DUMP = "hr_sample_debug.json"


def _to_epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 1e12 else int(value)
    if isinstance(value, str):
        s = value.replace("Z", "+00:00")
        try:
            return int(datetime.fromisoformat(s).timestamp())
        except ValueError:
            return None
    return None


def _normalize(raw):
    """whoop-data ciktisini [(epoch, bpm)] listesine cevirir; 0/off-wrist filtrelenir."""
    time_keys = ("time", "timestamp", "t", "datetime", "date")
    bpm_keys = ("bpm", "heart_rate", "heartRate", "value", "y")
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        ts = next((_to_epoch(item[k]) for k in time_keys if k in item), None)
        bpm = next((item[k] for k in bpm_keys if k in item), None)
        if ts is None or bpm is None:
            continue
        try:
            b = int(round(float(bpm)))
        except (TypeError, ValueError):
            continue
        if b > 0:  # off-wrist / bosluk
            out.append((ts, b))
    return out


def _client():
    from whoop_data import WhoopClient
    return WhoopClient(username=config.WHOOP_EMAIL, password=config.WHOOP_PASSWORD)


def _fetch_window(client, start_date, end_date, step, debug):
    from whoop_data import get_heart_rate_data
    raw = get_heart_rate_data(
        client=client, start_date=start_date, end_date=end_date, step=step
    )
    if debug:
        with open(_DEBUG_DUMP, "w", encoding="utf-8") as f:
            sample = raw[:5] if isinstance(raw, list) else raw
            json.dump(sample, f, indent=2, default=str, ensure_ascii=False)
    return _normalize(raw)


def tile_windows(start_date, end_date, window_days):
    """[start,end]'i bosluksuz, ust uste binmeden, her biri <= window_days takvim gunu
    olan kapsayici pencerelere boler. Doner: [(start_str, end_str)]. (Saf/test edilebilir.)"""
    d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
    span = timedelta(days=max(1, window_days - 1))  # [cur, cur+(N-1)] = tam N takvim gunu
    out, cur = [], d0
    while cur <= d1:
        chunk_end = min(cur + span, d1)
        out.append((cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cur = chunk_end + timedelta(days=1)
    return out


def fetch_hr(start_date, end_date, step=60, debug=False):
    """start_date/end_date: 'YYYY-MM-DD'. ~7 gunluk chunk'lara boler. Doner [(epoch,bpm)]."""
    config.require_hr()
    client = _client()
    out = []
    for i, (s, e) in enumerate(tile_windows(start_date, end_date, config.HR_WINDOW_DAYS)):
        out += _fetch_window(client, s, e, step, debug and i == 0)
    return out


def today_str():
    return datetime.now(UTC).strftime("%Y-%m-%d")
