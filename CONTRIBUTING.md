# Contributing

Thanks for your interest in **who-stresses-me-out**! Contributions of all kinds are
welcome — bug reports, fixes, new data sources, docs, and ideas.

## Getting started

```bash
git clone https://github.com/archixusa/who-stresses-me-out.git
cd who-stresses-me-out
pip install -r requirements.txt
cp .env.example .env          # fill in your own values
python _smoketest.py          # end-to-end test on synthetic data (no live accounts needed)
```

Optional extras: `pip install -r requirements-auto.txt` for the calendar/Slack sources.

## Development guidelines

- **Run the tests.** `python _smoketest.py` must pass before you open a PR. It exercises the
  analysis engine, the Telegram helpers, de-duplication, and the source mappers on synthetic
  data — no real WHOOP/Telegram/Google/Slack account required.
- **Keep pure logic testable.** Mapping/parsing functions (e.g. `event_to_meeting`,
  `cluster`, the analysis helpers) are written as pure functions so they can be unit-tested
  without network access. Follow that pattern and add assertions to `_smoketest.py`.
- **Never commit secrets.** `.env`, `*.db`, and the various `*_token*.json` files are
  git-ignored. Double-check `git status` before committing.
- **Style.** Small, focused modules; standard-library first; clear names. Match the
  surrounding code.

## Adding a new automatic source

Automatic-context sources live in `sources/`. To add one:

1. Create `sources/your_source.py` exposing `fetch(days) -> list[Meeting]` (see
   `sources/google_calendar.py` for a minimal example).
2. If its ids depend on recomputed boundaries (like Slack clustering), set
   `MODE = "replace_window"`; otherwise it defaults to stable `(source, ext_id)` de-dup.
3. Register it in `sources.load()` / `sources.mode()` and document it in
   `docs/AUTO_SOURCES_SETUP.md`.

## Pull requests

- Branch from `main`, keep the change focused, and describe **what** and **why**.
- Reference any related issue.
- Make sure `_smoketest.py` passes and no secrets are staged.

## A note on the unofficial WHOOP path

Minute-level heart rate uses WHOOP's undocumented internal API (a gray area under WHOOP's
Terms). Please keep contributions to that path **read-only and personal-use** in spirit, and
prefer the official API where possible. See the README's *Data sources* section.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
