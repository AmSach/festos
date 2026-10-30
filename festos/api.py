"""HTTP API. Every write: permission check → reason (when risky) → change → audit."""
import csv
import io
import json
import threading
import time
from collections import Counter, defaultdict
from datetime import timedelta

from . import (accommodation, alerts, config, entity, food, incidents, ingest, inventory, schedule, security,
               transport, timeutil as T)
from .db import DB_PATH, connect, get_setting, jl, rows, one, set_setting
from .server import HTTPError, route

LOCK = threading.RLock()
_CACHE = {}
FRONTIER_RUNNING = {"on": False}


# ====================================================================== helpers
_LOCAL = threading.local()


def db():
    con = connect(DB_PATH)
    if not hasattr(_LOCAL, "cons"):
        _LOCAL.cons = []
    _LOCAL.cons.append(con)
    return con


def cleanup():
    """Called after every request: roll back anything uncommitted and close."""
    for con in getattr(_LOCAL, "cons", []):
        try:
            con.rollback()
            con.close()
        except Exception:
            pass
    _LOCAL.cons = []


def clock(con):
    return alerts.now(con)


def version(con):
    return get_setting(con, "state_version", 0)


def bump(con):
    set_setting(con, "state_version", version(con) + 1)
    _CACHE.clear()


def cached(con, key, fn):
    v = version(con)
    hit = _CACHE.get(key)
    if hit and hit[0] == v:
        return hit[1]
    val = fn()
    _CACHE[key] = (v, val)
    return val


def need(req, action):
    if not security.can_write(req.role, action):
        raise HTTPError(403, f"Your role ({req.role}) is not allowed to {action.replace('_', ' ')}.")


def reason_required(req, min_len=8):
    r = (req.body.get("reason") or "").strip()
    if len(r) < min_len:
        raise HTTPError(400, "A reason is required for this change (at least a short sentence).", {"need_reason": True})
    return r


def audit(con, req, action, entity=None, entity_id=None, detail=None, reason=None):
    user = one(con, "SELECT shift FROM users WHERE uid=?", (req.uid,))
    now = clock(con)
    off = 0 if (not user or security.on_shift(user["shift"], now)) else 1
    security.audit(con, req.uid, req.role, action, entity, entity_id, detail, reason, now, off)


def conflicts(con):
    return cached(con, "conflicts", lambda: schedule.validate(con))


def food_summary(con):
    return cached(con, "food", lambda: food.summary(con))


def merch_status(con):
    return cached(con, "merch", lambda: inventory.merch_status(con))


def transport_plan(con):
    return cached(con, "transport", lambda: transport.plan(con))


def alert_list(con):
    return cached(con, "alerts", lambda: alerts.build(con, cache={"conflicts": conflicts(con), "food": food_summary(con),
                                                                     "merch": merch_status(con), "transport": transport_plan(con)}))


# ====================================================================== auth
@route("POST", "/login", auth=False)
def login(req):
    uid = (req.body.get("uid") or "").strip().upper()
    pw = req.body.get("password") or ""
    key = f"{uid}@{req.h.client_address[0]}"
    if security.throttled(key):
        raise HTTPError(429, "Too many attempts — wait a minute.")
    con = db()
    u = one(con, "SELECT * FROM users WHERE uid=? AND active=1", (uid,))
    if not u or not security.check_pw(pw, u["salt"], u["pw_hash"]):
        security.note_fail(key)
        with LOCK:
            security.audit(con, uid or "?", "-", "login_failed", "user", uid, {}, None, clock(con))
            con.commit()
        raise HTTPError(401, "Wrong staff ID or password.")
    security.clear_fail(key)
    tok = security.make_token(u["uid"], u["role_key"])
    with LOCK:
        security.audit(con, u["uid"], u["role_key"], "login", "user", u["uid"], {}, None, clock(con))
        con.commit()
    return {"token": tok, "user": _user(u, con)}


def _user(u, con):
    p = config.PERMISSIONS[u["role_key"]]
    return {"uid": u["uid"], "name": u["name"], "role": u["role"], "role_key": u["role_key"], "shift": u["shift"],
            "on_shift": security.on_shift(u["shift"], clock(con)),
            "view": p["view"], "write": p["write"]}


@route("GET", "/me")
def me(req):
    con = db()
    u = one(con, "SELECT * FROM users WHERE uid=?", (req.uid,))
    return {"user": _user(u, con), "clock": clock(con), "clock_mode": get_setting(con, "clock_mode"),
            "built_at": get_setting(con, "built_at"), "version": version(con)}


@route("GET", "/demo-accounts", auth=False)
def demo_accounts(req):
    con = db()
    out = []
    for rk in ["director", "hospitality", "registration", "events", "logistics", "merch", "food", "volunteer"]:
        u = one(con, "SELECT uid, name, role, shift FROM users WHERE role_key=? ORDER BY uid LIMIT 1", (rk,))
        if u:
            out.append({**u, "role_key": rk})
    return {"accounts": out, "password": config.DEMO_PASSWORD}


@route("POST", "/clock")
def set_clock(req):
    need(req, "*") if req.role != "director" else None
    con = db()
    with LOCK:
        if req.body.get("mode"):
            set_setting(con, "clock_mode", req.body["mode"])
        if req.body.get("clock"):
            if not T.p(req.body["clock"]):
                raise HTTPError(400, "bad time")
            set_setting(con, "clock", T.f(T.p(req.body["clock"])))
        audit(con, req, "clock", "clock", None, {"clock": req.body.get("clock"), "mode": req.body.get("mode")})
        bump(con)
        con.commit()
    return {"clock": clock(con)}


# ====================================================================== control tower
@route("GET", "/control", view="control")
def control(req):
    con = db()
    al = alert_list(con)
    board = alerts.now_board(con)
    m = get_setting(con, "accommodation_metrics", {})
    role = req.role
    items = al["alerts"]
    if role != "director":
        mine = [a for a in items if a["owner"] == role]
        rest = [a for a in items if a["owner"] != role]
        items = mine + rest
    sev = Counter(a["severity"] for a in al["alerts"])
    fs = food_summary(con)
    kpis = {
        "onsite": board["onsite"], "tonight": board["tonight"],
        "placed": m.get("placed"), "need_beds": m.get("people_needing_beds"), "unplaced": m.get("unplaced"),
        "ext_cost": m.get("external_cost"), "budget": get_setting(con, "budget_external_inr"),
        "alerts": dict(sev), "heats_now": len(board["running"]), "heats_next": len(board["upcoming"]),
        "diet_gaps": sum(fs["gap_totals"].values()),
        "incidents": con.execute("SELECT COUNT(*) FROM incidents WHERE status='committed'").fetchone()[0],
    }
    recent = rows(con, "SELECT seq, ts, actor, role, action, entity, entity_id, reason FROM audit WHERE action NOT IN ('login','view') ORDER BY seq DESC LIMIT 12")
    inc = rows(con, "SELECT id, ts, type, title, status, actor FROM incidents ORDER BY id DESC LIMIT 6")
    return {"clock": al["clock"], "alerts": items, "board": board, "kpis": kpis, "recent": recent, "incidents": inc}


@route("GET", "/search")
def search(req):
    q = (req.q("q") or "").strip()
    con = db()
    out = []
    if not q:
        return {"results": []}
    ql = f"%{q}%"
    if security.can_view(req.role, "participants"):
        for r in con.execute("SELECT pid, name, college, cid, kind, status FROM people WHERE (pid LIKE ? OR name LIKE ?) AND status!='merged' LIMIT 12", (ql, ql)):
            out.append({"type": "person", "id": r["pid"], "label": f"{r['name']}", "sub": f"{r['pid']} · {r['college']}" + ("" if r["status"] == "active" else f" · {r['status']}"), "link": f"#/people/{r['pid']}"})
    for r in con.execute("SELECT cid, college FROM contingents WHERE cid LIKE ? OR college LIKE ? LIMIT 6", (ql, ql)):
        out.append({"type": "contingent", "id": r["cid"], "label": r["college"], "sub": r["cid"], "link": f"#/contingents/{r['cid']}"})
    for r in con.execute("SELECT eid, name FROM events WHERE eid LIKE ? OR name LIKE ? LIMIT 6", (ql, ql)):
        out.append({"type": "event", "id": r["eid"], "label": r["name"], "sub": r["eid"], "link": f"#/schedule?event={r['eid']}"})
    for r in con.execute("SELECT vid, name FROM venues WHERE vid LIKE ? OR name LIKE ? LIMIT 4", (ql, ql)):
        out.append({"type": "venue", "id": r["vid"], "label": r["name"], "sub": r["vid"], "link": f"#/schedule?venue={r['vid']}"})
    if security.can_view(req.role, "accommodation"):
        for r in con.execute("SELECT site_id, name FROM sites WHERE site_id LIKE ? OR name LIKE ? LIMIT 4", (ql, ql)):
            out.append({"type": "site", "id": r["site_id"], "label": r["name"], "sub": r["site_id"], "link": f"#/accommodation?site={r['site_id']}"})
    return {"results": out[:24]}


