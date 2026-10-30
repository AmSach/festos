"""Builds the whole plan from the data pack (first run or reset)."""
import os
import time

from . import accommodation, entity, ingest, schedule, security
from .db import DB_PATH, connect, init_schema, set_setting


def build(path=None, log=print, schedule_seconds=None, frontier=True):
    path = path or DB_PATH
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(path + ext):
            os.remove(path + ext)
    con = connect(path)
    init_schema(con)
    t0 = time.time()
    now = "2026-10-30 07:00"
    log("FestOS · building plan from the data pack")
    ingest.ingest_all(con, now)
    log(f"  imported & validated pack ({con.execute('SELECT COUNT(*) FROM issues').fetchone()[0]} findings logged)")
    found, _ = entity.find_duplicates(con, now)
    log(f"  entity resolution: {len(found)} probable duplicate pair(s) held for review")
    security.seed_users(con, ingest.read_csv("core_team.csv"))
    security.audit(con, "system", "system", "import", "pack", "v1", {"files": 10}, "initial import of the synthetic data pack", now)
    con.commit()
    schedule.build(con, seconds=schedule_seconds, log=log)
    con.commit()
    security.audit(con, "system", "system", "plan_schedule", "schedule", "v1", {}, "initial schedule", now)
    m, *_ = accommodation.run(con, log=log)
    log(f"  accommodation: {m['placed']}/{m['people_needing_beds']} housed, ₹{m['external_cost']:,} external, {m['unplaced']} waiting")
    security.audit(con, "system", "system", "plan_accommodation", "accommodation", "v1",
                   {"placed": m["placed"], "unplaced": m["unplaced"]}, "initial accommodation plan", now)
    con.commit()
    if frontier:
        accommodation.frontier(con, log=log)
        con.commit()
    set_setting(con, "built_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    set_setting(con, "build_seconds", round(time.time() - t0, 1))
    con.commit()
    log(f"FestOS · plan ready in {time.time()-t0:.0f}s")
    return con
