"""Logistics stock (time- and location-aware) and merchandise issuance."""
import json
import math
from collections import Counter, defaultdict

from . import timeutil as T
from .db import get_setting, jl

STORES = {"Store Room A", "Registration Desk", "Van 1"}
VENUE_LOC = {"Main Stage": "V01", "Sports Complex": "V08"}


# ====================================================================== logistics
def stock_summary(con):
    rows = [dict(r) for r in con.execute("SELECT * FROM logistics ORDER BY item, line_id")]
    items = defaultdict(lambda: {"available": 0, "inspect": 0, "repair": 0, "transit": 0, "reserved": 0, "lines": []})
    for r in rows:
        it = items[r["item"]]
        it[r["state"]] = it.get(r["state"], 0) + r["qty"]
        it["lines"].append(r)
    return items


def equipment_plan(con):
    """Walk the schedule in time order and decide where each unit comes from.
    Units stay at a venue after a heat and are re-used there; otherwise they are
    pulled from the location holding the most available units."""
    venues = {r["vid"]: r["name"] for r in con.execute("SELECT vid,name FROM venues")}
    ev = {}
    for r in con.execute("SELECT eid, name, equipment, waived_equipment FROM events"):
        eq = jl(r["equipment"], {})
        for w in jl(r["waived_equipment"], []):
            eq.pop(w, None)
        ev[r["eid"]] = (r["name"], eq)
    heats = [dict(r) for r in con.execute("SELECT * FROM heats ORDER BY start")]
    pool = defaultdict(Counter)        # item -> location -> free units
    for r in con.execute("SELECT item, location, SUM(qty) q FROM logistics WHERE state='available' GROUP BY item, location"):
        pool[r["item"]][r["location"]] += r["q"]
    busy = []                          # (free_at, item, venue_name, qty)
    moves, shortages, reservations = [], [], []
    for h in heats:
        start, end = h["start"], h["end"]
        # release units whose heat ended
        still = []
        for fa, item, loc, q in busy:
            if fa <= start:
                pool[item][loc] += q
            else:
                still.append((fa, item, loc, q))
        busy = still
        vname = venues[h["vid"]]
        name, eq = ev[h["eid"]]
        for item, q in eq.items():
            need = q
            got = []
            take = min(need, pool[item][vname])
            if take:
                pool[item][vname] -= take
                need -= take
                got.append((vname, take))
            for loc, avail in sorted(pool[item].items(), key=lambda kv: -kv[1]):
                if need <= 0:
                    break
                if loc == vname or avail <= 0:
                    continue
                t = min(avail, need)
                pool[item][loc] -= t
                need -= t
                got.append((loc, t))
                moves.append({"by": _minus(start, 30), "item": item, "qty": t, "from": loc, "to": vname,
                              "for": f"{name} · {h['round']} · {h['hid']}"})
            if need > 0:
                shortages.append({"hid": h["hid"], "event": name, "item": item, "need": q, "missing": need,
                                  "start": start, "venue": vname})
            reservations.append({"hid": h["hid"], "event": name, "item": item, "qty": q - need, "need": q,
                                 "start": start, "end": end, "venue": vname, "sources": got})
            busy.append((_plus(end, 15), item, vname, q - need))
    return {"moves": moves, "shortages": shortages, "reservations": reservations}


def _plus(s, m):
    from datetime import timedelta
    return T.f(T.p(s) + timedelta(minutes=m))


def _minus(s, m):
    return _plus(s, -m)