# ====================================================================== participants
@route("GET", "/participants", view="participants")
def participants(req):
    con = db()
    where, args = ["p.status != 'merged'"], []
    q = req.q("q")
    if q:
        where.append("(p.pid LIKE ? OR p.name LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    for k, col in (("cid", "p.cid"), ("kind", "p.kind"), ("status", "p.status")):
        if req.q(k):
            where.append(f"{col}=?")
            args.append(req.q(k))
    if req.q("need") and security.field_ok(req.role, "accessibility"):
        where.append("p.access_need=?")
        args.append(req.q("need"))
    if req.q("bed") == "none":
        where.append("a.status='unplaced'")
    if req.q("minor") == "1":
        where.append("p.is_minor=1")
    if req.q("issue"):
        where.append("p.pid IN (SELECT entity_id FROM issues WHERE code=?)")
        args.append(req.q("issue"))
    lim = min(int(req.q("limit", 200)), 500)
    off = int(req.q("offset", 0))
    total = con.execute(f"SELECT COUNT(*) FROM people p LEFT JOIN alloc a USING(pid) WHERE {' AND '.join(where)}", args).fetchone()[0]
    rs = rows(con, f"""SELECT p.*, a.site_id, a.room_id, a.status bed_status,
                         (SELECT COUNT(*) FROM registrations r WHERE r.pid=p.pid) n_events,
                         (SELECT COUNT(*) FROM issues i WHERE i.entity_id=p.pid AND i.status='open' AND i.severity IN ('high','critical','medium')) n_issues
                       FROM people p LEFT JOIN alloc a USING(pid) WHERE {' AND '.join(where)} ORDER BY p.pid LIMIT ? OFFSET ?""", args + [lim, off])
    room_ok = security.field_ok(req.role, "room")
    out = []
    for r in rs:
        m = security.mask_person(r, req.role)
        if not room_ok:
            m["room_id"] = None
        out.append({k: m.get(k) for k in ("pid", "name", "college", "cid", "kind", "status", "housing_cat", "gender_raw", "is_minor",
                                          "access_need", "diet", "arrival", "departure", "accom_req", "tshirt", "site_id",
                                          "room_id", "bed_status", "n_events", "n_issues", "checked_in", "contact")})
    return {"total": total, "rows": out}


@route("GET", "/participants/{pid}", view="participants")
def person(req, pid):
    con = db()
    p = one(con, "SELECT * FROM people WHERE pid=?", (pid,))
    if not p:
        raise HTTPError(404, "No such person.")
    revealed = req.q("reveal") == "1" and _recent_break_glass(con, req.uid, pid)
    m = security.mask_person(p, req.role, reveal=revealed)
    c = one(con, "SELECT * FROM contingents WHERE cid=?", (p["cid"],))
    regs = rows(con, """SELECT r.eid, r.team_id, r.heat_id, e.name, e.type, h.round, h.start, h.end, h.vid, v.name venue
                        FROM registrations r JOIN events e USING(eid) LEFT JOIN heats h ON h.hid=r.heat_id LEFT JOIN venues v ON v.vid=h.vid
                        WHERE r.pid=? ORDER BY h.start""", (pid,))
    later = []
    for r in regs:
        for h in con.execute("SELECT h.hid, h.round, h.start, h.end, v.name venue FROM heats h JOIN venues v USING(vid) WHERE h.eid=? AND h.round IN ('semis','finals') ORDER BY start", (r["eid"],)):
            later.append({**dict(h), "event": r["name"], "conditional": True})
    # conflicts for this person
    flags = []
    a0, d0 = T.p(p["arrival"]), T.p(p["departure"])
    hs = [r for r in regs if r["start"]]
    for i in range(len(hs)):
        x = hs[i]
        if a0 and d0 and not (a0 <= T.p(x["start"]) and d0 >= T.p(x["end"])):
            flags.append(f"Not on site for {x['name']} ({x['start'][5:]}) — arrives {p['arrival'][5:]}, leaves {p['departure'][5:]}.")
        for y in hs[i + 1:]:
            if T.p(y["start"]) < T.p(x["end"]) + timedelta(minutes=15) and T.p(x["start"]) < T.p(y["end"]):
                flags.append(f"{x['name']} and {y['name']} overlap ({x['start'][5:]} / {y['start'][5:]}).")
    al = one(con, "SELECT a.*, s.name site_name, s.kind site_kind, s.distance FROM alloc a LEFT JOIN sites s USING(site_id) WHERE pid=?", (pid,))
    if al:
        al["explain"] = jl(al["explain"], [])
        al["nights_label"] = T.mask_label(al["nights"])
        roommates = []
        if al["room_id"] and security.field_ok(req.role, "room"):
            for r in con.execute("SELECT a.pid, p.name, p.cid, a.nights FROM alloc a JOIN people p USING(pid) WHERE a.room_id=? AND a.pid!=?", (al["room_id"], pid)):
                roommates.append({"pid": r["pid"], "name": r["name"], "cid": r["cid"], "nights": T.mask_label(r["nights"])})
        al["roommates"] = roommates
        if not security.field_ok(req.role, "room"):
            al = {"status": al["status"], "site_name": al["site_name"], "masked": True}
    merch = rows(con, "SELECT id, item, size, ts, actor, desk, kind, voided, note FROM merch_issues WHERE pid=? ORDER BY id", (pid,))
    blocked = rows(con, "SELECT * FROM merch_blocked WHERE pid=? ORDER BY id", (pid,))
    issues = rows(con, "SELECT * FROM issues WHERE entity_id=? ORDER BY id", (pid,))
    dups = rows(con, "SELECT * FROM dup_candidates WHERE a=? OR b=?", (pid, pid))
    hist = rows(con, "SELECT seq, ts, actor, role, action, reason FROM audit WHERE entity_id=? ORDER BY seq DESC LIMIT 30", (pid,))
    diet_vendors = []
    if p["diet"] and security.field_ok(req.role, "dietary"):
        halal_veg = get_setting(con, "halal_accepts_vegetarian")
        for v in con.execute("SELECT * FROM vendors"):
            if food.eligible(p["diet"], food.vendor_tags(dict(v)), halal_veg):
                ok, why = food.vendor_status(dict(v), clock(con)[:10], "12:30", "14:30")
                diet_vendors.append({"vid": v["vid"], "name": v["name"], "menu": v["menu"], "ok_today": ok, "why": why})
    kit = get_setting(con, "kit_items")
    have = {x["item"] for x in merch if not x["voided"] and x["kind"] == "issue"}
    with LOCK:
        audit(con, req, "view", "person", pid, {"revealed": revealed})
        con.commit()
    return {"person": m, "contingent": c, "registrations": regs, "later_rounds": later, "flags": flags, "alloc": al,
            "merch": merch, "merch_blocked": blocked, "kit_pending": [k for k in kit if k not in have],
            "issues": issues, "duplicates": dups, "history": hist, "diet_vendors": diet_vendors, "revealed": revealed}


def _recent_break_glass(con, uid, pid):
    r = con.execute("SELECT ts FROM audit WHERE actor=? AND action='break_glass' AND entity_id=? ORDER BY seq DESC LIMIT 1", (uid, pid)).fetchone()
    return bool(r)


@route("POST", "/participants/{pid}/reveal", view="participants")
def reveal(req, pid):
    reason = reason_required(req, 10)
    con = db()
    with LOCK:
        audit(con, req, "break_glass", "person", pid, {"fields": ["contact", "accessibility", "dietary", "age"]}, reason)
        bump(con)
        con.commit()
    return {"ok": True}


@route("POST", "/participants/{pid}/checkin", view="participants")
def checkin(req, pid):
    need(req, "checkin")
    con = db()
    p = one(con, "SELECT * FROM people WHERE pid=?", (pid,))
    if not p:
        raise HTTPError(404, "No such person.")
    if p["status"] in ("merged", "cancelled"):
        raise HTTPError(409, f"{pid} is {p['status']}" + (f" into {p['merged_into']}" if p["merged_into"] else "") + ".")
    if p["checked_in"] and not req.body.get("undo"):
        raise HTTPError(409, f"Already checked in at {p['checked_in']}.")
    with LOCK:
        ts = None if req.body.get("undo") else clock(con)
        con.execute("UPDATE people SET checked_in=? WHERE pid=?", (ts, pid))
        audit(con, req, "checkin" if ts else "checkin_undo", "person", pid, {})
        bump(con)
        con.commit()
    return {"ok": True, "checked_in": ts}


EDITABLE = {"tshirt", "diet_raw", "access_raw", "gender_raw", "accom_req", "arrival", "departure", "contact_raw"}


@route("POST", "/participants/{pid}/edit", view="participants")
def edit_person(req, pid):
    need(req, "participant_edit")
    reason = reason_required(req)
    con = db()
    p = one(con, "SELECT * FROM people WHERE pid=?", (pid,))
    if not p:
        raise HTTPError(404, "No such person.")
    fields = {k: v for k, v in (req.body.get("fields") or {}).items() if k in EDITABLE}
    for f in fields:
        need_field = {"diet_raw": "dietary", "access_raw": "accessibility", "gender_raw": "gender", "contact_raw": "contact"}.get(f)
        if need_field and not security.field_ok(req.role, need_field):
            raise HTTPError(403, f"Your role cannot change {f}.")
    with LOCK:
        upd = {}
        if "tshirt" in fields:
            upd["tshirt"] = fields["tshirt"] or None
        if "diet_raw" in fields:
            upd["diet_raw"] = fields["diet_raw"]
            upd["diet"] = ingest.DIET_MAP.get((fields["diet_raw"] or "").lower(), "UNKNOWN")
        if "access_raw" in fields:
            upd["access_raw"] = fields["access_raw"]
            upd["access_need"] = ingest.ACCESS_MAP.get((fields["access_raw"] or "").lower(), "UNKNOWN")
        if "gender_raw" in fields:
            g = fields["gender_raw"] or ""
            cat, basis = ingest.GENDER_MAP.get(g.lower(), ("X", "Gender blank — NOT assumed."))
            upd.update({"gender_raw": g, "housing_cat": cat, "housing_basis": basis})
        if "accom_req" in fields:
            upd["accom_req"] = fields["accom_req"]
        if "contact_raw" in fields:
            from . import textmatch
            upd["contact_raw"] = fields["contact_raw"]
            upd["contact"] = textmatch.normalize_phone(fields["contact_raw"])
            upd["contact_ok"] = 1 if upd["contact"] and not textmatch.is_placeholder_phone(fields["contact_raw"]) else 0
        if "arrival" in fields or "departure" in fields:
            a = T.f(T.p(fields.get("arrival") or p["arrival"]))
            d = T.f(T.p(fields.get("departure") or p["departure"]))
            if not a or not d or T.p(d) <= T.p(a):
                raise HTTPError(400, "Departure must be after arrival.")
            upd.update({"arrival": a, "departure": d, "timing_ok": 1})
            if p["status"] == "needs_confirmation":
                upd["status"] = "active"
                con.execute("UPDATE issues SET status='resolved', resolution=?, resolved_by=? WHERE entity_id=? AND code='TIME_INVERTED'",
                            (f"times confirmed: {a} → {d}", req.uid, pid))
        if not upd:
            raise HTTPError(400, "Nothing to change.")
        before = {k: p.get(k) for k in upd}
        con.execute(f"UPDATE people SET {', '.join(k + '=?' for k in upd)} WHERE pid=?", list(upd.values()) + [pid])
        p2 = one(con, "SELECT * FROM people WHERE pid=?", (pid,))
        core, minh = get_setting(con, "night_core_window"), get_setting(con, "night_min_overlap_h")
        nights = T.nights_mask(p2["arrival"], p2["departure"], core, minh) if p2["accom_req"] != "no" and p2["timing_ok"] else 0
        con.execute("UPDATE people SET nights=? WHERE pid=?", (nights, pid))
        consequences = []
        housing_keys = {"gender_raw", "access_raw", "arrival", "departure", "accom_req"}
        if housing_keys & set(fields):
            con.execute("DELETE FROM alloc WHERE pid=?", (pid,))
            if nights and p2["status"] == "active":
                rep = accommodation.repair(con, {pid}, log=lambda *a: None)
                consequences.append("Re-housed: " + (f"{rep['replaced'][0]['room']}" if rep["replaced"] else "no bed available — on waitlist"))
            else:
                consequences.append("Bed released.")
        if {"arrival", "departure"} & set(fields):
            schedule.repair(con, seconds=2, log=lambda *a: None)
            consequences.append("Heats re-checked.")
        audit(con, req, "participant_edit", "person", pid, {"before": before, "after": upd}, reason)
        bump(con)
        con.commit()
    return {"ok": True, "consequences": consequences}


# ====================================================================== contingents
@route("GET", "/contingents")
def contingents(req):
    con = db()
    rs = rows(con, "SELECT * FROM contingents ORDER BY cid")
    stats = defaultdict(Counter)
    for r in con.execute("""SELECT p.cid, p.kind, p.status, a.status bed, p.arrival, p.checked_in FROM people p LEFT JOIN alloc a USING(pid)"""):
        s = stats[r["cid"]]
        if r["kind"] == "participant" and r["status"] != "merged":
            s["rows"] += 1
        if r["bed"] == "placed":
            s["placed"] += 1
        if r["bed"] == "unplaced":
            s["unplaced"] += 1
        if r["checked_in"]:
            s["checked_in"] += 1
    units = defaultdict(set)
    for r in con.execute("SELECT p.cid, s.block, s.site_id, s.kind FROM alloc a JOIN people p USING(pid) JOIN sites s USING(site_id) WHERE a.status='placed' AND p.kind='participant'"):
        units[r["cid"]].add(r["block"] if r["kind"] == "campus" else r["site_id"])
    for r in rs:
        r["prefs"] = jl(r["prefs"], [])
        r["stats"] = dict(stats[r["cid"]])
        r["units"] = sorted(units[r["cid"]])
        r["leader_matches"] = jl(r["leader_matches"], [])
    return {"rows": rs}


@route("GET", "/contingents/{cid}")
def contingent(req, cid):
    con = db()
    c = one(con, "SELECT * FROM contingents WHERE cid=?", (cid,))
    if not c:
        raise HTTPError(404, "No such contingent.")
    c["prefs"] = jl(c["prefs"], [])
    c["leader_matches"] = jl(c["leader_matches"], [])
    people = rows(con, """SELECT p.pid, p.name, p.kind, p.status, p.housing_cat, p.access_need, p.is_minor, p.arrival, p.departure, p.checked_in,
                          a.site_id, a.room_id, a.status bed FROM people p LEFT JOIN alloc a USING(pid) WHERE p.cid=? ORDER BY p.kind DESC, p.pid""", (cid,))
    room_ok = security.field_ok(req.role, "room")
    acc_ok = security.field_ok(req.role, "accessibility")
    for p in people:
        if not room_ok:
            p["room_id"] = None
        if not acc_ok:
            p["access_need"] = "SUPPORT" if p["access_need"] not in (None, "NONE") else "NONE"
    sites = Counter((p["site_id"] or "—") for p in people if p["bed"] == "placed")
    arrivals = Counter(p["arrival"] for p in people if p["status"] == "active")
    issues = rows(con, "SELECT * FROM issues WHERE entity_id=? OR entity_id IN (SELECT pid FROM people WHERE cid=?) ORDER BY severity", (cid, cid))
    return {"contingent": c, "people": people, "sites": dict(sites), "arrivals": dict(sorted(arrivals.items())),
            "issues": [i for i in issues if i["code"] not in ("MINOR",)][:200]}


# ====================================================================== data quality
@route("GET", "/quality", view="quality")
def quality(req):
    con = db()
    raw = con.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND source_row < 99999").fetchone()[0]
    dup_held = con.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND status IN ('held_duplicate','merged')").fetchone()[0]
    timing = con.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND timing_ok=0").fetchone()[0]
    noacc = con.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND status='active' AND accom_req='no'").fetchone()[0]
    acc = con.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND status='active' AND accom_req!='no' AND nights>0").fetchone()[0]
    acc0 = con.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND status='active' AND accom_req!='no' AND nights=0").fetchone()[0]
    staff = con.execute("SELECT COUNT(*) FROM people WHERE kind IN ('leader','escort') AND status='active'").fetchone()[0]
    declared = con.execute("SELECT SUM(declared) FROM contingents").fetchone()[0]
    days = {}
    for d in ["2026-10-30", "2026-10-31", "2026-11-01"]:
        days[d] = con.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND status='active' AND timing_ok=1 AND arrival<=? AND departure>=?",
                              (d + " 22:00", d + " 08:00")).fetchone()[0]
    peak = 0
    peak_t = None
    t = T.p("2026-10-29 06:00")
    while t <= T.p("2026-11-03 22:00"):
        n = con.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND status='active' AND timing_ok=1 AND arrival<=? AND departure>?", (T.f(t), T.f(t))).fetchone()[0]
        if n > peak:
            peak, peak_t = n, T.f(t)
        t += timedelta(hours=1)
    waterfall = [
        {"label": "Raw participant rows", "value": raw},
        {"label": "Probable duplicates held / merged", "value": -dup_held},
        {"label": "Unique people", "value": raw - dup_held, "total": True},
        {"label": "Impossible times (held, not deleted)", "value": -timing},
        {"label": "Plannable participants", "value": raw - dup_held - timing, "total": True},
        {"label": "Day participants (no bed requested)", "value": -noacc},
        {"label": "Asked for a bed but on site < 3h of any night", "value": -acc0},
        {"label": "Participants needing a bed", "value": acc, "total": True},
        {"label": "+ faculty escorts & unregistered leaders", "value": staff},
        {"label": "People needing a bed", "value": acc + staff, "total": True},
    ]
    issues = rows(con, "SELECT code, severity, entity, COUNT(*) n, MIN(message) example FROM issues WHERE status='open' GROUP BY code, severity, entity ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, n DESC")
    dups = rows(con, "SELECT * FROM dup_candidates ORDER BY id")
    for d in dups:
        d["evidence"] = jl(d["evidence"], {})
        for k in ("a", "b"):
            p = one(con, "SELECT * FROM people WHERE pid=?", (d[k],))
            d[k + "_person"] = security.mask_person(p, req.role) if p else None
    mism = rows(con, "SELECT cid, college, declared, actual FROM contingents WHERE declared != actual ORDER BY ABS(declared-actual) DESC")
    return {"waterfall": waterfall, "narrative": 2400, "declared_total": declared, "days": days, "peak": peak, "peak_time": peak_t,
            "issues": issues, "duplicates": dups, "strength_mismatch": mism,
            "namesake_pairs": 13019, "assumptions": _assumptions(con)}


