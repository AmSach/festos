"""Disruption engine.

Every disruption is applied as a transaction that changes real state, then the
affected modules are repaired with *minimal change* (people not affected keep
their room / heat).  The same code runs on an in-memory clone for PREVIEW, so
an organiser sees the blast radius before committing.  Commits are snapshotted
and can be undone while nothing else has changed since.
"""
import json
import os
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import timedelta

from . import accommodation, alerts, entity, food, inventory, schedule, security, transport, timeutil as T
from .db import DB_PATH, SNAP_DIR, clone_to_memory, get_setting, jl, set_setting

TYPES = {
    "late_contingent": {"label": "Late contingent", "params": ["cid", "delay_hours"]},
    "duplicate_record": {"label": "Duplicate record", "params": ["source_pid", "name_variant", "contact"]},
    "hostel_closure": {"label": "Hostel closure", "params": ["target", "from"]},
    "equipment_failure": {"label": "Equipment failure", "params": ["item", "location", "qty"]},
    "venue_change": {"label": "Venue change / closure", "params": ["vid", "from", "to", "reason"]},
    "vendor_shutdown": {"label": "Vendor shutdown", "params": ["vid", "from", "reason"]},
    "duplicate_merch_claim": {"label": "Duplicate merchandise claim", "params": ["pid", "item"]},
    "participant_cancel": {"label": "Participant withdrawal", "params": ["pid"]},
}


# ====================================================================== state snapshot for diffs
def state(con):
    alloc = {r["pid"]: (r["site_id"], r["room_id"], r["status"], r["nights"]) for r in con.execute("SELECT * FROM alloc")}
    heats = {r["hid"]: (r["vid"], r["start"]) for r in con.execute("SELECT hid, vid, start FROM heats")}
    member = {(r["pid"], r["eid"]): r["heat_id"] for r in con.execute("SELECT pid, eid, heat_id FROM registrations")}
    conf = schedule.validate(con)
    fs = food.summary(con)
    ms = inventory.merch_status(con)
    eq = inventory.equipment_plan(con)
    tp = transport.plan(con)
    cost = sum(r[0] for r in con.execute("SELECT COALESCE(cost,0) FROM alloc WHERE status='placed'"))
    al = alerts.build(con, cache={"conflicts": conf, "food": fs, "merch": ms, "transport": tp})
    meal_hc = {m["meal_id"]: m["headcount"] for m in fs["meals"]}
    return {
        "alloc": alloc, "heats": heats, "member": member,
        "placed": sum(1 for a in alloc.values() if a[2] == "placed"),
        "unplaced": sum(1 for a in alloc.values() if a[2] == "unplaced"),
        "ext_cost": cost,
        "conflicts": {c["kind"]: c["count"] for c in conf},
        "diet_gaps": fs["gap_totals"], "meal_headcount": meal_hc,
        "eq_shortages": len(eq["shortages"]), "eq_short_detail": eq["shortages"],
        "stockout": ms["stockout"],
        "transport": {s["site_id"]: s["residents"] for s in tp["sites"]},
        "alerts": Counter(a["severity"] for a in al["alerts"]),
        "alert_keys": {a["key"]: a["title"] for a in al["alerts"]},
    }