def set_state(con, line_id, qty, new_state, to_loc=None):
    """Split a stock line when only part of it changes state/location."""
    r = con.execute("SELECT * FROM logistics WHERE line_id=?", (line_id,)).fetchone()
    if not r:
        raise ValueError("unknown stock line")
    qty = min(int(qty), r["qty"])
    if qty <= 0:
        raise ValueError("quantity must be positive")
    loc = to_loc or r["location"]
    if qty == r["qty"]:
        con.execute("UPDATE logistics SET state=?, location=? WHERE line_id=?", (new_state, loc, line_id))
        nid = line_id
    else:
        con.execute("UPDATE logistics SET qty=qty-? WHERE line_id=?", (qty, line_id))
        n = con.execute("SELECT COUNT(*) FROM logistics WHERE line_id LIKE ?", (line_id + "%",)).fetchone()[0]
        nid = f"{line_id}.{n}"
        con.execute("INSERT INTO logistics(line_id,item,qty,location,condition,state) VALUES(?,?,?,?,?,?)",
                    (nid, r["item"], qty, loc, r["condition"], new_state))
    return dict(r), nid, qty


# ====================================================================== merchandise
def merch_status(con):
    kit = get_setting(con, "kit_items")
    stock = {(r["item"], r["size"]): dict(r) for r in con.execute("SELECT * FROM merch_stock")}
    issued = Counter()
    for r in con.execute("SELECT item, size, COUNT(*) n FROM merch_issues WHERE voided=0 GROUP BY item, size"):
        issued[(r["item"], r["size"])] = r["n"]
    people = [dict(r) for r in con.execute("SELECT pid, cid, tshirt, arrival, status FROM people WHERE kind='participant' AND status IN ('active','needs_confirmation')")]
    demand = Counter()
    for p in people:
        for it in kit:
            if it == "T-shirt":
                demand[("T-shirt", p["tshirt"] or "?")] += 1
            else:
                demand[(it, "NA")] += 1
    got = defaultdict(set)
    for r in con.execute("SELECT pid, item FROM merch_issues WHERE voided=0 AND kind='issue'"):
        got[r["pid"]].add(r["item"])
    out = []
    keys = sorted(set(stock) | set(demand), key=lambda k: (k[0], ["S", "M", "L", "XL", "XXL", "NA", "?"].index(k[1]) if k[1] in ["S", "M", "L", "XL", "XXL", "NA", "?"] else 9))
    for k in keys:
        st = stock.get(k, {"qty": 0, "reserved": 0})
        avail_total = st["qty"] - st["reserved"]
        d = demand.get(k, 0)
        iss = issued.get(k, 0)
        remaining_need = max(0, d - iss) if k[0] in kit else 0
        left = avail_total - iss
        out.append({"item": k[0], "size": k[1], "qty": st["qty"], "reserved": st["reserved"], "issued": iss,
                    "on_hand": left, "demand": d if k[0] in kit else None,
                    "gap": (left - remaining_need) if k[0] in kit else None,
                    "entitled": k[0] in kit})
    # stock-out forecast by arrival order
    arrivals = sorted([p for p in people if p["status"] == "active"], key=lambda p: p["arrival"] or "9")
    stockout = {}
    for it in kit:
        for size in (["S", "M", "L", "XL", "XXL"] if it == "T-shirt" else ["NA"]):
            k = (it, size)
            st = stock.get(k)
            if not st:
                continue
            left = st["qty"] - st["reserved"] - issued.get(k, 0)
            cnt = 0
            for p in arrivals:
                if it in got[p["pid"]]:
                    continue
                if it == "T-shirt" and p["tshirt"] != size:
                    continue
                cnt += 1
                if cnt > left:
                    stockout[f"{it}|{size}"] = p["arrival"]
                    break
    # fair share of scarce T-shirt sizes per contingent
    fair = {}
    for size in ["S", "M", "L", "XL", "XXL"]:
        st = stock.get(("T-shirt", size))
        if not st:
            continue
        left = st["qty"] - st["reserved"] - issued.get(("T-shirt", size), 0)
        dem = Counter(p["cid"] for p in people if p["tshirt"] == size and "T-shirt" not in got[p["pid"]])
        tot = sum(dem.values())
        if tot > left > 0:
            raw = {c: left * n / tot for c, n in dem.items()}
            base = {c: int(v) for c, v in raw.items()}
            rem = left - sum(base.values())
            for c, _ in sorted(raw.items(), key=lambda kv: -(kv[1] - int(kv[1])))[:rem]:
                base[c] += 1
            fair[size] = {"available": left, "demand": tot, "quota": base}
    return {"lines": out, "stockout": stockout, "fair_share": fair, "kit": kit}