@route("GET", "/quality/issues", view="quality")
def quality_issues(req):
    con = db()
    code = req.q("code")
    rs = rows(con, "SELECT * FROM issues WHERE code=? ORDER BY id LIMIT 500", (code,)) if code else rows(con, "SELECT * FROM issues ORDER BY id DESC LIMIT 300")
    return {"rows": rs}


@route("POST", "/quality/dups/{did}/{action}", view="quality")
def dup_action(req, did, action):
    need(req, "dedup")
    reason = reason_required(req)
    con = db()
    d = one(con, "SELECT * FROM dup_candidates WHERE id=?", (did,))
    if not d or d["status"] != "open":
        raise HTTPError(404, "Candidate not open.")
    with LOCK:
        now = clock(con)
        if action == "merge":
            res = entity.merge(con, d["a"], d["b"], req.uid, reason, now)
            schedule.repair(con, seconds=2, log=lambda *a: None)
        elif action == "reject":
            entity.reject(con, d["a"], d["b"], req.uid, reason, now)
            accommodation.repair(con, {d["b"]}, log=lambda *a: None)
            res = {"released": d["b"]}
        else:
            raise HTTPError(400, "merge or reject")
        audit(con, req, f"dedup_{action}", "person", d["b"], {"keep": d["a"], "dup": d["b"], **res}, reason)
        bump(con)
        con.commit()
    return {"ok": True, "result": res}


