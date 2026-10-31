"""Independent checker: re-derives every hard rule from the stored plan, without
reusing the optimiser's code paths. Run:  python3 -m festos.verify
Used for the "how outputs were checked" evidence and by the test suite."""
import json
import sys
from collections import Counter, defaultdict

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from . import schedule, timeutil as T
from .db import connect, get_setting


def check(con):
    R = []

    def rule(name, bad, detail=""):
        R.append({"rule": name, "ok": not bad, "violations": len(bad) if isinstance(bad, (list, set)) else int(bool(bad)),
                  "examples": list(bad)[:5] if isinstance(bad, (list, set)) else [], "detail": detail})

    people = {r["pid"]: dict(r) for r in con.execute("SELECT * FROM people")}
    sites = {r["site_id"]: dict(r) for r in con.execute("SELECT * FROM sites")}
    rooms = {r["room_id"]: dict(r) for r in con.execute("SELECT * FROM rooms")}
    alloc = [dict(r) for r in con.execute("SELECT * FROM alloc WHERE status='placed'")]
    verified = get_setting(con, "external_accessible_beds_verified") or {}
    overrides = {r["entity_id"] for r in con.execute("SELECT entity_id FROM overrides WHERE active=1")}

    # --- accommodation
    g, wc, gf, minors, inactive, nights_bad, staff_campus = [], [], [], [], [], [], []
    for a in alloc:
        p = people[a["pid"]]
        s = sites[a["site_id"]]
        if a["pid"] in overrides:
            continue
        if p["status"] != "active":
            inactive.append(a["pid"])
        if a["nights"] != p["nights"]:
            nights_bad.append(a["pid"])
        if s["kind"] == "campus":
            if p["housing_cat"] == "S":
                staff_campus.append(a["pid"])
            elif p["housing_cat"] == "X" and s["designation"] != "X":
                g.append(a["pid"])
            elif p["housing_cat"] in ("M", "F") and s["designation"] not in (p["housing_cat"],):
                g.append(a["pid"])
            if p["kind"] == "participant" and p["access_need"] == "WC" and not rooms[a["room_id"]]["accessible"]:
                wc.append(a["pid"])
            if p["kind"] == "participant" and p["access_need"] in ("WC", "GF", "UNKNOWN") and s["floor"] != 0:
                gf.append(a["pid"])
        else:
            if p["kind"] == "participant" and p["access_need"] in ("WC", "GF", "UNKNOWN") and not verified.get(a["site_id"]):
                gf.append(a["pid"])
            if p["is_minor"] and s["transport"] != "yes":
                minors.append(a["pid"])
    rule("Gender pool respected (nobody outside the binary in a binary floor; men/women in their pool)", g)
    rule("Wheelchair users only in accessible rooms", wc)
    rule("Ground-floor / blank accessibility need only on floor 0 or verified step-free", gf)
    rule("Minors never at a property without regular transport", minors)
    rule("Escorts not in student hostel rooms", staff_campus)
    rule("Only active, confirmed people hold beds", inactive)
    rule("Allocated nights match the person's stay", nights_bad)

    # bed / room capacity per night and one category per room per night
    bed_use = defaultdict(int)
    dbl, over, mixed = [], [], []
    room_n = defaultdict(lambda: defaultdict(list))
    for a in alloc:
        key = (a["room_id"], a["bed"])
        if bed_use[key] & a["nights"]:
            dbl.append(f"{a['room_id']}#{a['bed']}")
        bed_use[key] |= a["nights"]
        for n in T.mask_list(a["nights"]):
            room_n[a["room_id"]][n].append(people[a["pid"]]["housing_cat"])
    size = get_setting(con, "external_room_size")
    for rid, nn in room_n.items():
        cap = rooms[rid]["capacity"] if rid in rooms else size
        for n, cats in nn.items():
            if len(cats) > cap:
                over.append(f"{rid} night {n}")
            if len(set(cats)) > 1:
                mixed.append(f"{rid} night {n}: {sorted(set(cats))}")
            if "S" in cats and len(cats) > 1:
                mixed.append(f"{rid} night {n}: escort shares")
    rule("No bed double-booked on any night", dbl)
    rule("Room capacity respected every night", over)
    rule("One housing category per room per night; escorts in private rooms", mixed)
    site_n = defaultdict(lambda: [0] * T.N_NIGHTS)
    for a in alloc:
        for n in T.mask_list(a["nights"]):
            site_n[a["site_id"]][n] += 1
    sover = [f"{s} night {n}" for s, v in site_n.items() for n, c in enumerate(v) if c > sites[s]["beds"]]
    rule("Site capacity respected every night", sover)
    closed = [a["pid"] for a in alloc if sites[a["site_id"]]["closed_from"] and
              any(T.NIGHT0.replace(hour=23) + __import__("datetime").timedelta(days=n) >= T.p(sites[a["site_id"]]["closed_from"]) for n in T.mask_list(a["nights"]))]
    rule("Nobody housed in a closed site after it closes", closed)
    cost = sum(a["cost"] or 0 for a in alloc)
    budget = get_setting(con, "budget_external_inr")
    budget_ov = con.execute("SELECT COUNT(*) FROM overrides WHERE kind='budget'").fetchone()[0]
    rule("External spend within budget (or Director override recorded)", cost > budget and not budget_ov, f"Rs. {cost:,} of {budget:,}")
    # leaders near group
    units = defaultdict(set)
    for a in alloc:
        p = people[a["pid"]]
        s = sites[a["site_id"]]
        if p["kind"] == "participant":
            units[p["cid"]].add(s["block"] if s["kind"] == "campus" else s["site_id"])
    far = []
    for a in alloc:
        p = people[a["pid"]]
        s = sites[a["site_id"]]
        u = s["block"] if s["kind"] == "campus" else s["site_id"]
        if p["kind"] == "leader" and u not in units[p["cid"]]:
            far.append(a["pid"])
        if p["kind"] == "escort" and a["site_id"] != "EXT01" and u not in units[p["cid"]]:
            far.append(a["pid"])
    rule("Leaders and escorts housed near their group", far)

    # --- schedule (validator is a separate code path from the optimiser)
    conf = schedule.validate(con)
    hard_kinds = {"venue_window", "venue_double_booked", "round_order", "panel_overlap", "judge_clash", "venue_capacity", "team_split", "venue_requirement"}
    for k in sorted(hard_kinds):
        c = [x for x in conf if x["kind"] == k]
        rule(f"Schedule: no {k.replace('_', ' ')}", [x["message"] for x in c])

    # --- merchandise
    dup = [r[0] for r in con.execute("SELECT pid||'/'||item FROM merch_issues WHERE kind='issue' AND voided=0 GROUP BY pid, item HAVING COUNT(*)>1")]
    rule("Each entitled item issued at most once per person", dup)
    neg = [r[0] for r in con.execute("""SELECT s.item||' '||s.size FROM merch_stock s WHERE s.qty - s.reserved <
                                        (SELECT COUNT(*) FROM merch_issues m WHERE m.item=s.item AND m.size=s.size AND m.voided=0)""")]
    rule("Merchandise never issued beyond stock", neg)

    # --- governance
    from .security import verify_chain
    ch = verify_chain(con)
    rule("Audit log hash chain intact", not ch["ok"], f"{ch.get('checked')} entries")
    return R


def main():
    con = connect()
    res = check(con)
    w = max(len(r["rule"]) for r in res)
    bad = 0
    for r in res:
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['rule']:<{w}}  {r['detail']}" + ("" if r["ok"] else f"  ({r['violations']}: {r['examples']})"))
        bad += not r["ok"]
    print(f"\n{len(res) - bad}/{len(res)} hard rules hold.")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
