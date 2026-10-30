"""Food vendors: compliance, dietary coverage per meal, interruptions, settlement.

Coverage is a small flow problem (diet groups → compliant vendors → capacity).
We solve it lexicographically: the most restricted diets (nut allergy, Jain,
gluten-free, vegan, halal) are routed first and can never be displaced by
less-restricted diners — augmenting paths only ever grow source flows."""
import json
from collections import Counter, defaultdict, deque
from datetime import timedelta

from . import timeutil as T
from .db import get_setting, jl

DIET_LABEL = {"NUT": "Nut allergy", "JAIN": "Jain", "GF": "Gluten-free", "VEGAN": "Vegan", "HALAL": "Halal",
              "VEG": "Vegetarian", "UNKNOWN": "Unknown (blank)", "NONVEG": "Non-vegetarian"}
PRIORITY = ["NUT", "JAIN", "GF", "VEGAN", "HALAL", "VEG", "UNKNOWN", "NONVEG"]
SAFETY = {"NUT", "GF", "JAIN"}


def vendor_tags(v, pessimistic=True):
    tags = set(jl(v["diets"], []))
    flags = " ".join(jl(v["flags"], []))
    if pessimistic and "Menu says vegan" in flags:
        tags = {"non-vegetarian"}          # label conflict: only safe for unrestricted diners
    return tags


def eligible(diet, tags, halal_veg):
    pure_veg = ("vegetarian" in tags or "vegan" in tags or "jain" in tags) and "non-vegetarian" not in tags
    if diet == "NONVEG":
        return True
    if diet in ("VEG", "UNKNOWN"):
        return pure_veg
    if diet == "JAIN":
        return "jain" in tags
    if diet == "HALAL":
        return "halal" in tags or (halal_veg and pure_veg)
    if diet == "GF":
        return "gluten-free" in tags
    if diet == "NUT":
        return "nut-free" in tags
    if diet == "VEGAN":
        return "vegan" in tags
    return False


def vendor_status(v, date, ms, me):
    """Can vendor serve meal on date between ms-me? returns (ok, reason)."""
    exp = v["cert_renewed"] or v["cert_expiry"]
    if exp < date:
        return False, f"hygiene certificate expired {exp}"
    h0, h1 = v["hours"].split("-")
    if not (h0 <= ms and me <= h1):
        return False, f"open {v['hours']} — outside {ms}–{me}"
    if v["shutdown_from"] and v["shutdown_from"] <= f"{date} {me}":
        return False, f"shut down from {v['shutdown_from'][5:]}: {v['shutdown_reason'] or 'interruption'}"
    return True, "ok"


def meals(con):
    out = []
    for d in get_setting(con, "catering_days"):
        for name, s, e in get_setting(con, "meals"):
            out.append({"meal_id": f"{d} {name}", "date": d, "name": name, "start": s, "end": e})
    return out


def headcount(con, meal):
    mid = T.p(f"{meal['date']} {meal['start']}") + timedelta(minutes=30)
    cnt = Counter()
    for r in con.execute("SELECT diet, arrival, departure, kind FROM people WHERE status='active' AND timing_ok=1"):
        a, d = T.p(r["arrival"]), T.p(r["departure"])
        if a <= mid <= d:
            cnt[r["diet"]] += 1
    cnt["UNKNOWN"] += get_setting(con, "staff_meal_headcount")
    return cnt


def maxflow_lex(groups, vendors, edges):
    """groups: {diet: demand}, vendors: {vid: cap}, edges: {diet: [vid]} → flows {(diet,vid): f}."""
    S, Tn = "S", "T"
    cap = defaultdict(int)
    adj = defaultdict(set)

    def add(u, v, c):
        cap[(u, v)] += c
        adj[u].add(v)
        adj[v].add(u)
    for vid, c in vendors.items():
        add(("v", vid), Tn, c)
    for d in groups:
        for vid in edges.get(d, []):
            add(("d", d), ("v", vid), 10 ** 6)
    flow = defaultdict(int)

    def resid(u, v):
        return cap[(u, v)] - flow[(u, v)] + flow[(v, u)]

    def bfs():
        par = {S: None}
        q = deque([S])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v not in par and resid(u, v) > 0:
                    par[v] = u
                    if v == Tn:
                        return par
                    q.append(v)
        return None
    for d in sorted(groups, key=lambda x: PRIORITY.index(x) if x in PRIORITY else 99):
        add(S, ("d", d), groups[d])
        while True:
            par = bfs()
            if not par:
                break
            path = []
            v = Tn
            while par[v] is not None:
                path.append((par[v], v))
                v = par[v]
            b = min(resid(u, v) for u, v in path)
            for u, v in path:
                back = min(b, flow[(v, u)])
                flow[(v, u)] -= back
                flow[(u, v)] += b - back
    res = {}
    for d in groups:
        for vid in edges.get(d, []):
            f = flow[(("d", d), ("v", vid))] - flow[(("v", vid), ("d", d))]
            if f > 0:
                res[(d, vid)] = f
    return res


