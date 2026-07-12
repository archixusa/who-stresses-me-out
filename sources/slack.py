"""Slack auto-source — EXPERIMENTAL / heuristic.

Text chat maps poorly onto a physiological stress window (you're not necessarily
tense while typing), so treat this as a *weak* signal. It clusters direct-message
activity: human messages with one person, spaced less than SLACK_GAP_MIN apart, are
grouped into a single "conversation window".

Because cluster boundaries can shift as the window slides, this source uses
``MODE = "replace_window"``: auto_sync deletes the window's Slack events and re-inserts
the freshly computed ones, so re-running never leaves duplicates.

Setup: create a Slack app, add a User OAuth Token (xoxp-...) with scopes
``im:history``, ``im:read``, ``users:read``, and put it in SLACK_TOKEN.
"""
import time

import requests

import config

from . import Meeting

API = "https://slack.com/api"
_TIMEOUT = 30
MIN_WINDOW_SEC = 60          # sub-minute windows can't yield usable HR samples
MODE = "replace_window"


def _get(method, token, _tries=4, **params):
    """Slack Web API GET with 429/rate-limit retry (honours Retry-After)."""
    for _ in range(_tries):
        r = requests.get(f"{API}/{method}", params=params,
                         headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
        if r.status_code == 429:
            time.sleep(min(int(r.headers.get("Retry-After", "1")), 30))
            continue
        data = r.json()
        if data.get("ok"):
            return data
        if data.get("error") == "ratelimited":
            time.sleep(2)
            continue
        raise RuntimeError(f"Slack {method}: {data.get('error')}")
    raise RuntimeError(f"Slack {method}: rate-limited after {_tries} tries")


def _paginated(method, token, key, **params):
    """Follow response_metadata.next_cursor until exhausted (no silent truncation)."""
    out, cursor = [], None
    while True:
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        data = _get(method, token, **p)
        out.extend(data.get(key, []))
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    return out


def _is_human(m):
    """A real message from a person (not a bot / app / system message)."""
    return (m.get("type") == "message" and "ts" in m
            and not m.get("bot_id") and m.get("subtype") not in ("bot_message",))


def cluster(timestamps, gap_sec):
    """Group epoch times into windows -> [(start, end, count)]. Pure & unit-testable."""
    ts = sorted(t for t in timestamps)
    if not ts:
        return []
    out, start, prev, n = [], ts[0], ts[0], 1
    for t in ts[1:]:
        if t - prev <= gap_sec:
            prev, n = t, n + 1
        else:
            out.append((start, prev, n))
            start, prev, n = t, t, 1
    out.append((start, prev, n))
    return out


def fetch(days=None):
    days = days or config.SYNC_DAYS
    token = config.SLACK_TOKEN
    if not token:
        raise RuntimeError("SLACK_TOKEN missing.")
    gap = config.SLACK_GAP_MIN * 60
    oldest = time.time() - days * 86400
    out = []

    ims = _paginated("conversations.list", token, "channels", types="im", limit=200)
    for im in ims:
        try:
            uinfo = _get("users.info", token, user=im.get("user")).get("user", {})
            if uinfo.get("is_bot") or uinfo.get("id") == "USLACKBOT":
                continue  # bots / Slackbot are not "people you talked to"
            person = uinfo.get("real_name") or uinfo.get("name") or im.get("user")
            msgs = _paginated("conversations.history", token, "messages",
                              channel=im["id"], oldest=str(oldest), limit=200)
            times = [float(m["ts"]) for m in msgs if _is_human(m)]
            for cs, ce, n in cluster(times, gap):
                if n < 2:
                    continue  # a single message isn't a conversation
                start = int(cs)
                end = max(int(ce), start + MIN_WINDOW_SEC)
                out.append(Meeting(
                    ext_id=f"{im['id']}:{start}", ts_start=start, ts_end=end,
                    person=person, title=f"Slack DM ({n} msgs)",
                    tag="slack", source="slack",
                ))
        except RuntimeError as e:  # one bad DM must not abort the whole run
            print(f"[slack] {im.get('id')} skipped: {e}")
            continue
    return out
