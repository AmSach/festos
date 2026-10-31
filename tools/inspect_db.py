import sqlite3
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

con = sqlite3.connect(r"E:\festos\festos.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

for t in ["people", "alloc", "overrides", "audit"]:
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
    print(f"\n=== Table: {t} ===")
    print("Columns:", ", ".join(cols))
    sample = cur.execute(f"SELECT * FROM {t} LIMIT 1").fetchone()
    if sample:
        d = dict(sample)
        # trim long fields
        for k, v in d.items():
            s = str(v)
            if len(s) > 60:
                s = s[:57] + "..."
            print(f"  {k}: {s}")
