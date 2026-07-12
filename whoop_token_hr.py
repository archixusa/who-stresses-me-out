"""Jeton ile dakikalik HR backfill (gayriresmi dahili API — GRI ALAN).

Whoop'un dahili metrics-service'inden 1 dakikalik nabzi ceker; sifre-login kapali
oldugu icin taze bir erisim jetonu (access token) ile calisir.

Kullanim:
  1. Whoop web'e (app.whoop.com) girmisken tarayicida DevTools ac (F12) > Network sekmesi.
  2. 'api.prod.whoop.com' giden herhangi bir istege tikla > Headers > Request Headers >
     'authorization: Bearer <UZUN_TOKEN>' satirindaki <UZUN_TOKEN>'i kopyala.
  3. whoop_token.txt dosyasina yapistir (tek satir, "Bearer " olmadan sadece token).
  4. python whoop_token_hr.py            (veya gun sayisi: python whoop_token_hr.py 14)

NOT: Jeton ~1 saatte dolar; her backfill oncesi tazele. Bu dahili API belgesizdir
(ToS gri alan). Backfill sonrasi rapor, bulusma penceresine dusen HR eslesirse
otomatik dakikalik moda gecer.
"""
import os
import sys
from datetime import UTC, datetime, timedelta

import config
import db
import whoop_source

TOKEN_FILE = "whoop_token.txt"


def _client_with_token(token):
    """whoop-data WhoopClient'i sifre-login yapmadan, verilen jetonla kurar."""
    from whoop_data.client import WhoopClient
    c = WhoopClient.__new__(WhoopClient)   # __init__'i (sifre auth) atla
    c.username = None
    c.password = None
    c.access_token = token.strip()
    c.refresh_token = None
    c.userid = None
    c.api_version = "7"
    c._get_user_id()   # jetonu dogrular + userid ceker (gecersizse Exception)
    return c


def _clean_token(tok):
    tok = (tok or "").strip()
    return tok[7:].strip() if tok.lower().startswith("bearer ") else tok


def _read_token():
    # 1) ONCE ortam degiskeni (tercih edilen)
    if config.WHOOP_ACCESS_TOKEN:
        return _clean_token(config.WHOOP_ACCESS_TOKEN)
    # 2) whoop_token.txt (dosya fallback) — kullanimdan sonra silinmesi onerilir
    if os.path.exists(TOKEN_FILE):
        print(f"[hr] UYARI: jeton {TOKEN_FILE} dosyasindan okundu (duz metin). "
              f"WHOOP_ACCESS_TOKEN ortam degiskenini tercih et ve bu dosyayi kullanimdan sonra sil.")
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return _clean_token(f.read())
    return None


def run(days=None):
    from whoop_data import get_heart_rate_data
    days = days or config.SYNC_DAYS
    token = _read_token()
    if not token:
        print(f"Jeton yok. {TOKEN_FILE} dosyasina Bearer token'i yapistir (bkz. dosya basligi).")
        return 0

    db.init_db()
    try:
        client = _client_with_token(token)
    except Exception as e:
        print(f"Jeton gecersiz veya dolmus olabilir — tarayicidan tazele: {e}")
        return 0

    end = datetime.now(UTC).date()
    start = end - timedelta(days=days)
    total = 0
    for s, e in whoop_source.tile_windows(
        start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), config.HR_WINDOW_DAYS
    ):
        try:
            raw = get_heart_rate_data(client=client, start_date=s, end_date=e, step=60)
        except Exception as ex:
            print(f"  {s}..{e}: HATA (jeton dolmus olabilir) {ex}")
            break
        samples = whoop_source._normalize(raw)
        if samples:
            db.upsert_hr(samples)
            total += len(samples)
        print(f"  {s}..{e}: {len(samples)} ornek")

    print(f"[hr] toplam {total} dakikalik ornek cache'lendi (hr_cache: {db.hr_count()}).")
    if total:
        print("Rapor artik dakikalik modda calisir: Telegram'da /rapor.")
    return total


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    d = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
    run(d)