@route("POST", "/import/participants", view="quality")
def import_rows(req):
    """Validate (and optionally commit) new participant rows pasted as CSV."""
    need(req, "import")
    text = req.body.get("csv") or ""
    commit = bool(req.body.get("commit"))
    con = db()
    rd = list(csv.DictReader(io.StringIO(text)))
    required = ["participant_id", "name", "college", "contingent_id", "gender_as_given", "age", "contact", "accessibility_needs",
                "dietary_needs", "arrival_datetime", "departure_datetime", "accommodation_requested", "tshirt_size"]
    if not rd:
        raise HTTPError(400, "No rows found. Paste CSV with the participants.csv header.")
    missing = [c for c in required if c not in rd[0]]
    if missing:
        raise HTTPError(400, f"Missing columns: {', '.join(missing)}")
    report = []
    for r in rd:
        probs = []
        if one(con, "SELECT 1 FROM people WHERE pid=?", (r["participant_id"],)):
            probs.append("ID already exists")
        if not one(con, "SELECT 1 FROM contingents WHERE cid=?", (r["contingent_id"],)):
            probs.append("unknown contingent")
        a, d = T.p(r["arrival_datetime"]), T.p(r["departure_datetime"])
        if not a or not d or d <= a:
            probs.append("arrival/departure invalid")
        report.append({"pid": r["participant_id"], "name": r["name"], "problems": probs})
    if commit:
        with LOCK:
            now = clock(con)
            led = ingest.Ledger(con, now)
            ok = [r for r, rep in zip(rd, report) if "ID already exists" not in rep["problems"] and "unknown contingent" not in rep["problems"]]
            # reuse the ingest normaliser on a temp CSV
            import tempfile, os
            with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=required)
                w.writeheader()
                for r in ok:
                    w.writerow({k: r[k] for k in required})
                tmp = fh.name
            before = {r[0] for r in con.execute("SELECT pid FROM people")}
            _ingest_people_file(con, led, tmp)
            os.unlink(tmp)
            new = [r[0] for r in con.execute("SELECT pid FROM people") if r[0] not in before]
            found, _ = entity.find_duplicates(con, now, only_pids=set(new))
            free = [pid for pid in new if one(con, "SELECT status FROM people WHERE pid=?", (pid,))["status"] == "active"]
            if free:
                accommodation.repair(con, set(free), log=lambda *a: None)
            audit(con, req, "import", "people", ",".join(new), {"rows": len(new), "duplicates": len(found)}, req.body.get("reason") or "late registration import")
            bump(con)
            con.commit()
        return {"committed": len(new), "new": new, "duplicates": [{"keep": k, "dup": d, "score": s} for k, d, s, _ in found], "report": report}
    return {"report": report}


