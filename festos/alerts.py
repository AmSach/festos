"""Control tower: everything that needs attention, ranked by severity × urgency,
with an owner and a next action — so nobody has to call the festival director."""
import math
from collections import Counter, defaultdict
from datetime import timedelta

from . import timeutil as T, schedule, inventory, food, transport
from .db import get_setting, jl

def hd(s):
    """Human date: '2026-10-31 07:30' -> 'Sat 31 Oct 07:30'."""
    d = T.p(s)
    if not d:
        return s or ""
    return d.strftime("%a %d Oct" if d.month == 10 else "%a %d Nov").lstrip("0") + (d.strftime(" %H:%M") if len(s) > 10 else "")


SEV = {"critical": 100, "high": 60, "medium": 30, "low": 12, "info": 0}


def now(con):
    mode = get_setting(con, "clock_mode", "demo")
    if mode == "live":
        from datetime import datetime
        return datetime.now().strftime(T.FMT)
    return get_setting(con, "clock")


def _urg(clock, when):
    if not when:
        return 1.0, None
    h = (T.p(when) - T.p(clock)).total_seconds() / 3600
    if h <= 0:
        return 2.0, h
    if h <= 3:
        return 1.8, h
    if h <= 12:
        return 1.4, h
    if h <= 24:
        return 1.2, h
    return 1.0, h


