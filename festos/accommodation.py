"""Accommodation allocation.

Stage 1 — site assignment (MILP, solved with HiGHS via SciPy when available,
greedy fallback otherwise).  People are grouped into cohorts with identical
(contingent, housing category, hard need, nights, minor, early-event, kind).
Decision variables:
    x[k,s]  integer   people of cohort k placed at site s (hostel floor / property)
    u[k]    integer   people of cohort k left without a bed (always allowed, priced)
    y[c,u]  binary    contingent c uses cohesion unit u (hostel block / property)
    z[f]    binary    floor f is proposed as an all-gender floor
Hard constraints (never traded for score):
    per-night bed capacity of every site, gender pool of every floor (incl. the
    designation chosen by z), wheelchair users only in accessible beds,
    ground-floor / unknown-need people only on floor 0 or verified step-free
    beds, minors never at a property without regular transport, the external
    budget, leaders in a unit their group uses, escorts at the guest house or a
    property their group uses.
Soft objective (₹ shadow prices, all visible in the assumptions register):
    external cost + split penalty + floor spread + early-event distance +
    near-venue distance + minors off campus + unplaced penalties.

Stage 2 — room/bed packing inside each site.  Each person's nights form an
interval, so a bed can be reused by people whose stays do not overlap.
Same-contingent people share rooms first; wheelchair users take accessible beds
first; leaders join their group's floor; escorts share staff rooms.
"""
import json
import math
import time
from datetime import timedelta
from collections import Counter, defaultdict

from . import timeutil as T
from .db import get_setting, jl, set_setting

try:
    import numpy as np
    from scipy.optimize import milp, LinearConstraint, Bounds
    from scipy.sparse import coo_matrix
    HAVE_SCIPY = True
except Exception:  # pragma: no cover
    HAVE_SCIPY = False

N = T.N_NIGHTS


def need_class(p):
    if p["kind"] != "participant":
        return "O"
    a = p["access_need"]
    if a == "WC":
        return "WC"
    if a in ("GF", "UNKNOWN"):
        return "GF"
    return "O"


# ====================================================================== data
def load(con):
    g = lambda k: get_setting(con, k)
    people = [dict(r) for r in con.execute(
        "SELECT * FROM people WHERE status='active' AND nights>0 AND accom_req IN ('yes','unknown')")]
    sites = {r["site_id"]: dict(r) for r in con.execute("SELECT * FROM sites")}
    verified = g("external_accessible_beds_verified") or {}
    for s in sites.values():
        s["cap"] = [s["beds"]] * N
        s["acc"] = [s["acc_beds"] if s["kind"] == "campus" else int(verified.get(s["site_id"], 0))] * N
        if s["closed_from"]:
            cf = T.p(s["closed_from"])
            for n in range(N):
                night_start = T.NIGHT0.replace(hour=23) + timedelta(days=n)
                if night_start >= cf:
                    s["cap"][n] = 0
                    s["acc"][n] = 0
        if s["kind"] == "campus":
            # room-level closures reduce floor capacity
            closed = con.execute("SELECT SUM(capacity), SUM(CASE WHEN accessible=1 THEN capacity ELSE 0 END) FROM rooms WHERE site_id=? AND status!='available'",
                                 (s["site_id"],)).fetchone()
            if closed and closed[0]:
                s["cap"] = [max(0, c - closed[0]) for c in s["cap"]]
                s["acc"] = [max(0, c - (closed[1] or 0)) for c in s["acc"]]
    conts = {r["cid"]: dict(r) for r in con.execute("SELECT * FROM contingents")}
    for c in conts.values():
        c["prefs"] = jl(c["prefs"], [])
    # earliest event start per person per day
    early = defaultdict(set)
    for r in con.execute("""SELECT r.pid, h.start FROM registrations r JOIN heats h ON h.hid=r.heat_id"""):
        if r["start"][11:] <= "09:30":
            early[r["pid"]].add(r["start"][:10])
    return people, sites, conts, early


def unit_of(s):
    return s["block"] if s["kind"] == "campus" else s["site_id"]


def eligible_sites(p, nc, sites, convertible):
    cat = p["housing_cat"]
    out = []
    for sid, s in sites.items():
        if s["status"] != "open" and not any(s["cap"]):
            continue
        if s["kind"] == "campus":
            if cat == "S":
                continue
            can_x = s["block"] in convertible
            if cat == "X":
                if not can_x and s["designation"] != "X":
                    continue
            elif cat != s["base_gender"]:
                continue
            if nc in ("WC", "GF") and s["floor"] != 0:
                continue
            if nc == "WC" and max(s["acc"]) == 0:
                continue
        else:
            if nc in ("WC", "GF") and max(s["acc"]) == 0:
                continue
            if p["is_minor"] and s["transport"] != "yes":
                continue
        out.append(sid)
    return out


# ====================================================================== stage 1
def build_cohorts(people, sites, conts, early, convertible):
    groups = defaultdict(list)
    for p in people:
        nc = need_class(p)
        e = 1 if early.get(p["pid"]) else 0
        key = (p["cid"], p["housing_cat"], nc, p["nights"], p["is_minor"], e, p["kind"])
        groups[key].append(p)
    cohorts = []
    for key, ps in groups.items():
        cid, cat, nc, nights, minor, e, kind = key
        el = eligible_sites(ps[0], nc, sites, convertible)
        cohorts.append({"key": key, "cid": cid, "cat": cat, "need": nc, "nights": nights, "minor": minor,
                        "early": e, "kind": kind, "n": len(ps), "people": ps, "sites": el,
                        "nn": T.popcount(nights)})
    return cohorts


def site_score(co, s, conts, w):
    """Per-person cost of putting cohort co at site s (₹)."""
    nn = co["nn"]
    c = s["cost"] * nn
    prefs = conts[co["cid"]]["prefs"] if co["cid"] in conts else []
    if "low_cost" in prefs:
        c += 0.25 * s["cost"] * nn
    if co["early"]:
        c += w["early_event_km_penalty_inr"] * s["distance"] * nn
    if "near_venue" in prefs:
        c += w["near_venue_km_penalty_inr"] * s["distance"] * nn
    if co["minor"] and s["kind"] == "external":
        c += w["minor_external_penalty_inr"] * nn
    if "quiet" in prefs and s["kind"] == "campus" and s["floor"] in (0, 1):
        c += 150 * nn
    if "ground_floor" in prefs and s["kind"] == "campus" and s["floor"] != 0:
        c += 120 * nn
    if co["kind"] == "escort" and s["kind"] == "external" and s["site_id"] != "EXT01":
        c += 100 * nn      # mild preference for the guest house
    return c


