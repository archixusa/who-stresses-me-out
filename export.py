"""Local data export (JSON / CSV). Files are written next to the DB and are git-ignored.

Everything stays on the user's machine; export is only for the owner's own backup.
Exports carry a plaintext warning that they contain personal data.
"""
import csv
import io
import json
import os
import time

import config
import db

_FIELDS = ["id", "ts_start", "ts_end", "participants", "location", "topic", "tag",
           "caffeine", "alcohol", "illness", "commute", "notes", "source", "created_at"]


def _rows():
    events = db.export_events()
    out = []
    for e in events:
        row = {k: e.get(k) for k in _FIELDS}
        row["participants"] = "; ".join(e.get("participants") or [])
        out.append(row)
    return out


def to_json():
    return json.dumps({
        "_warning": "Contains personal names, notes and heart-rate data. Keep private.",
        "summary": db.data_summary(),
        "events": _rows(),
    }, indent=2, ensure_ascii=False)


def to_csv():
    buf = io.StringIO()
    buf.write("# who-stresses-me-out export — contains personal data, keep private\n")
    w = csv.DictWriter(buf, fieldnames=_FIELDS)
    w.writeheader()
    for r in _rows():
        w.writerow(r)
    return buf.getvalue()


def write_export(fmt="json"):
    """Writes the export to a file and returns its path."""
    fmt = "csv" if str(fmt).lower().startswith("c") else "json"
    content = to_csv() if fmt == "csv" else to_json()
    base = os.path.dirname(os.path.abspath(config.DB_PATH)) or "."
    path = os.path.join(base, f"wsmo_export_{int(time.time())}.{fmt}")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return path


if __name__ == "__main__":
    import sys
    fmt = sys.argv[1] if len(sys.argv) > 1 else "json"
    print("wrote:", write_export(fmt))
