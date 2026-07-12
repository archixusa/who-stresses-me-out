"""Pull events from the enabled automatic sources (config.AUTO_SOURCES) into the events
table. De-duplicated by (source, ext_id), so it is safe to run repeatedly. Called by
sync.py; schedule it with PM2 / cron for hands-free context.
"""
import config
import db
import sources
import tzutil


def run(days=None):
    names = [s.strip() for s in (config.AUTO_SOURCES or "").split(",") if s.strip()]
    if not names:
        print("[auto] AUTO_SOURCES is empty — no automatic sources configured.")
        return {}

    db.init_db()
    span_days = days or config.SYNC_DAYS
    now = tzutil.now_ts()
    window_start = now - span_days * 86400
    summary = {}

    for name in names:
        try:
            meetings = sources.load(name)(days)
        except Exception as e:  # one bad source must not break the others
            print(f"[auto] {name} skipped: {e}")
            summary[name] = {"error": str(e)}
            continue

        # Kararsiz-sinirli kaynaklarda (or. Slack) pencereyi sil-ve-yeniden-yaz
        if sources.mode(name) == "replace_window":
            db.delete_external_window(name, window_start, now)

        inserted = updated = failed = 0
        for m in meetings:
            try:
                result = db.upsert_external_event(
                    source=m.source, ext_id=m.ext_id, ts_start=m.ts_start, ts_end=m.ts_end,
                    friend=m.person, topic=m.title, tag=m.tag,
                )
                inserted += result == "inserted"
                updated += result == "updated"
            except Exception as e:  # tek yazma hatasi tum grubu iptal etmesin
                failed += 1
                print(f"[auto] {name} write failed for {m.ext_id}: {e}")
        print(f"[auto] {name}: {inserted} new, {updated} updated, {failed} failed")
        summary[name] = {"inserted": inserted, "updated": updated, "failed": failed}
    return summary


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    run()