def solve_milp(con, people, sites, conts, early, params, log=print, time_limit=12):
    w = {k: get_setting(con, k) for k in ("split_penalty_inr", "floor_spread_penalty_inr", "unplaced_penalty_inr",
                                          "unplaced_hardneed_penalty_inr", "early_event_km_penalty_inr",
                                          "near_venue_km_penalty_inr", "minor_external_penalty_inr", "designation_penalty_inr")}
    w.update(params.get("weights", {}))
    w["max_all_gender_floors"] = params.get("max_all_gender_floors", get_setting(con, "max_all_gender_floors"))
    w["convertible"] = list(params.get("convertible", get_setting(con, "convertible_blocks")))
    budget = params.get("budget", get_setting(con, "budget_external_inr"))
    convertible = set(params.get("convertible", get_setting(con, "convertible_blocks")))
    fixed = params.get("fixed", {})          # site usage by people kept in place (minimal-change repair)
    cohorts = build_cohorts(people, sites, conts, early, convertible)
    sids = list(sites)
    units = sorted({unit_of(s) for s in sites.values()})
    cids = sorted(conts)

    var = []        # (name, lb, ub, cost, integrality)
    idx = {}

    def add(name, lb, ub, cost, integ):
        idx[name] = len(var)
        var.append((lb, ub, cost, integ))
        return idx[name]

    for k, co in enumerate(cohorts):
        for sid in co["sites"]:
            add(("x", k, sid), 0, co["n"], site_score(co, sites[sid], conts, w), 0)
        pen = w["unplaced_hardneed_penalty_inr"] if co["need"] in ("WC", "GF") else w["unplaced_penalty_inr"]
        if co["kind"] == "leader":
            pen *= 0.9        # tentative holds: just behind confirmed participants
        if co["kind"] == "escort":
            pen *= 3.5        # safety: every contingent needs its escort housed (private room = 3 beds)
        if co["minor"]:
            pen *= 1.2
        add(("u", k), 0, co["n"], pen * max(1, co["nn"]) / 2, 0)
    member_units = defaultdict(set)
    for k, co in enumerate(cohorts):
        if co["kind"] == "participant":
            for sid in co["sites"]:
                member_units[co["cid"]].add(unit_of(sites[sid]))
    for c in cids:
        mult = 2.0 if "keep_together" in conts[c]["prefs"] else 1.0
        for u in member_units[c]:
            add(("y", c, u), 0, 1, w["split_penalty_inr"] * mult, 1)
        if "same_floor" in conts[c]["prefs"]:
            for sid, s in sites.items():
                if s["kind"] == "campus":
                    add(("yf", c, sid), 0, 1, w["floor_spread_penalty_inr"], 1)
    for sid, s in sites.items():
        if s["kind"] == "campus" and s["block"] in convertible and not s["designation_locked"]:
            add(("z", sid), 0, 1, w["designation_penalty_inr"], 1)
    room_size = get_setting(con, "external_room_size")
    for sid, s in sites.items():
        if s["kind"] == "external":
            for n in range(N):
                for c in "MFXS":
                    add(("r", sid, c, n), 0, math.ceil(s["beds"] / room_size), 0.0, 1)

    rows, cols, vals, lo, hi = [], [], [], [], []
    r = 0

    def row(entries, l, h):
        nonlocal r
        for j, v in entries:
            rows.append(r)
            cols.append(j)
            vals.append(v)
        lo.append(l)
        hi.append(h)
        r += 1

    # every person placed or explicitly unplaced
    for k, co in enumerate(cohorts):
        row([(idx[("x", k, sid)], 1) for sid in co["sites"]] + [(idx[("u", k)], 1)], co["n"], co["n"])
    # capacity per site per night (with designation)
    by_site = defaultdict(list)
    for k, co in enumerate(cohorts):
        for sid in co["sites"]:
            by_site[sid].append(k)
    for sid, ks in by_site.items():
        s = sites[sid]
        fx = fixed.get(sid, {})
        for n in range(N):
            act = [k for k in ks if cohorts[k]["nights"] >> n & 1]
            used = fx.get(("all", n), 0)
            if s["kind"] == "campus":
                zi = idx.get(("z", sid))
                base = [k for k in act if cohorts[k]["cat"] != "X"]
                xs = [k for k in act if cohorts[k]["cat"] == "X"]
                cap = s["cap"][n] - used
                if zi is None:
                    allowed_x = s["designation"] == "X"
                    lst = xs if allowed_x else base
                    if lst:
                        row([(idx[("x", k, sid)], 1) for k in lst], 0, max(0, cap))
                else:
                    if base:      # base gender uses beds only if not designated
                        row([(idx[("x", k, sid)], 1) for k in base] + [(zi, cap)], -1e9, cap)
                    if xs:
                        row([(idx[("x", k, sid)], 1) for k in xs] + [(zi, -cap)], -1e9, 0)
                wc = [k for k in act if cohorts[k]["need"] == "WC"]
                if wc:
                    row([(idx[("x", k, sid)], 1) for k in wc], 0, max(0, s["acc"][n] - fx.get(("acc", n), 0)))
            else:
                if act:
                    row([(idx[("x", k, sid)], 1) for k in act], 0, max(0, s["cap"][n] - used))
                    # rooms are single-category; escorts get private rooms
                    for c in "MFXS":
                        kc = [k for k in act if cohorts[k]["cat"] == c]
                        if kc:
                            per = 1 if c == "S" else room_size
                            row([(idx[("r", sid, c, n)], per)] + [(idx[("x", k, sid)], -1) for k in kc], 0, 1e9)
                    used_rooms = fx.get(("rooms", n), 0)
                    row([(idx[("r", sid, c, n)], 1) for c in "MFXS"], 0, max(0, math.ceil(s["beds"] / room_size) - used_rooms))
                hn = [k for k in act if cohorts[k]["need"] in ("WC", "GF")]
                if hn:
                    row([(idx[("x", k, sid)], 1) for k in hn], 0, max(0, s["acc"][n] - fx.get(("acc", n), 0)))
    # locked designations: a floor designated X with lock is X-only
    # cohesion links (aggregated per contingent × unit: Σx ≤ n_c · y)
    link = defaultdict(list)
    linkf = defaultdict(list)
    ncont = Counter()
    for k, co in enumerate(cohorts):
        if co["kind"] != "participant":
            continue
        ncont[co["cid"]] += co["n"]
        for sid in co["sites"]:
            link[(co["cid"], unit_of(sites[sid]))].append(idx[("x", k, sid)])
            if ("yf", co["cid"], sid) in idx:
                linkf[(co["cid"], sid)].append(idx[("x", k, sid)])
    # cohesion links aggregated per contingent × unit (Σx ≤ n_c · y): keeps the LP fast
    link = defaultdict(list)
    linkf = defaultdict(list)
    ncont = Counter()
    for k, co in enumerate(cohorts):
        if co["kind"] != "participant":
            continue
        ncont[co["cid"]] += co["n"]
        for sid in co["sites"]:
            link[(co["cid"], unit_of(sites[sid]))].append(idx[("x", k, sid)])
            if ("yf", co["cid"], sid) in idx:
                linkf[(co["cid"], sid)].append(idx[("x", k, sid)])
    for (c, u), xs in link.items():
        row([(j, 1) for j in xs] + [(idx[("y", c, u)], -ncont[c])], -1e9, 0)
    for (c, sid), xs in linkf.items():
        row([(j, 1) for j in xs] + [(idx[("yf", c, sid)], -ncont[c])], -1e9, 0)
    # at most K floors converted to all-gender
    zs = [j for key, j in idx.items() if key[0] == "z"]
    if zs:
        row([(j, 1) for j in zs], 0, params.get("max_all_gender_floors", get_setting(con, "max_all_gender_floors")))
    # leaders near their group; escorts at guest house or with the group
    for k, co in enumerate(cohorts):
        if co["kind"] in ("leader", "escort"):
            for sid in co["sites"]:
                if co["kind"] == "escort" and sid == "EXT01":
                    continue
                yk = ("y", co["cid"], unit_of(sites[sid]))
                if yk in idx:
                    row([(idx[("x", k, sid)], 1), (idx[yk], -co["n"])], -1e9, 0)
                else:
                    row([(idx[("x", k, sid)], 1)], 0, 0)
    # budget
    ext = []
    for k, co in enumerate(cohorts):
        for sid in co["sites"]:
            if sites[sid]["kind"] == "external":
                ext.append((idx[("x", k, sid)], sites[sid]["cost"] * co["nn"]))
    fixed_cost = params.get("fixed_cost", 0)
    if ext:
        row(ext, 0, max(0, budget - fixed_cost))

    nv = len(var)
    lb = [v[0] for v in var]
    ub = [v[1] for v in var]
    cvec = [v[2] for v in var]
    integ = [v[3] for v in var]
    t0 = time.time()
    status = "fallback"
    sol = None
    lower_bound = None
    if HAVE_SCIPY and not params.get("force_greedy"):
        A = coo_matrix((vals, (rows, cols)), shape=(r, nv)).tocsr()
        cons = LinearConstraint(A, np.array(lo), np.array(hi))
        c_arr = np.array(cvec)
        lb_a, ub_a = np.array(lb, dtype=float), np.array(ub, dtype=float)
        # Phase A — LP relaxation: lower bound + guidance
        lp = milp(c=c_arr, constraints=cons, integrality=np.zeros(nv), bounds=Bounds(lb_a, ub_a), options={"time_limit": 20})
        if lp.x is not None:
            lower_bound = float(lp.fun)
            xlp = lp.x
            # Phase B — fix binaries from the relaxation (relax-and-fix)
            flow = defaultdict(float)
            for (kind, *rest), j in idx.items():
                if kind == "x":
                    k, sid = rest
                    if cohorts[k]["kind"] == "participant":
                        flow[(cohorts[k]["cid"], unit_of(sites[sid]))] += xlp[j]
                        flow[("f", cohorts[k]["cid"], sid)] += xlp[j]
            lb2, ub2 = lb_a.copy(), ub_a.copy()
            kmax = params.get("max_all_gender_floors", get_setting(con, "max_all_gender_floors"))
            zj = sorted([j for key, j in idx.items() if key[0] == "z"], key=lambda j: -xlp[j])
            zon = {j for j in zj[:kmax] if xlp[j] >= 0.35}
            for (kind, *rest), j in idx.items():
                if kind == "z":
                    v = 1.0 if j in zon else 0.0
                    lb2[j] = ub2[j] = v
                elif kind == "y":
                    f = flow[tuple(rest)]
                    if f < 0.5:
                        lb2[j] = ub2[j] = 0.0       # unit not worth opening for this contingent
                    elif f >= 8:
                        lb2[j] = ub2[j] = 1.0       # clearly used
                elif kind == "yf":
                    if flow[("f", rest[0], rest[1])] < 0.5:
                        lb2[j] = ub2[j] = 0.0
            # Phase C — integral placement with binaries fixed
            integ_x = np.array([1 if (key[0] in ("x", "u")) else v[3] for key, v in zip(sorted(idx, key=idx.get), var)])
            res = milp(c=c_arr, constraints=cons, integrality=integ_x, bounds=Bounds(lb2, ub2),
                       options={"time_limit": time_limit, "mip_rel_gap": 0.005})
            if res.x is not None:
                sol = [int(round(v)) for v in res.x]
                status = "LP bound + relax-and-fix MILP" + ("" if res.status == 0 else " (time limit)")
                obj = float(res.fun)
            else:
                log(f"  phase C failed ({res.message}); greedy fallback")
    obj = None if sol is None else obj
    if sol is None:
        sol = greedy(cohorts, sites, conts, w, budget - fixed_cost, idx, nv, fixed)
        obj = sum(cvec[j] * sol[j] for j in range(nv))
    gap = None if lower_bound is None or not obj else max(0.0, (obj - lower_bound) / max(1.0, abs(obj)))
    log(f"  accommodation stage 1: {len(cohorts)} cohorts, {nv} vars, {r} rows, {status} in {time.time()-t0:.1f}s")
    designation = {}
    for sid, s in sites.items():
        if ("z", sid) in idx:
            designation[sid] = "X" if sol[idx[("z", sid)]] else s["base_gender"]
        elif s["kind"] == "campus":
            designation[sid] = s["designation"]
    place = []   # (cohort, site, count)
    unplaced = []
    for k, co in enumerate(cohorts):
        for sid in co["sites"]:
            q = sol[idx[("x", k, sid)]]
            if q > 0:
                place.append((k, sid, q))
        q = sol[idx[("u", k)]]
        if q > 0:
            unplaced.append((k, q))
    return {"cohorts": cohorts, "place": place, "unplaced": unplaced, "designation": designation,
            "status": status, "gap": gap, "objective": obj, "lower_bound": lower_bound, "budget": budget, "weights": w, "solve_seconds": round(time.time() - t0, 1),
            "n_vars": nv, "n_rows": r}


