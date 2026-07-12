# WHOOP Official OAuth Setup (optional)

The official WHOOP API is **not required** for the stress proxy — but it adds valuable
daily context: recovery (0–100), HRV, resting heart rate, day strain, sleep performance,
and **workout windows** (used to exclude exercise minutes from the stress proxy).

> The official WHOOP API does **not** expose the in-app "Stress Monitor" score, so stress
> is always derived from heart-rate. See the main README for details.

## 1. Create an app (self-service, instant)

1. Go to <https://developer.whoop.com> → sign in → Developer Dashboard.
2. **Create App** and give it a name.
3. **Redirect URI**: add `http://localhost:8080/callback` (must exactly match
   `WHOOP_REDIRECT_URI` in your `.env`).
4. **Scopes**: enable `read:recovery`, `read:cycles`, `read:sleep`, `read:workout`,
   `read:profile`, and `offline` (`offline` is required to receive a refresh token).
5. Save → your **Client ID** and **Client Secret** are issued immediately.

> A development-mode app supports up to 10 users without approval — fine for personal use.

## 2. Fill `.env`

```env
WHOOP_CLIENT_ID=...
WHOOP_CLIENT_SECRET=...
WHOOP_REDIRECT_URI=http://localhost:8080/callback
```

## 3. Authorize once

```bash
python whoop_oauth.py login
```

A browser opens; approve access with your WHOOP account. Tokens are written to
`whoop_tokens.json` (git-ignored). The access token lives ~1 hour; the refresh token is
rotated automatically.

## 4. Pull context

```bash
python whoop_oauth.py sync      # daily sync.py also calls this
```

Then `/gun` in Telegram shows today's recovery/strain/sleep, and the stress analysis
automatically excludes workout minutes.

## Security

- `whoop_tokens.json` and `.env` are git-ignored — never commit them.
- The client secret stays server-side and is never logged.
- Revoke access anytime from the WHOOP app settings or via `DELETE /v2/user/access`.