def build(con, cache=None):
    clock = now(con)
    A = []

    def add(key, sev, cat, title, detail, owner, link, count=None, when=None, why=None):
        u, hrs = _urg(clock, when)
        score = SEV[sev] * u + (math.log10((count or 0) + 1) * 6)
        A.append({"key": key, "severity": sev, "category": cat, "title": title, "detail": detail, "owner": owner,
                  "link": link, "count": count, "when": when, "hours": None if hrs is None else round(hrs, 1),
                  "score": round(score, 1), "why": why})

    # ---------------------------------------------------------------- accommodation
    up = [dict(r) for r in con.execute("""SELECT a.pid, a.nights, p.access_need, p.kind, p.arrival, p.cid FROM alloc a
                                         JOIN people p USING(pid) WHERE a.status='unplaced' AND p.status='active'""")]
    if up:
        first_n = min((T.mask_list(r["nights"])[0] for r in up if r["nights"]), default=0)
        when = T.f(T.NIGHT0 + timedelta(days=first_n, hours=22))
        hard = sum(1 for r in up if r["access_need"] in ("WC", "GF", "UNKNOWN") and r["kind"] == "participant")
        per_night = Counter(n for r in up for n in T.mask_list(r["nights"]))
        worst = max(per_night.items(), key=lambda kv: kv[1])
        add("beds_short", "critical", "Accommodation", f"{len(up)} people have no bed",
            f"Peak gap {worst[1]} on the night of {T.NIGHT_LABELS[worst[0]]}. {hard} of them have an accessibility need (never relaxed). Beds, not budget, bind beyond ₹26 L — add a property or confirm day-only participation.",
            "hospitality", "#/accommodation", len(up), when,
            "Peak demand 1,546 vs 1,450 beds before faculty escorts; gender pools and accessibility fragment supply further.")
    wc = [r for r in up if r["access_need"] == "WC"]
    if wc:
        add("wc_unmet", "critical", "Accommodation", f"{len(wc)} wheelchair users without an accessible bed",
            "Campus has 10 accessible rooms (26 beds). External properties are unverified. Call Central Residency / Campus Guest House and record verified accessible beds — the plan re-runs instantly.",
            "hospitality", "#/accommodation?tab=verify", len(wc), None, "Accessibility is a hard constraint: the system will not place them in an unverified room.")
    xf = [r["site_id"] for r in con.execute("SELECT site_id FROM sites WHERE kind='campus' AND designation='X' AND designation_locked=0")]
    if xf:
        add("allgender_approval", "high", "Accommodation", f"Approve {len(xf)} all-gender floors",
            f"The optimiser proposes {', '.join(xf)} as all-gender floors for non-binary, prefer-not-to-say and blank-gender participants (≈700 per night). Needs Director approval and signage.",
            "director", "#/accommodation?tab=floors", len(xf), T.f(T.NIGHT0 + timedelta(hours=12)))
    # early check-in conflicts (arrivals before 12:00 into a bed vacated that morning)
    # arrivals in the next 12 hours
    nxt = [dict(r) for r in con.execute("""SELECT p.pid, p.arrival, a.status FROM people p LEFT JOIN alloc a USING(pid)
                                          WHERE p.status='active' AND p.arrival > ? AND p.arrival <= ?""",
                                        (clock, T.f(T.p(clock) + timedelta(hours=12))))]
    if nxt:
        nob = sum(1 for r in nxt if r["status"] == "unplaced")
        by_h = Counter(r["arrival"] for r in nxt)
        peak = max(by_h.items(), key=lambda kv: kv[1])
        add("arrivals_next", "high" if nob else "medium", "Arrivals", f"{len(nxt)} arrivals in the next 12 h" + (f" — {nob} without a bed" if nob else ""),
            f"Biggest wave {peak[1]} at {hd(peak[0])}. Staff the registration desk and kit counter for that hour.",
            "registration", "#/arrivals", len(nxt), peak[0])

    # ---------------------------------------------------------------- people / data
    dups = con.execute("SELECT COUNT(*) FROM dup_candidates WHERE status='open'").fetchone()[0]
    if dups:
        add("dups", "high", "Data", f"{dups} probable duplicate record(s) awaiting review",
            "Held out of beds, kits and meals until someone confirms. Evidence breakdown in Data Quality.", "registration", "#/quality?tab=dups", dups)
    inv = con.execute("SELECT COUNT(*) FROM people WHERE timing_ok=0 AND status='needs_confirmation'").fetchone()[0]
    if inv:
        add("timing", "high", "Data", f"{inv} participants with impossible arrival/departure",
            "Arrival after departure. No bed, heat or meal is planned until the leader confirms.", "registration", "#/quality?tab=issues&code=TIME_INVERTED", inv)
    gb = con.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND gender_raw='' AND status='active'").fetchone()[0]
    if gb:
        add("gender_blank", "medium", "Data", f"{gb} blank gender fields — housed all-gender, confirmation pending",
            "Blank is not a gender. Ask leaders whether each person wants segregated housing; prefer-not-to-say is never re-asked.", "registration", "#/quality?tab=issues&code=GENDER_BLANK", gb)
    lm = con.execute("SELECT COUNT(*) FROM issues WHERE code='TEAM_ROSTER_MISSING' AND status='open'").fetchone()[0]
    if lm:
        add("rosters", "medium", "Events", f"{lm} team events have no team rosters",
            "Team IDs only appear on events labelled 'solo'. Collect rosters before prelims; heats are planned per person meanwhile.", "events", "#/quality?tab=issues&code=TEAM_ROSTER_MISSING", lm)

    # ---------------------------------------------------------------- schedule
    conf = (cache or {}).get("conflicts") or schedule.validate(con)
    hm = {r["hid"]: dict(r) for r in con.execute("SELECT * FROM heats")}
    for c in conf:
        if c["kind"] == "equipment_shortage":
            evs = c["refs"]
            add("eq_" + c["message"].split(":")[0], c["severity"], "Logistics", c["message"].split(".")[0],
                c["message"] + " Options: repair/rent, inspect 'needs check' stock, or waive the requirement (Events lead, with reason).",
                "logistics", "#/inventory", c["count"], min(h["start"] for h in hm.values()))
        elif c["kind"] in ("venue_double_booked", "venue_window", "round_order", "judge_clash", "panel_overlap", "venue_capacity"):
            when = min((hm[r]["start"] for r in c["refs"] if r in hm), default=None)
            add("sch_" + c["kind"] + str(len(A)), c["severity"], "Schedule", c["message"][:90], c["message"], "events", "#/schedule", c["count"], when)
        elif c["kind"] == "participant_clash":
            add("clash", "high", "Schedule", c["message"], "Open Schedule → Conflicts to see each person and the heats involved.", "events", "#/schedule?tab=conflicts", c["count"])
        elif c["kind"] == "participant_absent":
            add("absent", "medium", "Schedule", f"{c['count']} participants can't attend a heat of an event they registered for",
                "Mostly the 379 people arriving 22:00 on 31 Oct. Every event cannot also host prelims, semis and finals on 1 Nov — consider asking those contingents to arrive earlier.",
                "events", "#/schedule?tab=conflicts", c["count"])
        elif c["kind"] == "volunteer_shortfall":
            add("volunteers", "medium", "Staffing", "Volunteer demand exceeds the assumed pool", c["message"], "director", "#/schedule?tab=volunteers", c["count"])

    # ---------------------------------------------------------------- merchandise
    ms = (cache or {}).get("merch") or inventory.merch_status(con)
    for k, when in ms["stockout"].items():
        item, size = k.split("|")
        line = next((l for l in ms["lines"] if l["item"] == item and l["size"] == size), None)
        short = -line["gap"] if line and line["gap"] is not None and line["gap"] < 0 else 0
        sev = "critical" if item in ("ID card", "lanyard") else "high"
        add(f"merch_{item}_{size}", sev, "Merchandise", f"{item}{'' if size == 'NA' else ' ' + size} runs out {hd(when)}",
            f"Short by {short:,}. " + ("ID cards gate check-in — order a reprint now." if item == "ID card" else "Use the fair-share quota per contingent and one-size-up substitution with consent."),
            "merch", "#/merch", short, when)
    blocked = con.execute("SELECT COUNT(*) FROM merch_blocked").fetchone()[0]
    if blocked:
        add("dup_claims", "medium", "Merchandise", f"{blocked} duplicate merchandise claim(s) blocked",
            "Every blocked claim is logged with desk, staff member and original issue.", "merch", "#/merch?tab=blocked", blocked)

    # ---------------------------------------------------------------- food
    fs = (cache or {}).get("food") or food.summary(con)
    for d, g in sorted(fs["gap_totals"].items(), key=lambda kv: -kv[1]):
        w = fs["worst"][d]
        first = next((m for m in fs["meals"] if m["diets"].get(d, {}).get("gap", 0) > 0), None)
        sev = "critical" if d in food.SAFETY else "high" if d in ("HALAL", "VEG", "VEGAN") else "medium"
        add(f"diet_{d}", sev, "Food", f"{food.DIET_LABEL[d]}: {w['gap']} without a safe counter at {hd(w['meal'][:10])} {w['meal'][11:]}",
            f"{g:,} meal-covers uncovered across the fest. " + {
                "NUT": "No vendor declares a nut-free kitchen. Designate one counter with an allergen protocol and labels.",
                "JAIN": "Jain thali vendor is not tagged Jain; the Jain-tagged sandwich vendor's certificate expired. Verify / renew.",
                "GF": "Only one gluten-free vendor and its certificate expires 31 Oct.",
            }.get(d, "Re-balance counters or add a vendor."), "food", "#/food", w["gap"],
            f"{first['date']} {first['start']}" if first else None)
    for v in con.execute("SELECT * FROM vendors"):
        exp = v["cert_renewed"] or v["cert_expiry"]
        if exp < "2026-10-30":
            add(f"cert_{v['vid']}", "high", "Food", f"{v['name']} ({v['menu']}) blocked: hygiene cert expired {exp}",
                f"Capacity {v['capacity']} lost every meal until renewed.", "food", "#/food", v["capacity"])
        elif exp < "2026-11-01":
            add(f"certx_{v['vid']}", "high", "Food", f"{v['name']} cert expires {exp} — cannot serve 1 Nov",
                "Get the renewed certificate before the last day.", "food", "#/food", v["capacity"], f"{exp} 23:59")
        if v["shutdown_from"]:
            add(f"shut_{v['vid']}", "critical", "Food", f"{v['name']} shut down ({v['shutdown_reason'] or 'interruption'})",
                "Coverage recomputed; see Food for the diets that lost capacity.", "food", "#/food", v["capacity"], v["shutdown_from"])
    if fs["uncatered"]:
        tot = sum(fs["uncatered"].values())
        add("uncatered", "medium", "Food", f"{tot:,} meal-covers outside contracted catering days",
            "People arrive 29 Oct and leave up to 3 Nov, but vendors are only planned for 30 Oct–1 Nov: " +
            ", ".join(f"{k[5:]} ({v})" for k, v in list(fs["uncatered"].items())[:4]) + "…", "food", "#/food", tot, "2026-10-29 19:30")

    # ---------------------------------------------------------------- transport
    tp = (cache or {}).get("transport") or transport.plan(con)
    for r in tp["risks"][:6]:
        add(f"tr_{r['site']}_{r['date']}", "medium", "Transport", r["msg"][:90], r["msg"], "logistics", "#/transport", None, r["date"] + " 21:00")

    # ---------------------------------------------------------------- governance
    ov = con.execute("SELECT COUNT(*) FROM overrides WHERE active=1 AND risk='high'").fetchone()[0]
    if ov:
        add("overrides", "high", "Governance", f"{ov} high-risk override(s) in force",
            "Each carries a reason and the approver. Review them in Access & Audit.", "director", "#/audit?tab=overrides", ov)
    bg = con.execute("SELECT COUNT(*) FROM audit WHERE action='break_glass' AND ts >= ?", (T.f(T.p(clock) - timedelta(hours=24)),)).fetchone()[0]
    if bg:
        add("breakglass", "medium", "Governance", f"{bg} break-glass access(es) to sensitive data in 24 h", "Each is justified in the audit log.", "director", "#/audit", bg)
    inc = con.execute("SELECT COUNT(*) FROM incidents WHERE status='committed'").fetchone()[0]
    A.sort(key=lambda a: -a["score"])
    return {"clock": clock, "alerts": A, "incidents": inc}