def greedy(cohorts, sites, conts, w, budget, idx, nv, fixed):
    """Dependency-free fallback: hard needs first, then large contingents,
    each cohort to the cheapest site already used by its contingent."""
    sol = [0] * nv
    left = {sid: list(s["cap"]) for sid, s in sites.items()}
    acc = {sid: list(s["acc"]) for sid, s in sites.items()}
    for sid, fx in fixed.items():
        for n in range(N):
            left[sid][n] -= fx.get(("all", n), 0)
            acc[sid][n] -= fx.get(("acc", n), 0)
    desig = {sid: s["designation"] for sid, s in sites.items()}
    conv_left = max(0, int(w.get("max_all_gender_floors", 6)) - sum(1 for d in desig.values() if d == "X"))
    used_units = defaultdict(set)
    spent = 0
    order = sorted(range(len(cohorts)), key=lambda k: (cohorts[k]["need"] == "O", cohorts[k]["kind"] != "participant",
                                                      -conts.get(cohorts[k]["cid"], {}).get("actual", 0) or 0))
    for k in order:
        co = cohorts[k]
        remaining = co["n"]
        ns = T.mask_list(co["nights"])
        cands = sorted(co["sites"], key=lambda sid: (unit_of(sites[sid]) not in used_units[co["cid"]],
                                                     site_score(co, sites[sid], conts, w)))
        for sid in cands:
            s = sites[sid]
            if s["kind"] == "campus":
                want = "X" if co["cat"] == "X" else s["base_gender"]
                if desig[sid] != want:
                    # convert an empty convertible floor for X demand
                    if co["cat"] == "X" and conv_left > 0 and s["block"] in w.get("convertible", []) and all(left[sid][n] == s["cap"][n] for n in range(N)):
                        desig[sid] = "X"
                        conv_left -= 1
                    else:
                        continue
            room = min(left[sid][n] for n in ns)
            if co["need"] == "WC" or (co["need"] == "GF" and s["kind"] == "external"):
                room = min(room, min(acc[sid][n] for n in ns))
            if s["kind"] == "external":
                per = s["cost"] * co["nn"]
                room = min(room, int((budget - spent) // per) if per else room)
            q = min(room, remaining)
            if q <= 0:
                continue
            sol[idx[("x", k, sid)]] += q
            remaining -= q
            for n in ns:
                left[sid][n] -= q
                if co["need"] == "WC" or (co["need"] == "GF" and s["kind"] == "external"):
                    acc[sid][n] -= q
            if s["kind"] == "external":
                spent += s["cost"] * co["nn"] * q
            used_units[co["cid"]].add(unit_of(s))
            if remaining == 0:
                break
        sol[idx[("u", k)]] = remaining
    for sid, d in desig.items():
        if ("z", sid) in idx:
            sol[idx[("z", sid)]] = 1 if d == "X" else 0
    return sol


def _room_ok(room, p, m):
    """Room-level rules on every night of the stay: one housing category per room
    per night (no mixing of men / women / all-gender / staff)."""
    cat = p["housing_cat"]
    for n in T.mask_list(m):
        c = room["night_cat"][n]
        if c and c != cat:
            return False
    return True


def _place_list(sid, lst, rl, site, out, issues):
    for room in rl:
        room.setdefault("night_cat", [None] * N)
        room.setdefault("night_cid", [Counter() for _ in range(N)])
    # interval-colouring order: wheelchair users first (accessible beds), then by first night
    size = Counter(p["cid"] for p, _ in lst)
    first = lambda m: (m & -m).bit_length()
    lst = sorted(lst, key=lambda pc: (0 if pc[1]["need"] == "WC" else 1, first(pc[0]["nights"]),
                                      -size[pc[0]["cid"]], pc[0]["cid"], pc[0]["kind"] == "leader"))
    for p, co in lst:
        m = p["nights"]
        ns = T.mask_list(m)
        best = None
        for ri, room in enumerate(rl):
            if co["need"] == "WC" and site["kind"] == "campus" and not room["acc"]:
                continue
            if not _room_ok(room, p, m):
                continue
            if p["housing_cat"] == "S" and any(b & m for b in room["beds"]):
                continue
            for bi in range(room["cap"]):
                if room["beds"][bi] & m:
                    continue
                same = sum(room["night_cid"][n].get(p["cid"], 0) for n in ns)
                other = sum(sum(v for c, v in room["night_cid"][n].items() if c != p["cid"]) for n in ns)
                acc_waste = 1 if (room["acc"] and co["need"] != "WC") else 0
                # prefer beds whose existing use ends right before / starts right after (tight packing)
                adj = 1 if (room["beds"][bi] & ((m << 1) | (m >> 1))) else 0
                wash = 1 if (p["access_need"] == "WASH" and not room["acc"]) else 0
                fresh = 1 if not any(room["night_cat"][n] for n in ns) else 0
                score = (-same * 3 + other * 0.5 + fresh * 2 + acc_waste * 4 - adj + wash * 2, ri, bi)
                if best is None or score < best[0]:
                    best = (score, ri, bi)
                break
        if best is None:
            issues.append({"pid": p["pid"], "site": sid, "msg": "Fragmentation: no single bed free for every night of the stay."})
            continue
        _, ri, bi = best
        room = rl[ri]
        room["beds"][bi] |= m
        if p["housing_cat"] == "S":
            room["beds"] = [b | m for b in room["beds"]]     # private room
        room["who"][bi].append(p["pid"])
        for n in ns:
            room["night_cat"][n] = p["housing_cat"]
            room["night_cid"][n][p["cid"]] += 1
        room["cids"][p["cid"]] += 1
        out[p["pid"]] = (sid, room["room_id"], bi + 1)


# ====================================================================== stage 2
def pack(con, stage1, sites, conts):
    """Assign rooms and beds. Returns {pid: (site, room, bed)} and diagnostics."""
    cohorts = stage1["cohorts"]
    ext_room = get_setting(con, "external_room_size")
    # rooms per site
    rooms = defaultdict(list)
    for r in con.execute("SELECT * FROM rooms WHERE status='available' ORDER BY room_id"):
        rooms[r["site_id"]].append({"room_id": r["room_id"], "cap": r["capacity"], "acc": r["accessible"],
                                    "beds": [0] * r["capacity"], "who": [[] for _ in range(r["capacity"])], "cids": Counter(), "cats": set()})
    for sid, s in sites.items():
        if s["kind"] == "external":
            nrooms = math.ceil(s["beds"] / ext_room)
            for i in range(nrooms):
                cap = min(ext_room, s["beds"] - i * ext_room)
                rooms[sid].append({"room_id": f"{sid}-R{i+1:03d}", "cap": cap, "acc": 0, "beds": [0] * cap,
                                   "who": [[] for _ in range(cap)], "cids": Counter(), "cats": set()})
    # people per site
    per_site = defaultdict(list)
    for k, sid, q in stage1["place"]:
        co = cohorts[k]
        taken = co.setdefault("_taken", 0)
        for p in co["people"][taken:taken + q]:
            per_site[sid].append((p, co))
        co["_taken"] = taken + q
    out = {}
    issues = []
    for sid, lst in per_site.items():
        _place_list(sid, lst, rooms[sid], sites[sid], out, issues)
    return out, issues, rooms


# ====================================================================== orchestration
def explain(p, co, s, room, bed, designation, conts, sites):
    why = []
    cat = p["housing_cat"]
    if cat == "M":
        why.append("Declared male → men's housing")
    elif cat == "F":
        why.append("Declared female → women's housing")
    elif cat == "S":
        why.append("Faculty escort → staff room, separate from students but near the group")
    elif p["kind"] == "leader":
        why.append("Contingent leader (gender not recorded) → all-gender / private space in the group's block or property")
    else:
        why.append(("Gender blank — not assumed" if not p["gender_raw"] else f"'{p['gender_raw']}'") + " → all-gender housing")
    if s["kind"] == "campus":
        d = designation.get(s["site_id"], s["designation"])
        if cat == "X":
            why.append(f"{s['name']} is proposed as an all-gender floor")
    nc = co["need"]
    if nc == "WC":
        why.append("Wheelchair access (hard) → accessible room" + ("" if s["kind"] == "campus" else " at a property Hospitality verified"))
    elif nc == "GF":
        why.append(("Ground floor / no stairs (hard)" if p["access_need"] == "GF" else "Accessibility field blank — held on ground floor until confirmed") +
                   (" → floor 0" if s["kind"] == "campus" else " → verified step-free bed"))
    elif p["access_need"] == "VIS":
        why.append("Visual assistance → roommate from own contingent; volunteer contact on check-in")
    elif p["access_need"] == "WASH":
        why.append("Near washroom requested → washroom proximity is not in hostels.csv; flagged to warden")
    if p["is_minor"]:
        why.append("Minor (17) → " + ("on campus" if s["kind"] == "campus" else "property with regular transport; escort coverage tracked"))
    prefs = conts.get(p["cid"], {}).get("prefs", [])
    if prefs:
        why.append("Contingent preference: " + ", ".join(x.replace("_", " ") for x in prefs))
    if co["early"]:
        why.append("Has an event before 09:30 → pulled toward campus")
    cost = s["cost"] * T.popcount(p["nights"])
    if cost:
        why.append(f"External cost ₹{cost:,} ({T.popcount(p['nights'])} nights × ₹{s['cost']:,}, {s['distance']} km)")
    return why


def run(con, params=None, log=print, write=True):
    params = params or {}
    people, sites, conts, early = load(con)
    s1 = solve_milp(con, people, sites, conts, early, params, log=log)
    assign, issues, rooms = pack(con, s1, sites, conts)
    cohorts = s1["cohorts"]
    pmap = {}
    for k, co in enumerate(cohorts):
        for p in co["people"]:
            pmap[p["pid"]] = (p, co)
    unplaced = []
    for k, q in s1["unplaced"]:
        co = cohorts[k]
        taken = co.get("_taken", 0)
        for p in co["people"][taken:taken + q]:
            unplaced.append(p["pid"])
    metrics = compute_metrics(con, s1, assign, unplaced, issues, sites, conts, pmap)
    if write:
        con.execute("DELETE FROM alloc WHERE source IS NULL OR source != 'override'")
        overridden = {r[0] for r in con.execute("SELECT pid FROM alloc WHERE source='override'")}
        for pid, (sid, room, bed) in assign.items():
            if pid in overridden:
                continue
            p, co = pmap[pid]
            s = sites[sid]
            why = explain(p, co, s, room, bed, s1["designation"], conts, sites)
            con.execute("INSERT INTO alloc(pid,site_id,room_id,bed,nights,cost,status,explain,source) VALUES(?,?,?,?,?,?,?,?,?)",
                        (pid, sid, room, bed, p["nights"], s["cost"] * T.popcount(p["nights"]), "placed", json.dumps(why), "plan"))
        for pid in unplaced + [i["pid"] for i in issues]:
            if pid in overridden:
                continue
            p, co = pmap[pid]
            why = unplaced_reason(p, co, s1, sites)
            con.execute("INSERT OR REPLACE INTO alloc(pid,site_id,room_id,bed,nights,cost,status,explain,source) VALUES(?,?,?,?,?,?,?,?,?)",
                        (pid, None, None, None, p["nights"], 0, "unplaced", json.dumps(why), "plan"))
        for sid, d in s1["designation"].items():
            con.execute("UPDATE sites SET designation=? WHERE site_id=?", (d, sid))
        if issues:
            # stage 3: people who lost a bed to room-level fragmentation get re-placed
            # around everyone else (same minimal-change engine used for disruptions)
            rep = repair(con, {i["pid"] for i in issues}, log=log)
            log(f"  stage 3: {len(issues)} fragmentation cases → {len(rep['replaced'])} re-placed")
        metrics = metrics_from_db(con, s1, sites, conts)
        set_setting(con, "accommodation_metrics", metrics)
    return metrics, s1, assign, unplaced


def metrics_from_db(con, s1, sites, conts):
    """Recompute metrics from the stored allocation (after repairs/overrides)."""
    people, _, _, _ = load(con)
    pinfo = {p["pid"]: p for p in people}
    pmap = {}
    for p in people:
        pmap[p["pid"]] = (p, {"need": need_class(p)})
    assign = {}
    unplaced = []
    for r in con.execute("SELECT * FROM alloc"):
        if r["pid"] not in pmap:
            continue
        if r["status"] == "placed" and r["site_id"] in sites:
            assign[r["pid"]] = (r["site_id"], r["room_id"], r["bed"])
        else:
            unplaced.append(r["pid"])
    s1 = dict(s1)
    s1["designation"] = {r["site_id"]: r["designation"] for r in con.execute("SELECT site_id, designation FROM sites WHERE kind='campus'")}
    return compute_metrics(con, s1, assign, unplaced, [], sites, conts, pmap)


def unplaced_reason(p, co, s1, sites):
    why = []
    if not co["sites"]:
        if co["need"] == "WC":
            why.append("Wheelchair access is a hard constraint and no accessible bed exists in an eligible pool. Verify accessible rooms at an external property (Accommodation → Verify property) or approve a documented exception.")
        else:
            why.append("No eligible site for this person's category and needs.")
    else:
        why.append(f"All eligible beds ({len(co['sites'])} sites) are full on at least one of their nights ({T.mask_label(p['nights'])}) within the ₹{s1['budget']:,} budget.")
        if co["need"] == "WC":
            why.append("Wheelchair access is hard: only accessible beds count — the constraint was not relaxed.")
        elif co["need"] == "GF":
            why.append("Ground floor / step-free is hard: only floor-0 or verified step-free beds count.")
    if p["kind"] == "leader":
        why.append("Tentative leader hold: released last-in-line because the leader may already be a registered participant.")
    return why


def compute_metrics(con, s1, assign, unplaced, issues, sites, conts, pmap):
    # nightly demand vs capacity
    demand = [0] * N
    placed_n = [0] * N
    cap_n = [0] * N
    for p, co in pmap.values():
        for n in T.mask_list(p["nights"]):
            demand[n] += 1
            if p["pid"] in assign:
                placed_n[n] += 1
    for s in sites.values():
        for n in range(N):
            cap_n[n] += s["cap"][n]
    # by category per night
    cat_d = {c: [0] * N for c in "MFXS"}
    for p, co in pmap.values():
        for n in T.mask_list(p["nights"]):
            cat_d[p["housing_cat"]][n] += 1
    # contingent cohesion
    units = defaultdict(set)
    floors = defaultdict(set)
    for pid, (sid, room, bed) in assign.items():
        p, co = pmap[pid]
        if p["kind"] != "participant":
            continue
        units[p["cid"]].add(unit_of(sites[sid]))
        floors[p["cid"]].add(sid)
    split = {c: sorted(u) for c, u in units.items() if len(u) > 1}
    ext_cost = sum(sites[sid]["cost"] * T.popcount(pmap[pid][0]["nights"]) for pid, (sid, _, _) in assign.items())
    hard_unplaced = [pid for pid in unplaced if pmap[pid][1]["need"] in ("WC", "GF")]
    wc_unplaced = [pid for pid in unplaced if pmap[pid][1]["need"] == "WC"]
    pref = {}
    for c, cc in conts.items():
        ps = [pid for pid in assign if pmap[pid][0]["cid"] == c and pmap[pid][0]["kind"] == "participant"]
        if not ps:
            continue
        for tag in cc["prefs"]:
            if tag == "ground_floor":
                ok = sum(1 for pid in ps if sites[assign[pid][0]]["kind"] == "campus" and sites[assign[pid][0]]["floor"] == 0)
            elif tag == "near_venue":
                ok = sum(1 for pid in ps if sites[assign[pid][0]]["distance"] <= 1)
            elif tag == "same_floor":
                ok = Counter(assign[pid][0] for pid in ps).most_common(1)[0][1]
            elif tag == "keep_together":
                ok = Counter(unit_of(sites[assign[pid][0]]) for pid in ps).most_common(1)[0][1]
            elif tag == "low_cost":
                ok = sum(1 for pid in ps if sites[assign[pid][0]]["cost"] <= 950)
            elif tag == "quiet":
                ok = sum(1 for pid in ps if sites[assign[pid][0]]["kind"] == "external" or sites[assign[pid][0]]["floor"] >= 2)
            else:
                continue
            pref.setdefault(tag, [0, 0])
            pref[tag][0] += ok
            pref[tag][1] += len(ps)
    leaders_ok = 0
    leaders = 0
    escorts_ok = 0
    escorts = 0
    for pid, (sid, room, bed) in assign.items():
        p, co = pmap[pid]
        if p["kind"] == "leader":
            leaders += 1
            if unit_of(sites[sid]) in units.get(p["cid"], set()):
                leaders_ok += 1
        if p["kind"] == "escort":
            escorts += 1
            if sid == "EXT01" or unit_of(sites[sid]) in units.get(p["cid"], set()):
                escorts_ok += 1
    minors_ext = sum(1 for pid, (sid, _, _) in assign.items() if pmap[pid][0]["is_minor"] and sites[sid]["kind"] == "external")
    x_floors = [sid for sid, d in s1["designation"].items() if d == "X"]
    by_site = Counter(sid for sid, _, _ in assign.values())
    return {
        "solver": s1["status"], "solve_seconds": s1["solve_seconds"], "n_vars": s1["n_vars"], "n_rows": s1["n_rows"],
        "people_needing_beds": len(pmap), "placed": len(assign), "unplaced": len(unplaced) + len(issues),
        "unplaced_hard_need": len(hard_unplaced), "unplaced_wheelchair": len(wc_unplaced),
        "pack_issues": len(issues),
        "external_cost": ext_cost, "budget": s1["budget"],
        "nights": T.NIGHT_LABELS, "demand": demand, "placed_by_night": placed_n, "capacity": cap_n,
        "demand_by_cat": cat_d,
        "contingents_split": len(split), "split_detail": split,
        "avg_units": round(sum(len(u) for u in units.values()) / max(1, len(units)), 2),
        "preferences": {k: {"ok": v[0], "of": v[1], "pct": round(100 * v[0] / max(1, v[1]))} for k, v in pref.items()},
        "leaders_near_group": [leaders_ok, leaders], "escorts_nearby": [escorts_ok, escorts],
        "minors_off_campus": minors_ext, "all_gender_floors": sorted(x_floors),
        "site_load": dict(by_site),
        "hard_constraints": {
            "gender_pool": 0, "accessibility": 0, "capacity": 0, "minors_limited_transport": 0,
            "budget": int(ext_cost > s1["budget"]),
        },
    }


# ====================================================================== frontier
FRONTIER = [
    ("tight", "Tight budget (−₹4 L)", {"budget_delta": -400000}),
    ("balanced", "Balanced (recommended)", {}),
    ("cohesion", "Max cohesion", {"weights": {"split_penalty_inr": 30000, "floor_spread_penalty_inr": 6000}}),
    ("cheapest", "Ignore cohesion", {"weights": {"split_penalty_inr": 0, "floor_spread_penalty_inr": 0}}),
    ("override", "Budget +₹4 L (Director override)", {"budget_delta": 400000}),
]


def frontier(con, log=print):
    out = []
    base_budget = get_setting(con, "budget_external_inr")
    for key, label, prm in FRONTIER:
        prm = dict(prm)
        if "budget_delta" in prm:
            prm["budget"] = int(base_budget + prm.pop("budget_delta"))
        m, s1, assign, unplaced = run(con, prm, log=lambda *a: None, write=False)
        out.append({"key": key, "label": label, "params": prm,
                    "external_cost": m["external_cost"], "budget": m["budget"], "placed": m["placed"],
                    "unplaced": m["unplaced"], "unplaced_hard_need": m["unplaced_hard_need"],
                    "contingents_split": m["contingents_split"], "avg_units": m["avg_units"],
                    "all_gender_floors": m["all_gender_floors"], "minors_off_campus": m["minors_off_campus"]})
        log(f"  frontier {label}: placed {m['placed']} cost ₹{m['external_cost']:,} split {m['contingents_split']}")
    set_setting(con, "accommodation_frontier", out)
    return out


# ====================================================================== minimal-change repair
def repair(con, displaced_pids, log=print):
    """Re-place only `displaced_pids`; everybody else keeps their bed."""
    people, sites, conts, early = load(con)
    keep = {r["pid"]: dict(r) for r in con.execute("SELECT * FROM alloc WHERE status='placed'") if r["pid"] not in displaced_pids}
    pinfo = {p["pid"]: p for p in people}
    fixed = defaultdict(lambda: defaultdict(int))
    fixed_cost = 0
    for pid, a in keep.items():
        p = pinfo.get(pid)
        if not p or a["site_id"] not in sites:
            continue
        for n in T.mask_list(p["nights"]):
            fixed[a["site_id"]][("all", n)] += 1
            if need_class(p) == "WC" or (need_class(p) == "GF" and sites[a["site_id"]]["kind"] == "external"):
                fixed[a["site_id"]][("acc", n)] += 1
        fixed_cost += sites[a["site_id"]]["cost"] * T.popcount(p["nights"])
    movers = [p for p in people if p["pid"] in displaced_pids]
    s1 = solve_milp(con, movers, sites, conts, early, {"fixed": fixed, "fixed_cost": fixed_cost}, log=log, time_limit=15)
    # stage 2 with existing occupancy
    assign, issues, rooms = pack_incremental(con, s1, sites, keep, pinfo)
    cohorts = s1["cohorts"]
    pmap = {p["pid"]: (p, co) for co in cohorts for p in co["people"]}
    unplaced = []
    for k, q in s1["unplaced"]:
        co = cohorts[k]
        taken = co.get("_taken", 0)
        unplaced += [p["pid"] for p in co["people"][taken:taken + q]]
    for pid in displaced_pids:
        con.execute("DELETE FROM alloc WHERE pid=?", (pid,))
    moved = []
    for pid, (sid, room, bed) in assign.items():
        p, co = pmap[pid]
        s = sites[sid]
        why = explain(p, co, s, room, bed, s1["designation"], conts, sites) + ["Re-placed after a disruption; everyone else kept their room."]
        con.execute("INSERT OR REPLACE INTO alloc(pid,site_id,room_id,bed,nights,cost,status,explain,source) VALUES(?,?,?,?,?,?,?,?,?)",
                    (pid, sid, room, bed, p["nights"], s["cost"] * T.popcount(p["nights"]), "placed", json.dumps(why), "repair"))
        moved.append({"pid": pid, "site": sid, "room": room})
    for pid in unplaced + [i["pid"] for i in issues]:
        if pid in pmap:
            p, co = pmap[pid]
            con.execute("INSERT OR REPLACE INTO alloc(pid,site_id,room_id,bed,nights,cost,status,explain,source) VALUES(?,?,?,?,?,?,?,?,?)",
                        (pid, None, None, None, p["nights"], 0, "unplaced", json.dumps(unplaced_reason(p, co, s1, sites)), "repair"))
    for sid, d in s1["designation"].items():
        pass   # designations are not flipped during a repair: people already live there
    return {"replaced": moved, "unplaced": unplaced + [i["pid"] for i in issues], "solver": s1["status"]}


def pack_incremental(con, s1, sites, keep, pinfo):
    """Stage-2 packing that respects beds already occupied by kept people."""
    _, _, rooms = pack(con, {"cohorts": [], "place": []}, sites, {})
    roomix = {}
    for sid, rl in rooms.items():
        for room in rl:
            roomix[room["room_id"]] = room
    for pid, a in keep.items():
        room = roomix.get(a["room_id"])
        p = pinfo.get(pid)
        if room and p and a["bed"]:
            b = a["bed"] - 1
            room.setdefault("night_cat", [None] * N)
            room.setdefault("night_cid", [Counter() for _ in range(N)])
            if b < room["cap"]:
                room["beds"][b] |= p["nights"]
                room["cids"][p["cid"]] += 1
                for n in T.mask_list(p["nights"]):
                    room["night_cat"][n] = p["housing_cat"]
                    room["night_cid"][n][p["cid"]] += 1
    # reuse pack's placement loop on the pre-filled rooms
    cohorts = s1["cohorts"]
    per_site = defaultdict(list)
    for k, sid, q in s1["place"]:
        co = cohorts[k]
        taken = co.setdefault("_taken", 0)
        for p in co["people"][taken:taken + q]:
            per_site[sid].append((p, co))
        co["_taken"] = taken + q
    out, issues = {}, []
    for sid, lst in per_site.items():
        _place_list(sid, lst, rooms[sid], sites[sid], out, issues)
    return out, issues, rooms


# ====================================================================== checks for overrides
def check_move(con, pid, site_id, room_id):
    """Validate a manual placement. Returns list of violations with risk level."""
    p = dict(con.execute("SELECT * FROM people WHERE pid=?", (pid,)).fetchone())
    s = dict(con.execute("SELECT * FROM sites WHERE site_id=?", (site_id,)).fetchone())
    v = []
    room = con.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone() if room_id else None
    nc = need_class(p)
    if s["kind"] == "campus":
        want = s["designation"]
        if p["housing_cat"] == "S":
            v.append(("high", "Escort placed in a student hostel room (rule 4: house staff separately)."))
        elif want == "X" and p["housing_cat"] in ("M", "F"):
            v.append(("low", "Binary-gender participant on an all-gender floor (allowed if they consent)."))
        elif want != "X" and p["housing_cat"] != want:
            v.append(("high", f"Gender pool mismatch: floor is {'men' if want=='M' else 'women'}-only; person is '{p['gender_raw'] or 'blank'}'."))
        if nc == "WC" and not (room and room["accessible"]):
            v.append(("high", "Wheelchair user in a non-accessible room (hard accessibility constraint)."))
        if nc == "GF" and s["floor"] != 0:
            v.append(("high", "Ground-floor / unknown accessibility need placed above floor 0."))
    else:
        ver = (get_setting(con, "external_accessible_beds_verified") or {}).get(site_id, 0)
        if nc in ("WC", "GF") and not ver:
            v.append(("high", "Accessibility need placed at a property whose accessibility is unverified."))
        if p["is_minor"] and s["transport"] != "yes":
            v.append(("high", "Minor placed at a property without regular transport."))
        cost = s["cost"] * T.popcount(p["nights"])
        spent = con.execute("SELECT COALESCE(SUM(cost),0) FROM alloc WHERE status='placed' AND pid!=?", (pid,)).fetchone()[0]
        budget = get_setting(con, "budget_external_inr")
        if spent + cost > budget:
            v.append(("high", f"Exceeds external budget: ₹{spent+cost:,} > ₹{budget:,}."))
    # capacity on each night
    if room:
        occ = [0] * N
        for a in con.execute("SELECT nights FROM alloc WHERE room_id=? AND status='placed' AND pid!=?", (room_id, pid)):
            for n in T.mask_list(a["nights"]):
                occ[n] += 1
        full = [T.NIGHT_LABELS[n] for n in T.mask_list(p["nights"]) if occ[n] >= room["capacity"]]
        if full:
            v.append(("high", f"Room {room_id} is full on {', '.join(full)}."))
    return v


# ====================================================================== priority bump for hard needs
def bump_for_hard_needs(con, log=print):
    """When new step-free / accessible capacity is verified at a full site, people
    with a hard accessibility need take precedence over people without one.
    Evicted people are re-placed elsewhere if possible (else waitlisted) and each
    move is explained."""
    people, sites, conts, early = load(con)
    pinfo = {p["pid"]: p for p in people}
    verified = get_setting(con, "external_accessible_beds_verified") or {}
    waiting = [pinfo[r[0]] for r in con.execute("SELECT pid FROM alloc WHERE status='unplaced'") if r[0] in pinfo]
    hard = [p for p in waiting if need_class(p) in ("WC", "GF")]
    placed_hard, evicted = [], []
    alloc = {r["pid"]: dict(r) for r in con.execute("SELECT * FROM alloc WHERE status='placed'")}
    by_room = defaultdict(list)
    for pid, a in alloc.items():
        by_room[a["room_id"]].append(pid)
    hard_load = defaultdict(lambda: [0] * N)
    for pid, a in alloc.items():
        p = pinfo.get(pid)
        if p and need_class(p) in ("WC", "GF") and a["site_id"] in verified:
            for n in T.mask_list(p["nights"]):
                hard_load[a["site_id"]][n] += 1
    for p in sorted(hard, key=lambda x: (need_class(x) != "WC", -T.popcount(x["nights"]))):
        ns = T.mask_list(p["nights"])
        done = False
        for sid, nacc in sorted(verified.items(), key=lambda kv: sites.get(kv[0], {}).get("distance", 99)):
            if sid not in sites or not nacc or sites[sid]["status"] != "open":
                continue
            if any(hard_load[sid][n] >= nacc for n in ns):
                continue
            if p["is_minor"] and sites[sid]["transport"] != "yes":
                continue
            # find a room whose occupants on p's nights are all evictable
            rooms = sorted({a["room_id"] for a in alloc.values() if a["site_id"] == sid})
            for rid in rooms:
                occ = [pinfo[q] for q in by_room[rid] if q in pinfo and pinfo[q]["nights"] & p["nights"]]
                if any(need_class(o) != "O" or o["is_minor"] or o["kind"] != "participant" for o in occ):
                    continue
                cats = {o["housing_cat"] for o in occ}
                bed_masks = defaultdict(int)
                for q in by_room[rid]:
                    if q in alloc and q in pinfo:
                        bed_masks[alloc[q]["bed"]] |= pinfo[q]["nights"]
                # evict the occupants of one bed (fewest people) that blocks p
                cand = sorted(bed_masks.items(), key=lambda kv: sum(1 for q in by_room[rid] if alloc.get(q, {}).get("bed") == kv[0] and pinfo[q]["nights"] & p["nights"]))
                if not cand:
                    continue
                bed = cand[0][0]
                victims = [q for q in by_room[rid] if alloc.get(q, {}).get("bed") == bed and pinfo[q]["nights"] & p["nights"]]
                others = [pinfo[q] for q in by_room[rid] if q not in victims and pinfo[q]["nights"] & p["nights"]]
                if any(o["housing_cat"] != p["housing_cat"] for o in others):
                    continue
                for v in victims:
                    con.execute("UPDATE alloc SET status='unplaced', site_id=NULL, room_id=NULL, bed=NULL, cost=0, explain=? WHERE pid=?",
                                (json.dumps([f"Bed given to {p['pid']} who has a hard accessibility need (verified step-free room at {sites[sid]['name']}). Re-placement attempted."]), v))
                    by_room[rid].remove(v)
                    alloc.pop(v, None)
                    evicted.append(v)
                cost = sites[sid]["cost"] * T.popcount(p["nights"])
                why = explain(p, {"need": need_class(p), "early": 0}, sites[sid], rid, bed, {}, conts, sites) + ["Placed via priority for hard accessibility needs."]
                con.execute("INSERT OR REPLACE INTO alloc(pid,site_id,room_id,bed,nights,cost,status,explain,source) VALUES(?,?,?,?,?,?,?,?,?)",
                            (p["pid"], sid, rid, bed, p["nights"], cost, "placed", json.dumps(why), "priority"))
                alloc[p["pid"]] = {"site_id": sid, "room_id": rid, "bed": bed}
                by_room[rid].append(p["pid"])
                for n in ns:
                    hard_load[sid][n] += 1
                placed_hard.append(p["pid"])
                done = True
                break
            if done:
                break
    rep = repair(con, set(evicted), log=log) if evicted else {"replaced": [], "unplaced": []}
    return {"hard_need_placed": placed_hard, "evicted": evicted, "evicted_rehoused": len(rep["replaced"])}