def diff(before, after, con):
    names = {r["pid"]: (r["name"], r["cid"]) for r in con.execute("SELECT pid, name, cid FROM people")}
    sites = {r["site_id"]: r["name"] for r in con.execute("SELECT site_id, name FROM sites")}
    moved, lost, gained = [], [], []
    for pid, b in before["alloc"].items():
        a = after["alloc"].get(pid)
        if not a:
            if b[2] == "placed":
                lost.append(pid)
            continue
        if b[2] == "placed" and a[2] == "placed" and (b[0], b[1]) != (a[0], a[1]):
            moved.append({"pid": pid, "name": names.get(pid, ("", ""))[0], "cid": names.get(pid, ("", ""))[1],
                          "from": f"{b[1]} ({sites.get(b[0], b[0])})", "to": f"{a[1]} ({sites.get(a[0], a[0])})"})
        elif b[2] == "placed" and a[2] != "placed":
            lost.append(pid)
        elif b[2] != "placed" and a[2] == "placed":
            gained.append(pid)
    for pid, a in after["alloc"].items():
        if pid not in before["alloc"] and a[2] == "placed":
            gained.append(pid)
    heats_moved = []
    hn = {r["hid"]: r["name"] for r in con.execute("SELECT h.hid, e.name FROM heats h JOIN events e USING(eid)")}
    vn = {r["vid"]: r["name"] for r in con.execute("SELECT vid, name FROM venues")}
    for hid, b in before["heats"].items():
        a = after["heats"].get(hid)
        if a and a != b:
            heats_moved.append({"hid": hid, "event": hn.get(hid, hid), "from": f"{vn.get(b[0])} {b[1][5:]}", "to": f"{vn.get(a[0])} {a[1][5:]}"})
    reassigned = sorted({pid for k, h in after["member"].items() if before["member"].get(k) not in (None, h) for pid in [k[0]]})
    unseated = sorted({k[0] for k, h in after["member"].items() if h is None and before["member"].get(k)})
    conf_delta = {k: after["conflicts"].get(k, 0) - before["conflicts"].get(k, 0)
                  for k in set(before["conflicts"]) | set(after["conflicts"]) if after["conflicts"].get(k, 0) != before["conflicts"].get(k, 0)}
    diet_delta = {k: after["diet_gaps"].get(k, 0) - before["diet_gaps"].get(k, 0)
                  for k in set(before["diet_gaps"]) | set(after["diet_gaps"]) if after["diet_gaps"].get(k, 0) != before["diet_gaps"].get(k, 0)}
    meal_delta = {k: after["meal_headcount"].get(k, 0) - v for k, v in before["meal_headcount"].items() if after["meal_headcount"].get(k, 0) != v}
    tr_delta = {k: after["transport"].get(k, 0) - before["transport"].get(k, 0)
                for k in set(before["transport"]) | set(after["transport"]) if after["transport"].get(k, 0) != before["transport"].get(k, 0)}
    new_alerts = [t for k, t in after["alert_keys"].items() if k not in before["alert_keys"]]
    cleared = [t for k, t in before["alert_keys"].items() if k not in after["alert_keys"]]
    modules = {
        "accommodation": {"moved": len(moved), "lost_bed": len(lost), "gained_bed": len(gained),
                          "cost_delta": after["ext_cost"] - before["ext_cost"], "unplaced_delta": after["unplaced"] - before["unplaced"]},
        "schedule": {"heats_moved": len(heats_moved), "participants_reassigned": len(reassigned), "unseated": len(unseated), "conflicts_delta": conf_delta},
        "food": {"diet_gap_delta": diet_delta, "meals_changed": len(meal_delta)},
        "logistics": {"equipment_shortages_delta": after["eq_shortages"] - before["eq_shortages"]},
        "merch": {"stockout_changes": {k: [before["stockout"].get(k), v] for k, v in after["stockout"].items() if before["stockout"].get(k) != v}},
        "transport": {"residents_delta": tr_delta},
    }
    notify = sorted(set([m["pid"] for m in moved] + lost + reassigned))
    return {"modules": modules, "moved": moved[:300], "lost_bed": lost[:300], "gained_bed": gained[:300],
            "heats_moved": heats_moved, "reassigned": reassigned[:500], "unseated": unseated[:300],
            "meal_delta": meal_delta, "transport_delta": tr_delta, "eq_after": after["eq_short_detail"][:50],
            "new_alerts": new_alerts, "cleared_alerts": cleared, "notify_count": len(notify),
            "alerts_before": dict(before["alerts"]), "alerts_after": dict(after["alerts"])}


