"""Whoop RESMI API v2 (OAuth2 authorization-code) istemcisi.

Verdigi gunluk BAGLAM: recovery (0-100), HRV (rmssd ms), dinlenme nabzi, gun strain'i,
uyku performansi + WORKOUT pencereleri (analiz'de aktivite dislama icin).
Resmi API "Stres Monitoru" skorunu VERMEZ; stres yine dakikalik HR proxy'sinden gelir.

Kurulum: developer.whoop.com'da uygulama olustur (redirect URI: WHOOP_REDIRECT_URI),
Client ID/Secret'i .env'e yaz, sonra bir kez:  python whoop_oauth.py login
"""
import secrets
import time
import urllib.parse
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

import config
import db
import secrets_store
import tzutil

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE = "https://api.prod.whoop.com/developer"
SCOPES = "offline read:recovery read:cycles read:sleep read:workout read:profile"
_TIMEOUT = 30


# ---------------- token store ----------------
_TOKEN_KEY = "whoop_oauth_tokens"


def _load_tokens():
    # OS keyring tercih edilir; eski whoop_tokens.json otomatik migrate edilir
    return secrets_store.get_blob(_TOKEN_KEY, config.TOKEN_STORE_PATH)


def _save_tokens(tok):
    tok = {**tok, "obtained_at": tzutil.now_ts()}
    secrets_store.set_blob(_TOKEN_KEY, tok, config.TOKEN_STORE_PATH)


def _store_from_response(resp_json):
    _save_tokens({
        "access_token": resp_json["access_token"],
        "refresh_token": resp_json.get("refresh_token"),
        "expires_at": tzutil.now_ts() + int(resp_json.get("expires_in", 3600)),
        "scope": resp_json.get("scope"),
    })


# ---------------- one-time login ----------------
def login():
    config.require_oauth()
    state = secrets.token_urlsafe(16)  # >= 8 char (Whoop kurali)
    parsed = urllib.parse.urlparse(config.WHOOP_REDIRECT_URI)
    port = parsed.port or 8080

    params = {
        "response_type": "code",
        "client_id": config.WHOOP_CLIENT_ID,
        "redirect_uri": config.WHOOP_REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    holder = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = q.get("code", [None])[0]
            if code:  # favicon vb. kodsuz istekler code'u ezmesin
                holder["code"] = code
                holder["state"] = q.get("state", [None])[0]
                body = "<h2>Whoop baglandi. Bu sekmeyi kapatabilirsin.</h2>"
            else:
                body = "<h2>Bekleniyor…</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    print(f"[oauth] Tarayici aciliyor. Acilmazsa su URL'i ac:\n{url}\n")
    webbrowser.open(url)
    server = HTTPServer(("localhost", port), Handler)
    while not holder.get("code"):  # code gelene kadar istekleri isle (kodsuzu yut)
        server.handle_request()
    server.server_close()

    if not holder.get("code"):
        raise RuntimeError("Yetkilendirme kodu alinamadi.")
    if holder.get("state") != state:
        raise RuntimeError("State uyusmuyor (CSRF); tekrar dene.")

    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": holder["code"],
        "client_id": config.WHOOP_CLIENT_ID,
        "client_secret": config.WHOOP_CLIENT_SECRET,
        "redirect_uri": config.WHOOP_REDIRECT_URI,
    }, timeout=_TIMEOUT)
    resp.raise_for_status()
    _store_from_response(resp.json())
    print("[oauth] Token kaydedildi ->", config.TOKEN_STORE_PATH)


def _refresh(tok):
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
        "client_id": config.WHOOP_CLIENT_ID,
        "client_secret": config.WHOOP_CLIENT_SECRET,
        "scope": "offline",  # refresh token'i almaya devam et (rotasyon)
    }, timeout=_TIMEOUT)
    if resp.status_code == 400 and "invalid_grant" in resp.text:
        # Rotasyonlu tek-kullanimlik refresh token gecersiz kalmis (kayip yanit/es zamanli sync)
        raise RuntimeError(
            "Refresh token gecersiz. Tekrar yetkilendir: python whoop_oauth.py login"
        )
    resp.raise_for_status()
    _store_from_response(resp.json())  # YENI refresh token'i hemen sakla
    return _load_tokens()