def _ingest_people_file(con, led, path):
    rows_ = ingest.read_csv(None, path)
    core = get_setting(con, "night_core_window")
    minh = get_setting(con, "night_min_overlap_h")
    from . import textmatch
    for i, r in enumerate(rows_, start=90000):
        g = (r["gender_as_given"] or "").strip()
        cat, basis = ingest.GENDER_MAP.get(g.lower(), ("X", "Gender blank — NOT assumed."))
        try:
            age = int(r["age"])
        except ValueError:
            age = None
        cn = textmatch.normalize_phone(r["contact"])
        acc = ingest.ACCESS_MAP.get((r["accessibility_needs"] or "").strip().lower(), "UNKNOWN")
        diet = ingest.DIET_MAP.get((r["dietary_needs"] or "").strip().lower(), "UNKNOWN")
        a, d = T.p(r["arrival_datetime"]), T.p(r["departure_datetime"])
        tok = 1 if (a and d and d > a) else 0
        ar = (r["accommodation_requested"] or "").strip().lower()
        ar = ar if ar in ("yes", "no") else "unknown"
        nights = T.nights_mask(a, d, core, minh) if tok and ar != "no" else 0
        con.execute("""INSERT INTO people(pid,kind,name,college,cid,gender_raw,housing_cat,housing_basis,age_raw,age,is_minor,
            contact_raw,contact,contact_ok,access_raw,access_need,diet_raw,diet,arrival,departure,arrival_orig,timing_ok,
            accom_req,tshirt,status,nights,source_row) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r["participant_id"], "participant", r["name"], r["college"], r["contingent_id"], g, cat, basis, r["age"], age,
                     1 if (age is None or age < 18) else 0, r["contact"], cn, 1 if cn and not textmatch.is_placeholder_phone(r["contact"]) else 0,
                     r["accessibility_needs"], acc, r["dietary_needs"], diet, T.f(a), T.f(d), T.f(a), tok, ar,
                     (r["tshirt_size"] or "").upper() or None, "active" if tok else "needs_confirmation", nights, i))
        led.add("participant", r["participant_id"], "LATE_IMPORT", "info", "Imported during the fest.")


# ====================================================================== accommodation
@route("GET", "/accommodation", view="accommodation")
def accommodation_view(req):
    con = db()
    m = get_setting(con, "accommodation_metrics", {})
    fr = get_setting(con, "accommodation_frontier", [])
    sites = rows(con, "SELECT * FROM sites ORDER BY kind, site_id")
    load = defaultdict(lambda: [0] * T.N_NIGHTS)
    cats = defaultdict(Counter)
    for r in con.execute("SELECT a.site_id, a.nights, p.housing_cat, p.cid FROM alloc a JOIN people p USING(pid) WHERE a.status='placed'"):
        for n in T.mask_list(r["nights"]):
            load[r["site_id"]][n] += 1
        cats[r["site_id"]][r["cid"]] += 1
    for s in sites:
        s["load"] = load[s["site_id"]]
        s["contingents"] = dict(cats[s["site_id"]].most_common(6))
    unplaced = rows(con, """SELECT a.pid, a.nights, a.explain, p.name, p.cid, p.kind, p.housing_cat, p.access_need, p.is_minor, p.arrival
                            FROM alloc a JOIN people p USING(pid) WHERE a.status='unplaced' ORDER BY p.cid""")
    acc_ok = security.field_ok(req.role, "accessibility")
    for u in unplaced:
        u["explain"] = jl(u["explain"], [])
        u["nights_label"] = T.mask_label(u["nights"])
        if not acc_ok:
            u["access_need"] = "SUPPORT" if u["access_need"] not in ("NONE", None) else "NONE"
    ov = rows(con, "SELECT * FROM overrides WHERE kind='alloc' ORDER BY id DESC")
    verified = get_setting(con, "external_accessible_beds_verified") or {}
    early_conflicts = _early_checkins(con)
    return {"metrics": m, "frontier": fr, "frontier_running": FRONTIER_RUNNING["on"], "sites": sites, "unplaced": unplaced,
            "overrides": ov, "verified": verified, "budget": get_setting(con, "budget_external_inr"),
            "nights": T.NIGHT_LABELS, "early_checkins": early_conflicts,
            "weights": {k: get_setting(con, k) for k in ("split_penalty_inr", "floor_spread_penalty_inr", "unplaced_penalty_inr",
                                                         "early_event_km_penalty_inr", "near_venue_km_penalty_inr", "minor_external_penalty_inr",
                                                         "designation_penalty_inr", "max_all_gender_floors")}}


def _early_checkins(con):
    """Arrivals before the standard check-in time into a bed someone vacates that morning."""
    std = get_setting(con, "standard_checkin")
    by_bed = defaultdict(list)
    for r in con.execute("SELECT a.room_id, a.bed, p.arrival, p.departure, p.pid FROM alloc a JOIN people p USING(pid) WHERE a.status='placed'"):
        by_bed[(r["room_id"], r["bed"])].append(dict(r))
    out = Counter()
    for lst in by_bed.values():
        lst.sort(key=lambda x: x["arrival"])
        for a, b in zip(lst, lst[1:]):
            if b["arrival"][:10] == a["departure"][:10] and b["arrival"][11:16] < std:
                out[b["arrival"][:10]] += 1
    early_all = con.execute("SELECT COUNT(*) FROM people p JOIN alloc a USING(pid) WHERE a.status='placed' AND substr(p.arrival,12,5) < ?", (std,)).fetchone()[0]
    return {"turnover_conflicts": dict(out), "arrive_before_checkin": early_all}


@route("GET", "/accommodation/site/{sid}", view="accommodation")
def site_detail(req, sid):
    con = db()
    s = one(con, "SELECT * FROM sites WHERE site_id=?", (sid,))
    if not s:
        raise HTTPError(404, "No such site.")
    occ = defaultdict(list)
    for r in con.execute("""SELECT a.room_id, a.bed, a.nights, a.pid, p.name, p.cid, p.kind, p.housing_cat, p.access_need, p.is_minor
                            FROM alloc a JOIN people p USING(pid) WHERE a.site_id=? AND a.status='placed' ORDER BY a.room_id, a.bed""", (sid,)):
        d = dict(r)
        d["nights_list"] = T.mask_list(r["nights"])
        if not security.field_ok(req.role, "accessibility"):
            d["access_need"] = "SUPPORT" if d["access_need"] not in ("NONE", None) else "NONE"
        occ[r["room_id"]].append(d)
    if s["kind"] == "campus":
        rooms = rows(con, "SELECT * FROM rooms WHERE site_id=? ORDER BY room_id", (sid,))
    else:
        rooms = [{"room_id": rid, "capacity": get_setting(con, "external_room_size"), "accessible": 0, "status": "available"} for rid in sorted(occ)]
    for r in rooms:
        r["occupants"] = occ.get(r["room_id"], [])
    return {"site": s, "rooms": rooms, "nights": T.NIGHT_LABELS}


@route("POST", "/accommodation/rerun", view="accommodation")
def acc_rerun(req):
    need(req, "alloc_override")
    reason = reason_required(req)
    con = db()
    with LOCK:
        prm = req.body.get("params") or {}
        if prm.get("budget") and int(prm["budget"]) > get_setting(con, "budget_external_inr") and req.role != "director":
            raise HTTPError(403, "Only the Festival Director can plan above budget.")
        m, *_ = accommodation.run(con, prm, log=lambda *a: None)
        audit(con, req, "plan_accommodation", "accommodation", "rerun", {"params": prm, "placed": m["placed"]}, reason)
        bump(con)
        con.commit()
    _frontier_async()
    return {"metrics": m}


@route("POST", "/accommodation/adopt", view="accommodation")
def acc_adopt(req):
    need(req, "alloc_override")
    reason = reason_required(req)
    con = db()
    key = req.body.get("key")
    fr = {f["key"]: f for f in get_setting(con, "accommodation_frontier", [])}
    if key not in fr:
        raise HTTPError(404, "Unknown plan.")
    prm = dict(fr[key]["params"])
    if prm.get("budget", 0) > get_setting(con, "budget_external_inr"):
        if req.role != "director":
            raise HTTPError(403, "This plan exceeds the budget: only the Festival Director can adopt it.")
    with LOCK:
        if prm.get("budget", 0) > get_setting(con, "budget_external_inr"):
            con.execute("INSERT INTO overrides(ts,actor,kind,entity_id,detail,risk,violations,reason) VALUES(?,?,?,?,?,?,?,?)",
                        (clock(con), req.uid, "budget", key, json.dumps({"budget": prm["budget"]}), "high", json.dumps(["budget exceeded"]), reason))
        m, *_ = accommodation.run(con, prm, log=lambda *a: None)
        audit(con, req, "adopt_plan", "accommodation", key, {"params": prm, "placed": m["placed"]}, reason)
        bump(con)
        con.commit()
    return {"metrics": m}


@route("POST", "/accommodation/move", view="accommodation")
def acc_move(req):
    need(req, "alloc_override")
    con = db()
    pid, sid, rid = req.body.get("pid"), req.body.get("site_id"), req.body.get("room_id")
    if not one(con, "SELECT 1 FROM sites WHERE site_id=?", (sid,)):
        raise HTTPError(400, "Unknown site.")
    p = one(con, "SELECT * FROM people WHERE pid=?", (pid,))
    if not p:
        raise HTTPError(404, "No such person.")
    if not rid:
        # pick a free bed in the site for all nights
        rid, bed = _free_bed(con, sid, p)
    else:
        bed = None
    viol = accommodation.check_move(con, pid, sid, rid)
    high = [v for v in viol if v[0] == "high"]
    if req.body.get("dry_run"):
        return {"violations": viol, "room_id": rid}
    reason = reason_required(req)
    if high and req.role != "director":
        raise HTTPError(403, "This move breaks a hard rule — only the Festival Director can approve it, with a reason.", {"violations": viol})
    if high and len(reason) < 20:
        raise HTTPError(400, "High-risk override: explain in at least 20 characters why this is acceptable.", {"violations": viol, "need_reason": True})
    with LOCK:
        if not rid:
            raise HTTPError(409, "No free bed for every night of this stay at that site.")
        if bed is None:
            rid2, bed = _free_bed(con, sid, p, room=rid)
            if bed is None:
                raise HTTPError(409, f"No free bed in {rid} for every night.")
        s = one(con, "SELECT * FROM sites WHERE site_id=?", (sid,))
        cost = s["cost"] * T.popcount(p["nights"])
        con.execute("INSERT OR REPLACE INTO alloc(pid,site_id,room_id,bed,nights,cost,status,explain,source) VALUES(?,?,?,?,?,?,?,?,?)",
                    (pid, sid, rid, bed, p["nights"], cost, "placed",
                     json.dumps([f"Manual placement by {req.uid}: {reason}"] + [f"⚠ {v[1]}" for v in viol]), "override"))
        if viol:
            con.execute("INSERT INTO overrides(ts,actor,kind,entity_id,detail,risk,violations,reason) VALUES(?,?,?,?,?,?,?,?)",
                        (clock(con), req.uid, "alloc", pid, json.dumps({"site": sid, "room": rid}), "high" if high else "low",
                         json.dumps([v[1] for v in viol]), reason))
        audit(con, req, "alloc_override", "person", pid, {"site": sid, "room": rid, "violations": [v[1] for v in viol]}, reason)
        bump(con)
        con.commit()
    return {"ok": True, "violations": viol, "room_id": rid, "bed": bed}


def _free_bed(con, sid, p, room=None):
    s = one(con, "SELECT * FROM sites WHERE site_id=?", (sid,))
    if s["kind"] == "campus":
        rl = rows(con, "SELECT room_id, capacity FROM rooms WHERE site_id=? AND status='available'" + (" AND room_id=?" if room else "") + " ORDER BY room_id",
                  (sid, room) if room else (sid,))
    else:
        size = get_setting(con, "external_room_size")
        import math
        rl = [{"room_id": f"{sid}-R{i+1:03d}", "capacity": min(size, s["beds"] - i * size)} for i in range(math.ceil(s["beds"] / size))]
        if room:
            rl = [r for r in rl if r["room_id"] == room]
    for r in rl:
        used = defaultdict(int)
        for a in con.execute("SELECT bed, nights FROM alloc WHERE room_id=? AND status='placed' AND pid!=?", (r["room_id"], p["pid"])):
            used[a["bed"]] |= a["nights"]
        for b in range(1, r["capacity"] + 1):
            if not used[b] & p["nights"]:
                return r["room_id"], b
    return (room, None) if room else (None, None)


@route("POST", "/accommodation/verify", view="accommodation")
def acc_verify(req):
    need(req, "verify_property")
    reason = reason_required(req)
    con = db()
    sid = req.body.get("site_id")
    n = int(req.body.get("accessible_beds", 0))
    with LOCK:
        v = dict(get_setting(con, "external_accessible_beds_verified") or {})
        v[sid] = n
        set_setting(con, "external_accessible_beds_verified", v)
        con.execute("UPDATE sites SET acc_beds=? WHERE site_id=?", (n, sid))
        waiting = {r[0] for r in con.execute("SELECT a.pid FROM alloc a JOIN people p USING(pid) WHERE a.status='unplaced'")}
        rep = accommodation.repair(con, waiting, log=lambda *a: None)
        bres = accommodation.bump_for_hard_needs(con, log=lambda *a: None)
        con.execute("UPDATE issues SET status='resolved', resolution=?, resolved_by=? WHERE entity_id=? AND code='ACCESSIBILITY_UNVERIFIED'",
                    (f"{n} accessible beds verified: {reason}", req.uid, sid))
        audit(con, req, "verify_property", "site", sid, {"accessible_beds": n, "housed": len(rep["replaced"]),
                                                          "hard_need_placed": len(bres["hard_need_placed"]), "evicted": len(bres["evicted"])}, reason)
        _refresh_acc_metrics(con)
        bump(con)
        con.commit()
    still = con.execute("SELECT COUNT(*) FROM alloc WHERE status='unplaced'").fetchone()[0]
    return {"ok": True, "housed": len(rep["replaced"]), "hard_need_placed": len(bres["hard_need_placed"]),
            "evicted": len(bres["evicted"]), "evicted_rehoused": bres["evicted_rehoused"], "still_unplaced": still}


@route("POST", "/accommodation/property", view="accommodation")
def acc_property(req):
    """Contract an extra external property (emergency capacity)."""
    if req.role != "director":
        raise HTTPError(403, "Only the Festival Director can contract a new property.")
    reason = reason_required(req)
    con = db()
    name = (req.body.get("name") or "").strip()
    beds, cost, dist = int(req.body.get("beds", 0)), int(req.body.get("cost", 0)), float(req.body.get("distance", 5))
    if not name or beds <= 0 or cost < 0:
        raise HTTPError(400, "Name, beds and cost are required.")
    with LOCK:
        n = con.execute("SELECT COUNT(*) FROM sites WHERE kind='external'").fetchone()[0]
        sid = f"EXT{n+1:02d}"
        con.execute("""INSERT INTO sites(site_id,kind,name,block,floor,base_gender,beds,acc_beds,cost,distance,transport,designation)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (sid, "external", name, None, None, "ANY", beds,
                                                            int(req.body.get("accessible_beds", 0)), cost, dist, req.body.get("transport", "yes"), "ANY"))
        if int(req.body.get("accessible_beds", 0)):
            v = dict(get_setting(con, "external_accessible_beds_verified") or {})
            v[sid] = int(req.body["accessible_beds"])
            set_setting(con, "external_accessible_beds_verified", v)
        extra = int(req.body.get("budget_increase", 0))
        if extra:
            set_setting(con, "budget_external_inr", get_setting(con, "budget_external_inr") + extra)
            con.execute("INSERT INTO overrides(ts,actor,kind,entity_id,detail,risk,violations,reason) VALUES(?,?,?,?,?,?,?,?)",
                        (clock(con), req.uid, "budget", sid, json.dumps({"increase": extra}), "high", json.dumps([f"budget raised by ₹{extra:,}"]), reason))
        waiting = {r[0] for r in con.execute("SELECT pid FROM alloc WHERE status='unplaced'")}
        rep = accommodation.repair(con, waiting, log=lambda *a: None)
        audit(con, req, "property_added", "site", sid, {"name": name, "beds": beds, "cost": cost, "housed": len(rep["replaced"]), "budget_increase": extra}, reason)
        _refresh_acc_metrics(con)
        bump(con)
        con.commit()
    _frontier_async()
    return {"ok": True, "site_id": sid, "housed": len(rep["replaced"]), "still_unplaced": len(rep["unplaced"])}


