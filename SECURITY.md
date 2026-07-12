# Security Policy

This project handles personal health data and API credentials, so security matters even for
a small tool.

## Supported versions

This is an actively developed personal project; only the latest `main` is supported. Please
make sure you're on the newest commit before reporting an issue.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Instead, use GitHub's private vulnerability reporting:

1. Go to the repository's **Security** tab → **Report a vulnerability**, or
2. Open a [private security advisory](https://github.com/archixusa/who-stresses-me-out/security/advisories/new).

Please include steps to reproduce and the potential impact. You can expect an initial
response within a few days.

## Handling of secrets & data

- All credentials live only in **git-ignored** files: `.env`, `whoop_tokens.json`,
  `whoop_token.txt`, `google_credentials.json`, `google_token.json`.
- All personal data (heart rate, meetings, WHOOP metrics) is stored **locally** in SQLite and
  is never uploaded anywhere by this tool.
- Secrets are never written to logs, and only your own account data is ever read.
- If you fork or share the repo, verify with `git status` and a secret scan that no `.env`,
  `*.db`, or `*_token*.json` file is tracked.

## Scope note

The optional minute-level heart-rate path uses WHOOP's undocumented internal API. That is a
gray area under WHOOP's Terms of Service and is provided for personal, read-only use only.
