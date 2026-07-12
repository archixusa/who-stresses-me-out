"""Gunluk sync: (1) gayriresmi dakikalik HR -> hr_cache, (2) resmi API baglam
(recovery/strain/uyku + workout) -> daily/workouts. pm2 cron / n8n ile tetikle."""
from datetime import UTC, datetime, timedelta

import config
import db
import hr_provider


def sync_hr(days=None, debug=False):
    if not hr_provider.enabled():
        print("[sync] HR_PROVIDER=none — dakikalik HR atlandi.")
        return 0
    days = days or config.SYNC_DAYS
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days)
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    print(f"[sync] HR {start_s} -> {end_s} (7-gun chunk'lar)...")
    samples = hr_provider.fetch_hr(start_s, end_s, step=60, debug=debug)
    if not samples:
        print("[sync] UYARI: hic HR ornegi gelmedi. Kimligi ve hr_sample_debug.json'i kontrol et.")
        return 0
    db.upsert_hr(samples)
    print(f"[sync] {len(samples)} HR ornegi kaydedildi (toplam cache: {db.hr_count()}).")
    return len(samples)


def sync_official(days=None):
    """OAuth ayarliysa resmi baglami ceker; degilse sessizce atlar."""
    if not (config.WHOOP_CLIENT_ID and config.WHOOP_CLIENT_SECRET):
        print("[sync] Resmi API ayarli degil (OAuth atlandi).")
        return None
    try:
        import whoop_oauth
        return whoop_oauth.sync_daily(days)
    except Exception as e:  # baglam opsiyonel; HR sync'i bozmasin
        print(f"[sync] Resmi API sync hatasi (atlaniyor): {e}")
        return None


def sync_auto(days=None):
    """Otomatik baglam kaynaklari (takvim/Slack) ayarliysa cek; degilse atla."""
    if not config.AUTO_SOURCES:
        return None
    try:
        import auto_sync
        return auto_sync.run(days)
    except Exception as e:  # otomatik kaynak HR/resmi sync'i cokertmesin
        print(f"[sync] Auto-source sync hatasi (atlaniyor): {e}")
        return None


def run(days=None, debug=False):
    db.init_db()
    try:
        hr = sync_hr(days, debug)
    except Exception as e:  # gayriresmi kaynak kapaliysa resmi sync'i cokertmesin
        print(f"[sync] HR sync atlandi (gayriresmi kaynak kapali olabilir): {e}")
        hr = 0
    official = sync_official(days)
    auto = sync_auto(days)
    return {"hr_samples": hr, "official": official, "auto": auto}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    run(debug="--debug" in sys.argv)