def issue(con, pid, item, size, actor, desk, now, kind="issue", note=None, allow_sub=False):
    """Issue one item. Returns dict(ok, ...). Duplicate claims are blocked and logged."""
    p = con.execute("SELECT * FROM people WHERE pid=?", (pid,)).fetchone()
    if not p:
        return {"ok": False, "code": "UNKNOWN_PERSON", "message": f"No participant {pid}."}
    p = dict(p)
    if p["status"] == "merged":
        return {"ok": False, "code": "MERGED", "message": f"{pid} was merged into {p['merged_into']} — issue against that record."}
    if p["status"] == "held_duplicate":
        return {"ok": False, "code": "HELD", "message": f"{pid} is held as a probable duplicate; resolve it in Data Quality first."}
    kit = get_setting(con, "kit_items")
    if item not in kit and kind == "issue":
        return {"ok": False, "code": "NOT_ENTITLED", "message": f"No eligibility rule for '{item}' — needs a policy decision."}
    if item == "T-shirt":
        size = size or p["tshirt"]
        if not size:
            return {"ok": False, "code": "SIZE_UNKNOWN", "message": "Size not recorded — ask the participant."}
    else:
        size = "NA"
    if kind == "issue":
        prev = con.execute("SELECT * FROM merch_issues WHERE pid=? AND item=? AND kind='issue' AND voided=0", (pid, item)).fetchone()
        if prev:
            con.execute("INSERT INTO merch_blocked(ts,pid,item,actor,desk,original_issue) VALUES(?,?,?,?,?,?)",
                        (now, pid, item, actor, desk, prev["id"]))
            n = con.execute("SELECT COUNT(*) FROM merch_blocked WHERE pid=? AND item=?", (pid, item)).fetchone()[0]
            return {"ok": False, "code": "DUPLICATE_CLAIM",
                    "message": f"{item} already issued to {pid} at {prev['ts']} by {prev['actor']} ({prev['desk']}). Claim #{n+1} blocked.",
                    "original": dict(prev), "attempts": n}
    st = con.execute("SELECT * FROM merch_stock WHERE item=? AND size=?", (item, size)).fetchone()
    issued = con.execute("SELECT COUNT(*) FROM merch_issues WHERE item=? AND size=? AND voided=0", (item, size)).fetchone()[0]
    left = (st["qty"] - st["reserved"] - issued) if st else 0
    if left <= 0:
        subs = (get_setting(con, "tshirt_substitution") or {}).get(size, []) if item == "T-shirt" else []
        alt = []
        for s2 in subs:
            st2 = con.execute("SELECT * FROM merch_stock WHERE item=? AND size=?", (item, s2)).fetchone()
            iss2 = con.execute("SELECT COUNT(*) FROM merch_issues WHERE item=? AND size=? AND voided=0", (item, s2)).fetchone()[0]
            if st2 and st2["qty"] - st2["reserved"] - iss2 > 0:
                alt.append(s2)
        if not alt or not allow_sub:
            return {"ok": False, "code": "OUT_OF_STOCK", "message": f"{item} {size} is out of stock.", "substitutes": alt}
        size = alt[0]
        note = (note or "") + f" substituted from {p['tshirt']} with consent"
    con.execute("INSERT INTO merch_issues(pid,item,size,ts,actor,desk,kind,note) VALUES(?,?,?,?,?,?,?,?)",
                (pid, item, size, now, actor, desk, kind, note))
    return {"ok": True, "item": item, "size": size, "pid": pid, "name": p["name"], "left": left - 1}