def now_board(con):
    """What is happening now / next 3 hours."""
    clock = now(con)
    c = T.p(clock)
    nxt = T.f(c + timedelta(hours=3))
    running = [dict(r) for r in con.execute("""SELECT h.*, e.name ev, v.name venue FROM heats h JOIN events e USING(eid) JOIN venues v USING(vid)
                                               WHERE h.start <= ? AND h.end > ? ORDER BY h.start""", (clock, clock))]
    upcoming = [dict(r) for r in con.execute("""SELECT h.*, e.name ev, v.name venue FROM heats h JOIN events e USING(eid) JOIN venues v USING(vid)
                                                WHERE h.start > ? AND h.start <= ? ORDER BY h.start""", (clock, nxt))]
    for h in running + upcoming:
        h["members"] = con.execute("SELECT COUNT(*) FROM registrations WHERE heat_id=?", (h["hid"],)).fetchone()[0]
    onsite = con.execute("SELECT COUNT(*) FROM people WHERE status='active' AND timing_ok=1 AND arrival<=? AND departure>?", (clock, clock)).fetchone()[0]
    checked = con.execute("SELECT COUNT(*) FROM people WHERE checked_in IS NOT NULL").fetchone()[0]
    arr = [dict(r) for r in con.execute("""SELECT substr(arrival,1,13) h, COUNT(*) n FROM people WHERE status='active' AND arrival>? AND arrival<=?
                                          GROUP BY h ORDER BY h""", (clock, T.f(c + timedelta(hours=24))))]
    # tonight's beds
    night = (c - T.NIGHT0).days if c.hour >= 6 else (c - T.NIGHT0).days - 1
    night = max(0, min(T.N_NIGHTS - 1, night))
    placed = sum(1 for r in con.execute("SELECT nights FROM alloc WHERE status='placed'") if r[0] >> night & 1)
    unpl = sum(1 for r in con.execute("SELECT nights FROM alloc WHERE status='unplaced'") if r[0] >> night & 1)
    beds = sum(r[0] for r in con.execute("SELECT beds FROM sites WHERE status='open'"))
    return {"clock": clock, "running": running, "upcoming": upcoming, "onsite": onsite, "checked_in": checked,
            "arrivals_24h": arr, "tonight": {"night": T.NIGHT_LABELS[night], "placed": placed, "unplaced": unpl, "beds": beds}}
