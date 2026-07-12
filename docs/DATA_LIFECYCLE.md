# Data lifecycle

Where your data comes from, where it lives, how long it stays, and how to get it out or delete it.
This tool is **local-only**: there is no server, no account, and nothing is uploaded. See
[PRIVACY.md](PRIVACY.md) for the privacy stance and [ANALYSIS_METHOD.md](ANALYSIS_METHOD.md) for how
the data is analysed.

## 1. How data enters

| Path | How | Lands in |
| --- | --- | --- |
| **Manual — buttons** | Tap a `person · context` shortcut on the persistent menu; a live meeting card opens; tap **⏹ Stop** to close it | `events` + `event_participants` |
| **Manual — `/meet`** | Step-by-step flow: person → place → context (no feeling asked) | `events` + `event_participants` |
| **Manual — `/log`** | `/log Person \| Place \| Context` one-liner | `events` + `event_participants` |
| **Extra context** | The `⚙️ Extra context` card screen toggles caffeine / alcohol / illness / commute and a free-text note — **no mood questions** | `events` confounder columns |
| **Google Calendar** | `auto_sync` turns timed events into meetings (window + attendees) | `events` (`source='google_calendar'`) |
| **Slack** | `auto_sync` clusters direct-message activity into conversation windows (experimental) | `events` (`source='slack'`) |
| **WHOOP official API** | `sync.py` / `whoop_oauth.py` pulls day-level context + workout windows | `daily`, `workouts` |
| **WHOOP minute HR** | Optional unofficial source backfills minute-level heart rate | `hr_cache` |

Automatic events are **de-duplicated by `(source, ext_id)`**, so re-running a sync never creates
duplicates. Sources with unstable boundaries (e.g. Slack) use a *replace-window* strategy: the window
is deleted and re-written each run so shifting cluster boundaries can't pile up duplicates.

## 2. Where it lives

Everything is in a single local **SQLite** database (`whoop_stress.db` by default; set `DB_PATH` to
move it). Uses write-ahead logging so the bot, sync, and report can run concurrently.

| Table | Contents |
| --- | --- |
| `events` | meetings: window, primary person, location, topic, `tag`, `source`, `ext_id`, confounders (`caffeine`, `alcohol`, `illness`, `commute`, `notes`) |
| `event_participants` | every participant of an event (primary + others) |
| `hr_cache` | minute-level heart-rate samples `(ts, bpm)` |
| `daily` | day-level official context: `recovery`, `hrv`, `rhr`, `strain`, `sleep_perf` |
| `workouts` | official workout windows (used to exclude exercise minutes) |
| `shortcuts` | your `person · context` quick buttons |
| `friends` / `locations` | autocomplete suggestions |
| `meta` | migration & seed bookkeeping |

## 3. Retention

Data is **kept until you delete it**. There is no automatic expiry and no background purging. The
only automatic maintenance is housekeeping on *open* meetings: when you start a new meeting, any
still-open ones are closed (recent ones at "now", very old ones at a default window length) so
orphaned open events don't accumulate. No data is discarded in the process.

## 4. Export formats

Trigger with `/export` (JSON, default) or `/export csv`, or from **📊 Reports** / **⚙️ Settings**.

- **JSON** — an object with a `_warning` field, a `summary` of counts, and the full `events` list
  (participants joined per event).
- **CSV** — a leading `#` warning comment, then one row per event with participants joined by `; `.

Both files are written **next to the database**, are **git-ignored**, and are sent to your own
Telegram chat with a caption warning.

> ⚠️ **Exports contain personal data** — names, notes, and heart-rate data. Keep them private and
> delete them when you no longer need them.

## 5. Deletion

| Scope | How | Safety |
| --- | --- | --- |
| **One event** | `🕘 Recent` → 🗑 on a row, or `/delete <id>` | Confirm step, then a brief **Undo** that restores the event (with its participants and confounders) |
| **Everything** | `/deletemydata`, or `⚙️ Settings → Delete all data` | **Two-step confirmation** before a full wipe |

A full wipe clears `events`, `event_participants`, `hr_cache`, `daily`, `workouts`, and `shortcuts`.
The `meta` seed markers are intentionally preserved, so the default starter shortcuts do **not**
reappear after you've deleted them.

Token files and credentials are **not** part of the app database — remove those separately (delete
`whoop_tokens.json` / `whoop_token.txt`, clear the relevant `.env` entries, or revoke access from the
provider). See [PRIVACY.md](PRIVACY.md#token-storage).

## 6. The legacy `feeling` field

Older databases (from before the privacy-first reshape) may contain a `feeling` value in `events`.
Its handling is deliberate:

- **Preserved:** the column is not dropped, so old databases keep opening and no historical row is
  destroyed.
- **Unused:** the reshaped code **never writes and never reads** it. New events are inserted without
  it, and the analysis and report never surface it. The end-to-end smoke test asserts that no
  feeling/mood language can appear in the report.

In effect, `feeling` is inert legacy baggage — retained for backward compatibility, invisible to
everything the tool does today.
