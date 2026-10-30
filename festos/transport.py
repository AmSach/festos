"""Shuttle planning for external properties, derived from who sleeps where and
when their events start/end."""
import math
from collections import defaultdict
from datetime import timedelta

from . import timeutil as T
from .db import get_setting

FEST_DAYS = ["2026-10-30", "2026-10-31", "2026-11-01"]


def plan(con):
    cap = get_setting(con, "bus_capacity")
    late_h = get_setting(con, "late_return_hour")
    sites = {r["site_id"]: dict(r) for r in con.execute("SELECT * FROM sites WHERE kind='external'")}
    res = defaultdict(list)
    for r in con.execute("""SELECT a.site_id, p.pid, p.arrival, p.departure, p.is_minor, p.housing_cat, p.kind
                            FROM alloc a JOIN people p USING(pid) WHERE a.status='placed'"""):
        if r["site_id"] in sites:
            res[r["site_id"]].append(dict(r))
    ev = defaultdict(list)
    for r in con.execute("SELECT r.pid, h.start, h.end FROM registrations r JOIN heats h ON h.hid=r.heat_id"):
        ev[r["pid"]].append((r["start"], r["end"]))
    out = []
    risks = []
    for sid, s in sorted(sites.items()):
        speed = 18.0
        travel = int(round(s["distance"] / speed * 60 + 10))
        days = []
        for d in FEST_DAYS:
            d0 = T.p(d + " 06:00")
            d1 = T.p(d + " 23:59")
            present = [p for p in res[sid] if T.p(p["arrival"]) <= d1 and T.p(p["departure"]) >= d0]
            if not present:
                continue
            starts = [st for p in present for st, en in ev[p["pid"]] if st[:10] == d]
            ends = [en for p in present for st, en in ev[p["pid"]] if en[:10] == d]
            first = min(starts) if starts else d + " 09:00"
            last = max(ends) if ends else d + " 18:00"
            # morning wave: everyone with an event that day needs to be on campus 15 min before
            with_ev = [p for p in present if any(st[:10] == d for st, _ in ev[p["pid"]])]
            depart = T.f(T.p(first) - timedelta(minutes=travel + 15))
            ret = T.f(T.p(last) + timedelta(minutes=10))
            late = T.p(ret).hour >= late_h or T.p(ret).hour < 5
            minors = sum(1 for p in present if p["is_minor"])
            women = sum(1 for p in present if p["housing_cat"] == "F")
            trips = math.ceil(len(present) / cap) if present else 0
            day = {"date": d, "residents": len(present), "with_events": len(with_ev), "first_event": first,
                   "first_departure": depart, "last_event_end": last, "last_return": ret, "trips_each_way": trips,
                   "late_return": late, "minors": minors, "women": women}
            days.append(day)
            if late and (minors or women):
                risks.append({"site": sid, "name": s["name"], "date": d, "msg":
                              f"{s['name']} ({s['distance']} km): last return {ret[11:]} with {women} women and {minors} minors resident — staff escort on the last shuttle."})
            if s["transport"] != "yes" and present:
                risks.append({"site": sid, "name": s["name"], "date": d, "msg":
                              f"{s['name']} has '{s['transport']}' transport but {len(present)} residents on {d[5:]} — book {trips} dedicated trips each way."})
        # arrivals into this property by hour (pickup from campus reception)
        arr = defaultdict(int)
        for p in res[sid]:
            arr[p["arrival"][:13] + ":00"] += 1
        out.append({"site_id": sid, "name": s["name"], "distance": s["distance"], "transport": s["transport"],
                    "travel_min": travel, "days": days, "arrivals": dict(sorted(arr.items())),
                    "residents": len(res[sid])})
    return {"sites": out, "risks": risks, "bus_capacity": cap}