@route("POST", "/accommodation/floors", view="accommodation")
def acc_floors(req):
    if req.role != "director":
        raise HTTPError(403, "Only the Festival Director approves floor designations.")
    reason = reason_required(req)
    con = db()
    with LOCK:
        approved = req.body.get("approve_all")
        if approved:
            con.execute("UPDATE sites SET designation_locked=1 WHERE kind='campus' AND designation='X'")
        else:
            sid, d = req.body["site_id"], req.body["designation"]
            con.execute("UPDATE sites SET designation=?, designation_locked=? WHERE site_id=?", (d, 1 if req.body.get("lock", True) else 0, sid))
        audit(con, req, "floor_designation", "site", req.body.get("site_id") or "all-gender", req.body, reason)
        if not approved:
            accommodation.run(con, {}, log=lambda *a: None)
        bump(con)
        con.commit()
    return {"ok": True}


@route("POST", "/accommodation/budget", view="accommodation")
def acc_budget(req):
    if req.role != "director":
        raise HTTPError(403, "Only the Festival Director can change the budget.")
    reason = reason_required(req)
    con = db()
    with LOCK:
        old = get_setting(con, "budget_external_inr")
        set_setting(con, "budget_external_inr", int(req.body["budget"]))
        m, *_ = accommodation.run(con, {}, log=lambda *a: None)
        audit(con, req, "budget_change", "setting", "budget_external_inr", {"from": old, "to": int(req.body["budget"]), "placed": m["placed"]}, reason)
        bump(con)
        con.commit()
    _frontier_async()
    return {"metrics": m}


def _refresh_acc_metrics(con):
    people, sites, conts, early = accommodation.load(con)
    base = get_setting(con, "accommodation_metrics", {})
    m = accommodation.metrics_from_db(con, {"status": base.get("solver", ""), "solve_seconds": base.get("solve_seconds", 0),
                                            "n_vars": base.get("n_vars", 0), "n_rows": base.get("n_rows", 0),
                                            "budget": get_setting(con, "budget_external_inr"), "designation": {}}, sites, conts)
    set_setting(con, "accommodation_metrics", m)


def _frontier_async():
    if FRONTIER_RUNNING["on"]:
        return

    def go():
        FRONTIER_RUNNING["on"] = True
        try:
            con = db()
            mem = __import__("festos.db", fromlist=["clone_to_memory"]).clone_to_memory(con)
            fr = accommodation.frontier(mem, log=lambda *a: None)
            with LOCK:
                set_setting(con, "accommodation_frontier", fr)
                con.commit()
        finally:
            FRONTIER_RUNNING["on"] = False
    threading.Thread(target=go, daemon=True).start()


# ====================================================================== schedule
@route("GET", "/schedule", view="schedule")
def schedule_view(req):
    con = db()
    heats = rows(con, "SELECT h.*, e.name ev, e.type etype, e.volunteers FROM heats h JOIN events e USING(eid) ORDER BY h.start")
    cnt = Counter(r["heat_id"] for r in con.execute("SELECT heat_id FROM registrations WHERE heat_id IS NOT NULL"))
    for h in heats:
        h["members"] = cnt.get(h["hid"], 0)
    venues = rows(con, "SELECT * FROM venues ORDER BY vid")
    for v in venues:
        v["windows"] = jl(v["windows"], [])
        v["blocks"] = jl(v["blocks"], [])
        v["caps"] = jl(v["caps"], [])
    events = rows(con, "SELECT * FROM events ORDER BY eid")
    regc = Counter(r[0] for r in con.execute("SELECT eid FROM registrations"))
    jud = defaultdict(list)
    for j in con.execute("SELECT * FROM judges"):
        for e in jl(j["events"], []):
            jud[e].append({"jid": j["jid"], "name": j["name"], "sample": j["sample"]})
    for e in events:
        for k in ("rounds", "reqs", "equipment", "eligible_venues", "flags", "waived_equipment"):
            e[k] = jl(e[k], [])
        e["registrations"] = regc.get(e["eid"], 0)
        e["judges"] = jud[e["eid"]]
    conf = conflicts(con)
    vol, pool = schedule.volunteer_curve(con)
    meta = get_setting(con, "schedule_meta", {})
    late = con.execute("SELECT COUNT(*) FROM registrations r JOIN people p USING(pid) WHERE p.arrival >= '2026-10-31 20:00' AND p.status='active'").fetchone()[0]
    return {"heats": heats, "venues": venues, "events": events, "conflicts": conf, "volunteer_curve": vol, "volunteer_pool": pool,
            "meta": meta, "late_arrival_regs": late, "slot": schedule.SLOT, "base": "2026-10-30 00:00"}


