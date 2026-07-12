# Automatic context sources (optional)

Instead of tapping a button for every meeting, let the tool figure out **who you were
with** from your calendar and Slack, and correlate those windows with your WHOOP data
automatically.

Enable sources with a comma-separated list in `.env`:

```env
AUTO_SOURCES=google_calendar,slack
```

Then run `python auto_sync.py` (or let `sync.py` / PM2 do it on a schedule). Detected
meetings are written into the same `events` table the manual logger uses, de-duplicated
by `(source, ext_id)`, so re-runs never create duplicates and the stress analysis works
unchanged.

Install the extra dependencies once:

```bash
pip install -r requirements-auto.txt
```

---

## 🗓 Google Calendar (recommended — the strongest signal)

A timed calendar event is an ideal "who you were with" record: a real start/end window
plus the other attendees. All-day events, solo blocks (no other attendees), and events
you declined are skipped automatically. The person is the other attendee(s); the title
becomes the topic; the context tag is `work`.

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project and
   enable the **Google Calendar API**.
2. Create an **OAuth client ID** of type **Desktop app**, download the JSON, and save it
   as `google_credentials.json` (or point `GOOGLE_CREDENTIALS_PATH` at it).
3. Set in `.env`:
   ```env
   AUTO_SOURCES=google_calendar
   GOOGLE_CREDENTIALS_PATH=google_credentials.json
   GOOGLE_CALENDAR_ID=primary
   ```
4. First run opens a browser to authorize (scope: `calendar.readonly`); the token is
   cached in `google_token.json` (git-ignored).

```bash
python auto_sync.py
```

## 💬 Slack (experimental — a weak signal)

> Text chat maps poorly onto a physiological stress window — you aren't necessarily tense
> while typing. Treat Slack as a **weak, experimental** signal. It clusters direct-message
> activity into "conversation windows": messages with one person spaced less than
> `SLACK_GAP_MIN` minutes apart become a single event, attributed to that person with the
> `slack` tag.

1. Create a Slack app at <https://api.slack.com/apps>.
2. Add a **User OAuth Token** (`xoxp-...`) with scopes: `im:history`, `im:read`,
   `users:read`. Install it to your workspace.
3. Set in `.env`:
   ```env
   AUTO_SOURCES=google_calendar,slack
   SLACK_TOKEN=xoxp-...
   SLACK_GAP_MIN=20
   ```

## Privacy

- `google_credentials.json`, `google_token.json`, and your Slack token live only in
  git-ignored files / your local `.env`.
- Everything is stored locally in SQLite; nothing is uploaded anywhere.
- The tool only ever **reads** your own calendar and DMs.

## Adding your own source

Drop a module in `sources/` exposing `fetch(days) -> list[Meeting]`, register it in
`sources/load()`, and add its name to `AUTO_SOURCES`. See `sources/google_calendar.py`
for a minimal example.