def _access_token():
    tok = _load_tokens()
    if not tok:
        raise RuntimeError("Token yok. Once 'python whoop_oauth.py login' calistir.")
    if tzutil.now_ts() >= tok["expires_at"] - 60:  # 60sn tampon
        if not tok.get("refresh_token"):
            raise RuntimeError("Refresh token yok; tekrar login gerek (offline scope?).")
        tok = _refresh(tok)
    return tok["access_token"]


# ---------------- REST ----------------
def _iso(ts):
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _iso_to_epoch(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _reset_seconds(header_val):
    """X-RateLimit-Reset saniye-cinsinden-bekleme mi epoch-timestamp mi olabilir."""
    try:
        v = int(header_val)
    except (TypeError, ValueError):
        return 5
    now = tzutil.now_ts()
    return (v - now) if v > now + 300 else v  # epoch ise farki al


def _get(path, params=None):
    """Sinirli denemeli GET: 401'de bir kez refresh, 429'da bekle; sonsuz recursion yok."""
    refreshed = False
    for attempt in range(4):
        token = _access_token()
        r = requests.get(API_BASE + path, params=params or {},
                         headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
        if r.status_code == 401 and not refreshed:
            _refresh(_load_tokens())
            refreshed = True
            continue
        if r.status_code == 429:
            wait = max(1, min(_reset_seconds(r.headers.get("X-RateLimit-Reset")), 60))
            time.sleep(wait)
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"WHOOP API {path}: 4 denemede basarisiz (rate-limit/401).")


def _first_record(resp):
    """Tek-kayit uc noktasi bazen {records:[...]} donebilir; ilk kaydi normalize et."""
    if not resp:
        return None
    if isinstance(resp, dict) and "records" in resp:
        recs = resp.get("records") or []
        return recs[0] if recs else None
    return resp


def _paginate(path, start_ts, end_ts):
    params = {"start": _iso(start_ts), "end": _iso(end_ts), "limit": 25}
    while True:
        data = _get(path, params)
        if not data:
            return
        yield from data.get("records", [])
        nxt = data.get("next_token")
        if not nxt:
            return
        params["nextToken"] = nxt


def _scored(rec):
    return rec.get("score_state") == "SCORED" and isinstance(rec.get("score"), dict)


# ---------------- gunluk sync ----------------
def sync_daily(days=None):
    config.require_oauth()
    db.init_db()
    days = days or config.SYNC_DAYS
    now = tzutil.now_ts()
    start = now - days * 86400

    # Workout pencereleri (aktivite dislama)
    n_wo = 0
    for w in _paginate("/v2/activity/workout", start, now):
        ws, we = _iso_to_epoch(w.get("start")), _iso_to_epoch(w.get("end"))
        if ws and we:
            strain = w["score"].get("strain") if _scored(w) else None
            db.upsert_workout(w["id"], ws, we, w.get("sport_name"), strain)
            n_wo += 1

    # Cycle-merkezli: gun strain + o gune bagli recovery + uyku
    n_days = 0
    for cyc in _paginate("/v2/cycle", start, now):
        cyc_start = _iso_to_epoch(cyc.get("start"))
        if not cyc_start:
            continue
        day = tzutil.fmt(cyc_start, "%Y-%m-%d")
        strain = cyc["score"].get("strain") if _scored(cyc) else None

        rec = _first_record(_get(f"/v2/cycle/{cyc['id']}/recovery"))
        recovery = hrv = rhr = None
        if rec and _scored(rec):
            s = rec["score"]
            recovery = s.get("recovery_score")
            hrv = s.get("hrv_rmssd_milli")
            rhr = s.get("resting_heart_rate")

        slp = _first_record(_get(f"/v2/cycle/{cyc['id']}/sleep"))
        sleep_perf = slp["score"].get("sleep_performance_percentage") if (slp and _scored(slp)) else None

        db.upsert_daily(day, recovery=recovery, hrv=hrv, rhr=rhr,
                        strain=strain, sleep_perf=sleep_perf, updated_at=now)
        n_days += 1

    print(f"[oauth] {n_days} gun + {n_wo} workout senkronize edildi.")
    return {"days": n_days, "workouts": n_wo}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "login"
    if cmd == "login":
        login()
    elif cmd == "sync":
        sync_daily()
    else:
        print("Kullanim: python whoop_oauth.py [login|sync]")
