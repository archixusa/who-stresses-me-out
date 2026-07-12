# Privacy

`who-stresses-me-out` is a **self-hosted, local-only** personal analytics tool. There is no server,
no account, and no telemetry. This document explains exactly what is stored, what is deliberately
**not** collected, and how you stay in control of your data.

> **In one line:** the only body in this dataset is yours, the only machine it lives on is yours,
> and the app never asks how you feel.

## What is stored (local SQLite only)

Everything is kept in a single SQLite file on your machine (`whoop_stress.db` by default; change it
with `DB_PATH`). Nothing is uploaded anywhere.

| Data | Where | Notes |
| --- | --- | --- |
| Meetings you log | `events` table | window, primary person, location, topic, context `tag`, source |
| Participants of each meeting | `event_participants` table | supports multi-participant / group events |
| Optional confounder flags | `events` columns | `caffeine`, `alcohol`, `illness`, `commute`, free-text `notes` |
| Minute-level heart rate | `hr_cache` table | `(timestamp, bpm)` — only if you enable the unofficial source |
| Day-level WHOOP context | `daily` table | recovery, HRV, resting HR, strain, sleep performance |
| Workout windows | `workouts` table | used to exclude exercise minutes from the analysis |
| Your quick buttons | `shortcuts` table | `person · context` shortcuts |
| Autocomplete suggestions | `friends`, `locations` | names you've typed before, for convenience |

## What is NOT collected

This is a design decision, not an oversight:

- **No mood, feeling, tension, emotion, or survey input of any kind.** There is no "rate how you
  felt" screen, no 1–5 scale, no journaling prompt — anywhere in the bot. The `⚙️ Extra context`
  screen offers only physiological confounders (caffeine/alcohol/illness/commute) and is explicitly
  labelled *"no mood questions"*.
- **No data about other people.** Participants are **never contacted, messaged, or measured.** The
  app only ever reads **your own** WHOOP account. A person's name is just a label you attach to your
  own heart-rate window.
- **No location tracking, no GPS, no contacts import.** "Location" is only the free-text place label
  you optionally type.
- **A legacy `feeling` column exists but is dead.** Old databases may still contain a `feeling`
  value from a previous version. The reshaped code **never writes and never reads it**. It is
  preserved only so old databases keep opening. See [DATA_LIFECYCLE.md](DATA_LIFECYCLE.md).

## Use aliases

You are strongly encouraged to use **aliases** for person names — `Alex`, `Sam`, `Jordan`, or role
labels like `coach`, `ex`, `landlord`. The analysis is identical on aliases, and it keeps your
database and any export meaningful only to you. The bot reminds you of this in **Settings**.

## Token storage

The tool touches two kinds of WHOOP credentials, and handles each conservatively:

| Credential | Preferred storage | Fallback |
| --- | --- | --- |
| **Official OAuth tokens** (access + refresh) | Your **operating-system keyring** via the [`keyring`](https://pypi.org/project/keyring/) library, when available | A git-ignored JSON file (`whoop_tokens.json`, path set by `TOKEN_STORE_PATH`), written with restrictive permissions and a warning |
| **Unofficial minute-level HR token** | An **environment variable** (never committed) | A `whoop_token.txt` file — **delete it after the backfill run** |

Notes:

- The OAuth **client secret** stays in your `.env` and is never logged.
- `whoop_tokens.json`, `whoop_token.txt`, `.env`, `google_credentials.json`, and `google_token.json`
  are all listed in `.gitignore` — never commit them.
- The unofficial HR token expires roughly hourly; refresh it from the browser when needed, and
  remove the fallback file once the backfill is done.
- Revoke official access anytime from the WHOOP app or via `DELETE /v2/user/access`.

## Export and delete — you are in control

| Action | Command / control | Behaviour |
| --- | --- | --- |
| See what's stored | `/mydata` | Summary counts and the heart-rate date range |
| Export a backup | `/export` (or `/export csv`) | Writes a JSON/CSV file locally and sends it to your own chat |
| Delete one meeting | `🕘 Recent` → 🗑 | Confirm, delete, and a brief **Undo** |
| Delete everything | `/deletemydata` **or** `⚙️ Settings → Delete all data` | **Two-step confirmation**, then a full local wipe |

### Exports contain personal data

An export is a plain JSON or CSV file containing your **person names, notes, and heart-rate data**.
Both formats begin with an explicit warning line. Treat an export like any other sensitive personal
file: keep it private, and delete it when you no longer need it. Exports are written next to the
database and are git-ignored.

## Threat model, briefly

- **What protects your data:** it never leaves your machine, and secrets are kept out of git.
- **What you are responsible for:** the security of the machine running the bot, your `.env` file,
  any exports you generate, and your Telegram account (only your `TELEGRAM_CHAT_ID` may interact
  with the bot, but Telegram itself is a third party in transit).

## See also

- [DATA_LIFECYCLE.md](DATA_LIFECYCLE.md) — how data enters, lives, and leaves.
- [ANALYSIS_METHOD.md](ANALYSIS_METHOD.md) — how the association analysis works.
- [WHOOP_OAUTH_SETUP.md](WHOOP_OAUTH_SETUP.md) · [AUTO_SOURCES_SETUP.md](AUTO_SOURCES_SETUP.md).
