# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Privacy-first, **non-causal** reshape. The tool moved from a "who stresses you out" ranker toward a
private personal-analytics tool that reports **associations between contexts and a heart-rate
signal** — never causal claims, and with **no mood data collected**.

### Removed

- **All emotion / feeling collection.** No feeling, tension, mood, or survey input anywhere in the
  bot. The step-by-step flow and the meeting card no longer ask how you felt; the `⚙️ Extra context`
  screen is explicitly labelled *"no mood questions"*.
- The subjective-vs-measured "agreement" reporting that depended on feeling input.
- The static, unverifiable "tests passing" badge (replaced by a real CI status badge).

### Added

- **Evidence levels** on every ranking — `insufficient` → `weak` → `emerging` → `consistent` — so
  low-evidence contexts are never presented as findings.
- **Bootstrap confidence intervals** (deterministic, fixed-seed percentile bootstrap of the mean) on
  each grouped result.
- **Matched controls** — same-weekday, same-time-of-day, non-meeting heart rate as a cross-check
  against time-of-day artefacts.
- **Confounder flags** — optional per-meeting caffeine / alcohol / illness / commute toggles plus a
  free-text note; heavily confounded contexts are downweighted and capped at *weak*.
- **Multi-participant (group) events** with explicit *limited per-person attribution*.
- **Supportive contexts** section for contexts associated with a **lower** heart-rate response.
- **Weekly experiments** — small, concrete data-improvement suggestions (never health advice).
- **Button-first Telegram UX** — persistent menu (➕ New meeting, ⏹ Stop, 📊 Reports, 📅 Today,
  🕘 Recent, ⚙️ Settings) and a live, editable meeting card. Text commands retained for backward
  compatibility.
- **Keyring token storage** — official OAuth tokens preferred in the OS keyring via the `keyring`
  library, with a git-ignored JSON-file fallback; environment variable preferred for the unofficial
  HR token.
- **Export / delete / privacy commands** — `/mydata`, `/export` (JSON/CSV), `/deletemydata`, plus
  per-event delete with **undo** and a two-step **Delete all data** wipe.
- **`pytest` unit-test suite and GitHub Actions CI**, alongside the existing `_smoketest.py`
  end-to-end check.
- New documentation: `docs/PRIVACY.md`, `docs/ANALYSIS_METHOD.md`, `docs/DATA_LIFECYCLE.md`, and this
  changelog.

### Changed

- **Non-causal, association-only framing throughout.** Reports and docs use *"associated with a
  higher/lower heart-rate response"* instead of *"X stresses you out"*.
- Reworked the report into an evidence-first **association report** with sync-health, data-quality,
  and experiment sections.
- README rewritten around the new product definition, with a "how to read the results" and a
  "what this tool cannot tell you" section.

### Deprecated

- `events.feeling` is now a **legacy** column: preserved so old databases keep opening, but never
  written or read by current code.

## [0.1.0] — Initial release

- Telegram logger for `person · context` meetings correlated with WHOOP data.
- Minute-level heart-rate proxy (pre-meeting baseline, arrival trim, workout exclusion, robust
  median, small-sample shrinkage) plus a day-level recovery/HRV/RHR signal.
- Official WHOOP API v2 (OAuth2) day-level sync and optional unofficial minute-level heart rate.
- Optional automatic context sources: Google Calendar and Slack.