def coverage(con, pessimistic=True):
    halal_veg = get_setting(con, "halal_accepts_vegetarian")
    vendors = [dict(r) for r in con.execute("SELECT * FROM vendors ORDER BY vid")]
    out = []
    for m in meals(con):
        hc = headcount(con, m)
        caps, edges, why = {}, defaultdict(list), {}
        for v in vendors:
            ok, reason = vendor_status(v, m["date"], m["start"], m["end"])
            why[v["vid"]] = reason
            if not ok:
                continue
            caps[v["vid"]] = v["capacity"]
            tags = vendor_tags(v, pessimistic)
            for d in hc:
                if eligible(d, tags, halal_veg):
                    edges[d].append(v["vid"])
        fl = maxflow_lex(dict(hc), caps, edges)
        served = Counter()
        load = Counter()
        for (d, vid), f in fl.items():
            served[d] += f
            load[vid] += f
        diets = {}
        for d, n in hc.items():
            diets[d] = {"demand": n, "served": served[d], "gap": n - served[d],
                        "vendors": [vid for vid in edges.get(d, [])]}
        out.append({**m, "headcount": sum(hc.values()), "diets": diets, "vendor_load": dict(load),
                    "vendor_cap": caps, "vendor_status": why,
                    "flows": [{"diet": d, "vid": vid, "covers": f} for (d, vid), f in fl.items()]})
    return out


def uncatered(con):
    days = set(get_setting(con, "catering_days"))
    res = Counter()
    for r in con.execute("SELECT arrival, departure FROM people WHERE status='active' AND timing_ok=1"):
        a, d = T.p(r["arrival"]), T.p(r["departure"])
        day = a.replace(hour=0, minute=0)
        while day <= d:
            ds = day.strftime("%Y-%m-%d")
            if ds not in days:
                # present across a meal window that day?
                for name, s, e in get_setting(con, "meals"):
                    ms = T.p(f"{ds} {s}")
                    if a <= ms <= d:
                        res[f"{ds} {name}"] += 1
            day += timedelta(days=1)
    return dict(sorted(res.items()))


def vendor_table(con, cov=None):
    cov = cov or coverage(con)
    rate_default = get_setting(con, "vendor_rate_inr")
    mgf = get_setting(con, "min_guarantee_frac")
    vendors = [dict(r) for r in con.execute("SELECT * FROM vendors ORDER BY vid")]
    logged = defaultdict(int)
    for r in con.execute("SELECT vid, SUM(covers) c FROM vendor_covers GROUP BY vid"):
        logged[r["vid"]] = r["c"]
    out = []
    for v in vendors:
        meals_ok = [m for m in cov if v["vid"] in m["vendor_cap"]]
        blocked = [{"meal": m["meal_id"], "reason": m["vendor_status"][v["vid"]]} for m in cov if v["vid"] not in m["vendor_cap"]]
        planned = sum(m["vendor_load"].get(v["vid"], 0) for m in cov)
        rate = v["rate"] or rate_default
        actual = logged[v["vid"]] or planned
        terms = v["terms"].lower()
        if "minimum guarantee" in terms:
            mg = int(mgf * v["capacity"] * len(meals_ok))
            payable = max(mg, actual) * rate
            note = f"MG {mg:,} covers over {len(meals_ok)} compliant meals; blocked meals carry no guarantee (compliance breach)."
        elif "50% advance" in terms:
            payable = actual * rate
            note = f"Advance ₹{int(0.5 * planned * rate):,} on forecast; balance on actuals."
        else:
            payable = actual * rate
            note = "Paid per item sold."
        exp = v["cert_renewed"] or v["cert_expiry"]
        out.append({**v, "diets": jl(v["diets"], []), "flags": jl(v["flags"], []),
                    "compliant_meals": len(meals_ok), "blocked_meals": blocked, "planned_covers": planned,
                    "logged_covers": logged[v["vid"]], "rate": rate, "payable": int(payable), "settlement_note": note,
                    "cert_state": "expired" if exp < "2026-10-30" else ("expires mid-fest" if exp < "2026-11-01" else "valid")})
    return out


def summary(con):
    cov = coverage(con)
    gaps = defaultdict(int)
    worst = {}
    for m in cov:
        for d, x in m["diets"].items():
            if x["gap"] > 0:
                gaps[d] += x["gap"]
                if d not in worst or x["gap"] > worst[d]["gap"]:
                    worst[d] = {"meal": m["meal_id"], "gap": x["gap"], "demand": x["demand"]}
    return {"meals": cov, "gap_totals": dict(gaps), "worst": worst, "uncatered": uncatered(con)}