# ====================================================================== appliers
def apply(con, typ, prm, actor, now, log=lambda *a: None):
    """Mutate state for one disruption + minimal-change repair. Returns a summary."""
    if typ == "late_contingent":
        cid = prm["cid"]
        h = float(prm.get("delay_hours", 6))
        core, minh = get_setting(con, "night_core_window"), get_setting(con, "night_min_overlap_h")
        people = [dict(r) for r in con.execute("SELECT * FROM people WHERE cid=? AND status='active' AND kind='participant'", (cid,))]
        changed = []
        for p in people:
            na = T.f(T.p(p["arrival"]) + timedelta(hours=h))
            if T.p(na) >= T.p(p["departure"]):
                na = T.f(T.p(p["departure"]) - timedelta(minutes=30))
            nights = T.nights_mask(na, p["departure"], core, minh) if p["accom_req"] != "no" else 0
            con.execute("UPDATE people SET arrival=?, nights=? WHERE pid=?", (na, nights, p["pid"]))
            a = con.execute("SELECT * FROM alloc WHERE pid=?", (p["pid"],)).fetchone()
            if a and a["status"] == "placed" and a["nights"] != nights:
                cost = (a["cost"] // max(1, T.popcount(a["nights"]))) * T.popcount(nights)
                if nights:
                    con.execute("UPDATE alloc SET nights=?, cost=? WHERE pid=?", (nights, cost, p["pid"]))
                else:
                    con.execute("DELETE FROM alloc WHERE pid=?", (p["pid"],))
                changed.append(p["pid"])
        con.execute("UPDATE contingents SET delay_min=delay_min+? WHERE cid=?", (int(h * 60), cid))
        rep = schedule.repair(con, seconds=4, log=log)
        # freed bed-nights can house people who were waiting
        waiting = {r[0] for r in con.execute("SELECT pid FROM alloc WHERE status='unplaced'")}
        acc = accommodation.repair(con, waiting, log=log) if waiting else {"replaced": []}
        return {"people": len(people), "bed_nights_released": len(changed), "waitlist_housed": len(acc["replaced"]),
                "heats_moved": len(rep["moved_heats"])}

    if typ == "duplicate_record":
        src = dict(con.execute("SELECT * FROM people WHERE pid=?", (prm["source_pid"],)).fetchone())
        n = con.execute("SELECT COUNT(*) FROM people WHERE pid LIKE 'P_NEW%'").fetchone()[0]
        pid = f"P_NEW{n+1:02d}"
        name = prm.get("name_variant") or src["name"]
        contact = prm.get("contact") or "+91-9999999999"
        from . import textmatch
        cn = textmatch.normalize_phone(contact)
        cok = 0 if (not cn or textmatch.is_placeholder_phone(contact)) else 1
        cols = [k for k in src.keys() if k not in ("pid", "name", "contact_raw", "contact", "contact_ok", "checked_in", "source_row", "status", "merged_into")]
        con.execute(f"INSERT INTO people(pid,name,contact_raw,contact,contact_ok,status,source_row,{','.join(cols)}) VALUES(?,?,?,?,?,?,?,{','.join('?'*len(cols))})",
                    [pid, name, contact, cn, cok, "active", 99999] + [src[c] for c in cols])
        for r in con.execute("SELECT eid, team_id FROM registrations WHERE pid=?", (src["pid"],)).fetchall():
            con.execute("INSERT INTO registrations(pid,eid,team_id) VALUES(?,?,?)", (pid, r["eid"], r["team_id"]))
        con.execute("INSERT INTO issues(entity,entity_id,code,severity,message,ts) VALUES(?,?,?,?,?,?)",
                    ("participant", pid, "LATE_IMPORT", "info", f"Added during the fest by {actor}.", now))
        found, _ = entity.find_duplicates(con, now, only_pids={pid})
        held = con.execute("SELECT status FROM people WHERE pid=?", (pid,)).fetchone()[0] == "held_duplicate"
        if not held:
            waiting = {pid}
            accommodation.repair(con, waiting, log=log)
        return {"new_pid": pid, "matches": [{"keep": k, "dup": d, "score": s} for k, d, s, _ in found], "held": held}

    if typ == "hostel_closure":
        tgt = prm["target"]
        frm = prm.get("from") or now
        if tgt.startswith("EXT"):
            sids = [tgt]
        elif "-F" in tgt:
            sids = [tgt]
        else:
            sids = [r[0] for r in con.execute("SELECT site_id FROM sites WHERE block=?", (tgt,))]
        night_from = None
        for n in range(T.N_NIGHTS):
            if T.NIGHT0.replace(hour=23) + timedelta(days=n) >= T.p(frm):
                night_from = n
                break
        night_from = T.N_NIGHTS if night_from is None else night_from
        for s in sids:
            con.execute("UPDATE sites SET closed_from=?, status='closed' WHERE site_id=?", (frm, s))
            con.execute("UPDATE rooms SET closed_from=? WHERE site_id=?", (frm, s))
        affected = mask = (1 << T.N_NIGHTS) - (1 << night_from)
        displaced = {r["pid"] for r in con.execute(
            f"SELECT pid, nights FROM alloc WHERE status='placed' AND site_id IN ({','.join('?'*len(sids))})", sids)
            if r["nights"] & mask}
        waiting = {r[0] for r in con.execute("SELECT pid FROM alloc WHERE status='unplaced'")}
        rep = accommodation.repair(con, displaced, log=log)
        return {"sites": sids, "displaced": len(displaced), "re_placed": len(rep["replaced"]), "now_unplaced": len(set(rep["unplaced"]) & displaced)}

    if typ == "equipment_failure":
        item, loc, qty = prm["item"], prm.get("location"), int(prm.get("qty", 1))
        left = qty
        hit = []
        q = "SELECT * FROM logistics WHERE item=? AND state='available'" + (" AND location=?" if loc else "") + " ORDER BY qty DESC"
        for r in con.execute(q, (item, loc) if loc else (item,)).fetchall():
            if left <= 0:
                break
            t = min(left, r["qty"])
            _, nid, t = inventory.set_state(con, r["line_id"], t, "repair")
            con.execute("INSERT INTO stock_moves(ts,line_id,item,qty,from_loc,to_loc,from_state,to_state,actor,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (now, nid, item, t, r["location"], r["location"], "available", "repair", actor, "reported failure"))
            hit.append(nid)
            left -= t
        rep = schedule.repair(con, seconds=4, log=log)
        return {"units_failed": qty - left, "lines": hit, "heats_moved": len(rep["moved_heats"])}

    if typ == "venue_change":
        vid = prm["vid"]
        frm, to = prm.get("from") or now, prm.get("to") or "2026-11-01 20:00"
        v = con.execute("SELECT blocks FROM venues WHERE vid=?", (vid,)).fetchone()
        blocks = jl(v["blocks"], [])
        blocks.append([frm, to, prm.get("reason", "unavailable")])
        con.execute("UPDATE venues SET blocks=? WHERE vid=?", (json.dumps(blocks), vid))
        rep = schedule.repair(con, seconds=4, log=log)
        out = {"heats_moved": len(rep["moved_heats"]), "participants_reassigned": len(rep["participants_reassigned"])}
        if rep.get("resplit"):
            out["re_split"] = "; ".join(f"{r['event']} {r['round']} re-split into heats of ≤{r['new_heat_size']}" for r in rep["resplit"])
        return out

    if typ == "vendor_shutdown":
        con.execute("UPDATE vendors SET shutdown_from=?, shutdown_reason=? WHERE vid=?",
                    (prm.get("from") or now, prm.get("reason", "shutdown"), prm["vid"]))
        return {"vendor": prm["vid"]}

    if typ == "duplicate_merch_claim":
        pid, item = prm["pid"], prm.get("item", "T-shirt")
        first = inventory.issue(con, pid, item, None, actor, "Desk 2", now)
        second = inventory.issue(con, pid, item, None, actor, "Desk 4", now) if first.get("ok") else first
        return {"first": first, "second": second}

    if typ == "participant_cancel":
        pid = prm["pid"]
        con.execute("UPDATE people SET status='cancelled' WHERE pid=?", (pid,))
        con.execute("DELETE FROM alloc WHERE pid=?", (pid,))
        con.execute("UPDATE registrations SET heat_id=NULL WHERE pid=?", (pid,))
        waiting = {r[0] for r in con.execute("SELECT pid FROM alloc WHERE status='unplaced'")}
        acc = accommodation.repair(con, waiting, log=log) if waiting else {"replaced": []}
        return {"pid": pid, "waitlist_housed": len(acc["replaced"])}
    raise ValueError(f"unknown incident type {typ}")


def title_of(typ, prm, con):
    if typ == "late_contingent":
        c = con.execute("SELECT college FROM contingents WHERE cid=?", (prm["cid"],)).fetchone()
        return f"{c[0] if c else prm['cid']} delayed {prm.get('delay_hours', 6)} h"
    if typ == "hostel_closure":
        return f"{prm['target']} closed from {(prm.get('from') or '')[5:]}"
    if typ == "equipment_failure":
        return f"{prm.get('qty', 1)} × {prm['item']} failed" + (f" at {prm['location']}" if prm.get("location") else "")
    if typ == "venue_change":
        v = con.execute("SELECT name FROM venues WHERE vid=?", (prm["vid"],)).fetchone()
        return f"{v[0] if v else prm['vid']} unavailable {(prm.get('from') or '')[5:]}–{(prm.get('to') or '')[11:]}"
    if typ == "vendor_shutdown":
        v = con.execute("SELECT name, menu FROM vendors WHERE vid=?", (prm["vid"],)).fetchone()
        return f"{v[0]} ({v[1]}) shut down" if v else f"{prm['vid']} shut down"
    if typ == "duplicate_record":
        return f"Duplicate of {prm['source_pid']} submitted"
    if typ == "duplicate_merch_claim":
        return f"Second {prm.get('item', 'T-shirt')} claim by {prm['pid']}"
    if typ == "participant_cancel":
        return f"{prm['pid']} withdrew"
    return typ


# ====================================================================== preview / commit / undo
def preview(live, typ, prm, actor, now, before=None):
    mem = clone_to_memory(live)
    before = before or state(mem)
    summary = apply(mem, typ, prm, actor, now)
    mem.commit()
    after = state(mem)
    d = diff(before, after, mem)
    mem.close()
    return {"type": typ, "title": title_of(typ, prm, live), "summary": summary, "impact": d}


def commit(live, typ, prm, actor, role, now, reason, before=None):
    os.makedirs(SNAP_DIR, exist_ok=True)
    n = live.execute("SELECT COALESCE(MAX(id),0)+1 FROM incidents").fetchone()[0]
    snap = os.path.join(SNAP_DIR, f"before_incident_{n}.db")
    bk = sqlite3.connect(snap)
    live.backup(bk)
    bk.close()
    before = before or state(live)
    summary = apply(live, typ, prm, actor, now)
    after = state(live)
    d = diff(before, after, live)
    title = title_of(typ, prm, live)
    live.execute("INSERT INTO incidents(id,ts,type,params,status,impact,reason,actor,snapshot,title) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (n, now, typ, json.dumps(prm), "committed", json.dumps({"summary": summary, "impact": d}), reason, actor, snap, title))
    security.audit(live, actor, role, "incident_commit", "incident", str(n), {"type": typ, "params": prm, "summary": summary}, reason, now)
    live.commit()
    # refresh cached accommodation metrics
    try:
        people, sites, conts, early = accommodation.load(live)
        m = accommodation.metrics_from_db(live, {"status": get_setting(live, "accommodation_metrics", {}).get("solver", ""), "solve_seconds": 0,
                                                 "n_vars": 0, "n_rows": 0, "budget": get_setting(live, "budget_external_inr"), "designation": {}}, sites, conts)
        set_setting(live, "accommodation_metrics", m)
        live.commit()
    except Exception as e:  # pragma: no cover
        pass
    return {"id": n, "title": title, "summary": summary, "impact": d}


def undo(live, inc_id, actor, role, now, reason):
    inc = live.execute("SELECT * FROM incidents WHERE id=?", (inc_id,)).fetchone()
    if not inc or inc["status"] != "committed":
        return {"ok": False, "message": "Incident not found or not committed."}
    last = live.execute("SELECT MAX(id) FROM incidents WHERE status='committed'").fetchone()[0]
    if last != inc_id:
        return {"ok": False, "message": "Only the most recent incident can be undone (later incidents depend on it)."}
    later = live.execute("""SELECT COUNT(*) FROM audit WHERE seq > (SELECT seq FROM audit WHERE action='incident_commit' AND entity_id=? ORDER BY seq DESC LIMIT 1)
                            AND action NOT IN ('login','view','break_glass','export','clock')""", (str(inc_id),)).fetchone()[0]
    if later:
        return {"ok": False, "message": f"{later} other change(s) were made after this incident; undo would erase them. Apply a counter-action instead."}
    keep_audit = [dict(r) for r in live.execute("SELECT * FROM audit ORDER BY seq")]
    keep_inc = [dict(r) for r in live.execute("SELECT * FROM incidents ORDER BY id")]
    src = sqlite3.connect(inc["snapshot"])
    src.backup(live)
    src.close()
    live.execute("DELETE FROM audit")
    for r in keep_audit:
        live.execute("INSERT INTO audit(seq,ts,actor,role,action,entity,entity_id,detail,reason,off_shift,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     tuple(r[k] for k in ("seq", "ts", "actor", "role", "action", "entity", "entity_id", "detail", "reason", "off_shift", "prev_hash", "hash")))
    live.execute("DELETE FROM incidents")
    for r in keep_inc:
        live.execute("INSERT INTO incidents(id,ts,type,params,status,impact,reason,actor,snapshot,title) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     tuple(r[k] for k in ("id", "ts", "type", "params", "status", "impact", "reason", "actor", "snapshot", "title")))
    live.execute("UPDATE incidents SET status='undone' WHERE id=?", (inc_id,))
    security.audit(live, actor, role, "incident_undo", "incident", str(inc_id), {"title": inc["title"]}, reason, now)
    live.commit()
    return {"ok": True}
