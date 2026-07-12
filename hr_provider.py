"""Minute-level HR behind a small provider adapter, so the UNDOCUMENTED WHOOP source is
isolated and swappable. Selected by config.HR_PROVIDER:

  * "unofficial" (default): password login via whoop_source (whoop-data library)
  * "none": disabled — the official day-level API still works independently

The token-paste route (whoop_token_hr.py) stays a manual one-shot backfill for when the
password login is unavailable.
"""
import config


def enabled():
    return (config.HR_PROVIDER or "unofficial").lower() != "none"


def fetch_hr(start_date, end_date, step=60, debug=False):
    provider = (config.HR_PROVIDER or "unofficial").lower()
    if provider == "none":
        return []
    if provider == "unofficial":
        import whoop_source
        return whoop_source.fetch_hr(start_date, end_date, step=step, debug=debug)
    raise ValueError(f"unknown HR_PROVIDER: {provider}")