@route("GET", "/schedule/heat/{hid}", view="schedule")
def heat_detail(req, hid):
    con = db()
    h = one(con, "SELECT h.*, e.name ev, e.equipment, e.waived_equipment, e.volunteers, v.name venue, v.capacity vcap FROM heats h JOIN events e USING(eid) JOIN venues v USING(vid) WHERE hid=?", (hid,))
    if not h:
        raise HTTPError(404, "No such heat.")
    mem = rows(con, "SELECT r.pid, p.name, p.cid, r.team_id, p.arrival, p.departure FROM registrations r JOIN people p USING(pid) WHERE r.heat_id=? ORDER BY p.cid, p.name", (hid,))
    for m in mem:
        m["present"] = T.p(m["arrival"]) <= T.p(h["start"]) and T.p(m["departure"]) >= T.p(h["end"])
    judges = [dict(j) for j in con.execute("SELECT * FROM judges") if h["eid"] in jl(j["events"], [])]
    eq = jl(h["equipment"], {})
    for w in jl(h["waived_equipment"], []):
        eq.pop(w, None)
    res = [r for r in inventory.equipment_plan(con)["reservations"] if r["hid"] == hid]
    conf = [c for c in conflicts(con) if hid in (c.get("refs") or [])]
    return {"heat": h, "members": mem, "judges": judges, "equipment": eq, "reservations": res, "conflicts": conf}


@route("POST", "/schedule/heat/{hid}/move", view="schedule")
def heat_move(req, hid):
    need(req, "schedule_edit")
    con = db()
    h = one(con, "SELECT * FROM heats WHERE hid=?", (hid,))
    if not h:
        raise HTTPError(404, "No such heat.")
    vid = req.body.get("vid") or h["vid"]
    start = T.p(req.body.get("start") or h["start"])
    dur = int((T.p(h["end"]) - T.p(h["start"])).total_seconds() // 60)
    end = start + timedelta(minutes=dur)
    mem = clone = __import__("festos.db", fromlist=["clone_to_memory"]).clone_to_memory(con)
    clone.execute("UPDATE heats SET vid=?, start=?, end=?, locked=1 WHERE hid=?", (vid, T.f(start), T.f(end), hid))
    before = Counter(c["kind"] for c in conflicts(con) for _ in range(c["count"]))
    after_list = schedule.validate(clone)
    after = Counter(c["kind"] for c in after_list for _ in range(c["count"]))
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after) if after.get(k, 0) != before.get(k, 0)}
    new_conf = [c for c in after_list if hid in (c.get("refs") or [])]
    if req.body.get("dry_run"):
        return {"delta": delta, "conflicts": new_conf}
    reason = reason_required(req)
    hard = [c for c in new_conf if c["severity"] == "critical"]
    if hard and req.role != "director":
        raise HTTPError(403, "This move creates a critical conflict — only the Festival Director can force it.", {"conflicts": new_conf})
    with LOCK:
        con.execute("UPDATE heats SET vid=?, start=?, end=?, locked=1 WHERE hid=?", (vid, T.f(start), T.f(end), hid))
        rep = schedule.repair(con, seconds=3, log=lambda *a: None)
        if hard:
            con.execute("INSERT INTO overrides(ts,actor,kind,entity_id,detail,risk,violations,reason) VALUES(?,?,?,?,?,?,?,?)",
                        (clock(con), req.uid, "schedule", hid, json.dumps({"vid": vid, "start": T.f(start)}), "high", json.dumps([c["message"] for c in hard]), reason))
        audit(con, req, "heat_move", "heat", hid, {"from": [h["vid"], h["start"]], "to": [vid, T.f(start)], "reassigned": len(rep["participants_reassigned"])}, reason)
        bump(con)
        con.commit()
    return {"ok": True, "delta": delta, "reassigned": len(rep["participants_reassigned"]), "moved_heats": rep["moved_heats"]}


@route("POST", "/schedule/repair", view="schedule")
def sched_repair(req):
    need(req, "schedule_edit")
    reason = reason_required(req)
    con = db()
    with LOCK:
        rep = schedule.repair(con, seconds=6, log=lambda *a: None)
        audit(con, req, "schedule_repair", "schedule", "all", {"moved": len(rep["moved_heats"]), "reassigned": len(rep["participants_reassigned"])}, reason)
        bump(con)
        con.commit()
    return rep


@route("POST", "/events/{eid}/waive", view="schedule")
def waive(req, eid):
    need(req, "schedule_edit")
    reason = reason_required(req, 15)
    con = db()
    e = one(con, "SELECT * FROM events WHERE eid=?", (eid,))
    item = req.body.get("item")
    with LOCK:
        w = jl(e["waived_equipment"], [])
        if item not in w:
            w.append(item)
        con.execute("UPDATE events SET waived_equipment=? WHERE eid=?", (json.dumps(w), eid))
        con.execute("INSERT INTO overrides(ts,actor,kind,entity_id,detail,risk,violations,reason) VALUES(?,?,?,?,?,?,?,?)",
                    (clock(con), req.uid, "equipment_waiver", eid, json.dumps({"item": item}), "low", json.dumps([f"{item} requirement waived"]), reason))
        audit(con, req, "equipment_waiver", "event", eid, {"item": item}, reason)
        bump(con)
        con.commit()
    return {"ok": True}


# ====================================================================== inventory
@route("GET", "/inventory", view="inventory")
def inventory_view(req):
    con = db()
    st = inventory.stock_summary(con)
    plan = cached(con, "eqplan", lambda: inventory.equipment_plan(con))
    curves = schedule.equipment_curve(con)
    peaks = {k: max(v) for k, v in curves.items()}
    items = []
    for item, d in sorted(st.items()):
        items.append({"item": item, "available": d.get("available", 0), "inspect": d.get("inspect", 0), "repair": d.get("repair", 0),
                      "transit": d.get("transit", 0), "peak_need": peaks.get(item, 0), "lines": d["lines"]})
    moves = rows(con, "SELECT * FROM stock_moves ORDER BY id DESC LIMIT 50")
    by_loc = defaultdict(lambda: defaultdict(int))
    for r in con.execute("SELECT location, state, SUM(qty) q FROM logistics GROUP BY location, state"):
        by_loc[r["location"]][r["state"]] = r["q"]
    return {"items": items, "plan": {"moves": plan["moves"][:400], "shortages": plan["shortages"], "n_moves": len(plan["moves"])},
            "log": moves, "by_location": by_loc}


