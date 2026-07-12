"""Automatic context sources: turn calendar / Slack activity into "who you were with"
events, so the stress analysis can run without any manual logging.

Each source exposes ``fetch(days) -> list[Meeting]``. ``auto_sync`` writes those into the
``events`` table, de-duplicated by ``(source, ext_id)``, and the existing stress engine
correlates them with WHOOP data unchanged.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Meeting:
    ext_id: str            # source-side id (for de-duplication)
    ts_start: int          # epoch, UTC
    ts_end: int
    person: Optional[str]  # who you were with
    title: Optional[str]
    tag: str               # context label (e.g. "work", "slack")
    source: str            # "google_calendar" | "slack" | ...


def load(name):
    """Resolve a source name to its ``fetch`` callable (lazy import)."""
    if name == "google_calendar":
        from . import google_calendar
        return google_calendar.fetch
    if name == "slack":
        from . import slack
        return slack.fetch
    raise ValueError(f"unknown source: {name}")


def mode(name):
    """How auto_sync should persist a source's events:
    - "upsert": provider ids are stable -> de-dup by (source, ext_id), preserve edits.
    - "replace_window": ids depend on recomputed boundaries (e.g. Slack clustering) ->
      delete the window and re-insert, so shifting boundaries can't create duplicates.
    """
    return {"slack": "replace_window"}.get(name, "upsert")