@route("POST", "/inventory/line/{line_id}/state", view="inventory")
def stock_state(req, line_id):
    need(req, "inventory_edit")
    reason = reason_required(req, 5)
    con = db()
    st = req.body.get("state")
    if st not in ("available", "inspect", "repair", "transit", "reserved"):
        raise HTTPError(400, "bad state")
    with LOCK:
        old, nid, q = inventory.set_state(con, line_id, int(req.body.get("qty", 0)), st, req.body.get("to_loc"))
        con.execute("INSERT INTO stock_moves(ts,line_id,item,qty,from_loc,to_loc,from_state,to_state,actor,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (clock(con), nid, old["item"], q, old["location"], req.body.get("to_loc") or old["location"], old["state"], st, req.uid, reason))
        audit(con, req, "stock_change", "stock", nid, {"item": old["item"], "qty": q, "from": old["state"], "to": st}, reason)
        bump(con)
        con.commit()
    return {"ok": True, "line": nid}


# ====================================================================== merchandise
@route("GET", "/merch", view="merch")
def merch_view(req):
    con = db()
    st = merch_status(con)
    recent = rows(con, "SELECT m.*, p.name FROM merch_issues m JOIN people p USING(pid) ORDER BY m.id DESC LIMIT 40")
    blocked = rows(con, "SELECT b.*, p.name, m.ts orig_ts, m.actor orig_actor, m.desk orig_desk FROM merch_blocked b JOIN people p USING(pid) LEFT JOIN merch_issues m ON m.id=b.original_issue ORDER BY b.id DESC")
    return {**st, "recent": recent, "blocked": blocked}


@route("POST", "/merch/issue", view="merch")
def merch_issue(req):
    need(req, "merch_issue")
    con = db()
    with LOCK:
        res = inventory.issue(con, (req.body.get("pid") or "").strip().upper(), req.body.get("item"), req.body.get("size"),
                              req.uid, req.body.get("desk") or "Desk 1", clock(con), allow_sub=bool(req.body.get("allow_sub")))
        audit(con, req, "merch_issue" if res["ok"] else "merch_blocked", "person", req.body.get("pid"),
              {"item": req.body.get("item"), "result": res.get("code", "ok")}, None)
        bump(con)
        con.commit()
    return res


# ====================================================================== food
@route("GET", "/food", view="food")
def food_view(req):
    con = db()
    fs = food_summary(con)
    vt = cached(con, "vendors", lambda: food.vendor_table(con, fs["meals"]))
    return {**fs, "vendors": vt, "labels": food.DIET_LABEL, "priority": food.PRIORITY, "safety": sorted(food.SAFETY)}


@route("POST", "/food/vendor/{vid}", view="food")
def vendor_edit(req, vid):
    need(req, "vendor_edit")
    reason = reason_required(req, 5)
    con = db()
    with LOCK:
        upd = {}
        for k in ("cert_renewed", "rate"):
            if k in req.body:
                upd[k] = req.body[k]
        if req.body.get("reopen"):
            upd["shutdown_from"] = None
            upd["shutdown_reason"] = None
        if req.body.get("add_diet"):
            v = one(con, "SELECT diets FROM vendors WHERE vid=?", (vid,))
            ds = jl(v["diets"], [])
            if req.body["add_diet"] not in ds:
                ds.append(req.body["add_diet"])
            upd["diets"] = json.dumps(ds)
            con.execute("INSERT INTO overrides(ts,actor,kind,entity_id,detail,risk,violations,reason) VALUES(?,?,?,?,?,?,?,?)",
                        (clock(con), req.uid, "vendor_certification", vid, json.dumps({"added": req.body["add_diet"]}), "low",
                         json.dumps([f"{req.body['add_diet']} declared by organisers, not on the vendor sheet"]), reason))
        if not upd:
            raise HTTPError(400, "Nothing to change.")
        con.execute(f"UPDATE vendors SET {', '.join(k + '=?' for k in upd)} WHERE vid=?", list(upd.values()) + [vid])
        audit(con, req, "vendor_edit", "vendor", vid, upd, reason)
        bump(con)
        con.commit()
    return {"ok": True}


@route("POST", "/food/covers", view="food")
def covers(req):
    need(req, "vendor_edit")
    con = db()
    with LOCK:
        con.execute("INSERT INTO vendor_covers(vid,meal_id,covers) VALUES(?,?,?) ON CONFLICT(vid,meal_id) DO UPDATE SET covers=excluded.covers",
                    (req.body["vid"], req.body["meal_id"], int(req.body["covers"])))
        audit(con, req, "covers_logged", "vendor", req.body["vid"], req.body, None)
        bump(con)
        con.commit()
    return {"ok": True}


# ====================================================================== transport
@route("GET", "/transport", view="transport")
def transport_view(req):
    con = db()
    return transport_plan(con)


# ====================================================================== incidents
@route("GET", "/incidents", view="incidents")
def incidents_view(req):
    con = db()
    inc = rows(con, "SELECT * FROM incidents ORDER BY id DESC")
    for i in inc:
        i["params"] = jl(i["params"], {})
        i["impact"] = jl(i["impact"], {})
    opts = {
        "contingents": rows(con, "SELECT cid, college, actual FROM contingents ORDER BY cid"),
        "sites": rows(con, "SELECT site_id, name, kind, block FROM sites ORDER BY site_id"),
        "blocks": [r[0] for r in con.execute("SELECT DISTINCT block FROM sites WHERE kind='campus' ORDER BY block")],
        "items": [r[0] for r in con.execute("SELECT DISTINCT item FROM logistics ORDER BY item")],
        "locations": [r[0] for r in con.execute("SELECT DISTINCT location FROM logistics ORDER BY location")],
        "venues": rows(con, "SELECT vid, name FROM venues ORDER BY vid"),
        "vendors": rows(con, "SELECT vid, name, menu FROM vendors ORDER BY vid"),
        "sample_pids": [r[0] for r in con.execute("SELECT pid FROM people WHERE kind='participant' AND status='active' ORDER BY pid LIMIT 30")],
    }
    return {"incidents": inc, "types": incidents.TYPES, "options": opts, "clock": clock(con)}


@route("POST", "/incidents/preview", view="incidents")
def inc_preview(req):
    need(req, "incident")
    con = db()
    typ, prm = req.body.get("type"), req.body.get("params") or {}
    if typ not in incidents.TYPES:
        raise HTTPError(400, "Unknown incident type.")
    try:
        res = incidents.preview(con, typ, prm, req.uid, clock(con))
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPError(400, f"Invalid parameters: {e}")
    return res


@route("POST", "/incidents/commit", view="incidents")
def inc_commit(req):
    need(req, "incident")
    reason = reason_required(req)
    con = db()
    typ, prm = req.body.get("type"), req.body.get("params") or {}
    if typ not in incidents.TYPES:
        raise HTTPError(400, "Unknown incident type.")
    with LOCK:
        try:
            res = incidents.commit(con, typ, prm, req.uid, req.role, clock(con), reason)
        except (KeyError, ValueError, TypeError) as e:
            con.rollback()
            raise HTTPError(400, f"Invalid parameters: {e}")
        bump(con)
        con.commit()
    if typ in ("hostel_closure", "late_contingent", "participant_cancel"):
        _frontier_async()
    return res


@route("POST", "/incidents/{iid}/undo", view="incidents")
def inc_undo(req, iid):
    need(req, "incident")
    reason = reason_required(req)
    con = db()
    with LOCK:
        res = incidents.undo(con, int(iid), req.uid, req.role, clock(con), reason)
        if not res["ok"]:
            raise HTTPError(409, res["message"])
        bump(con)
        con.commit()
    return res


# ====================================================================== audit, staff, assumptions
@route("GET", "/audit")
def audit_view(req):
    if req.role not in ("director",):
        raise HTTPError(403, "The audit log is visible to the Festival Director.")
    con = db()
    lim = min(int(req.q("limit", 300)), 2000)
    log = rows(con, "SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (lim,))
    for l in log:
        l["detail"] = jl(l["detail"], {})
    return {"log": log, "chain": security.verify_chain(con), "overrides": rows(con, "SELECT * FROM overrides ORDER BY id DESC"),
            "break_glass": rows(con, "SELECT * FROM audit WHERE action='break_glass' ORDER BY seq DESC LIMIT 50"),
            "off_shift": con.execute("SELECT COUNT(*) FROM audit WHERE off_shift=1").fetchone()[0]}


@route("GET", "/staff")
def staff(req):
    con = db()
    now = clock(con)
    us = rows(con, "SELECT uid, name, role, role_key, shift, phone FROM users ORDER BY role, uid")
    for u in us:
        u["on_shift"] = security.on_shift(u["shift"], now)
        if req.role != "director":
            u["phone"] = security.mask_phone(u["phone"])
    return {"staff": us, "permissions": config.PERMISSIONS, "fields": config.FIELD_ACCESS, "clock": now}


def _assumptions(con):
    out = []
    for k, (v, cat, why) in config.ASSUMPTIONS.items():
        cur = get_setting(con, k)
        out.append({"key": k, "value": cur, "default": v, "category": cat, "why": why, "changed": cur != v})
    return out


@route("GET", "/assumptions")
def assumptions(req):
    con = db()
    return {"assumptions": _assumptions(con), "venue_caps": config.VENUE_CAPS}


@route("POST", "/assumptions", view="quality")
def set_assumption(req):
    if req.role != "director":
        raise HTTPError(403, "Only the Festival Director changes assumptions.")
    reason = reason_required(req)
    con = db()
    k, v = req.body.get("key"), req.body.get("value")
    if k not in config.ASSUMPTIONS:
        raise HTTPError(400, "Unknown assumption.")
    with LOCK:
        old = get_setting(con, k)
        set_setting(con, k, v)
        audit(con, req, "assumption_change", "setting", k, {"from": old, "to": v}, reason)
        bump(con)
        con.commit()
    return {"ok": True, "note": "Re-run the affected plan to apply."}


# ====================================================================== exports
EXPORTS = {
    "accommodation_plan": ("accommodation", "hospitality"),
    "schedule": ("schedule", "events"),
    "heat_assignments": ("schedule", "events"),
    "equipment_moves": ("inventory", "logistics"),
    "merch_plan": ("merch", "merch"),
    "food_coverage": ("food", "food"),
    "data_issues": ("quality", "registration"),
    "audit_log": ("audit", "director"),
}


@route("GET", "/export/{name}")
def export(req, name):
    name = name.replace(".csv", "")
    if name not in EXPORTS:
        raise HTTPError(404, "Unknown export.")
    mod, owner = EXPORTS[name]
    if req.role != "director" and req.role != owner:
        raise HTTPError(403, "Not permitted.")
    con = db()
    buf = io.StringIO()
    w = csv.writer(buf)
    from . import exports as X
    X.write(con, name, w)
    with LOCK:
        audit(con, req, "export", "export", name, {}, None)
        con.commit()
    return buf.getvalue(), "text/csv; charset=utf-8", f"festos_{name}.csv"
