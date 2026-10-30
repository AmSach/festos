"""Event & venue scheduling.

Model
  * every round of every event becomes one or more *heats* (a heat = one sitting
    of a round in one venue; prelims of 190 people in a 60-seat hall need 4 heats)
  * a heat needs: an eligible venue (requirements ⊆ venue capabilities, capacity),
    a start time inside the venue's windows, changeover from the previous heat,
    the event's judging panel free, and the previous round finished + gap
  * participants are assigned to prelim heats AFTER timing, which is the main
    lever for avoiding clashes: someone in Debate and Quiz just takes non-
    overlapping heats; a late arrival takes the Nov-1 morning heat.

Algorithm
  1. greedy set-cover construction (each new heat goes where the most
     still-uncovered registrants are on site)
  2. simulated annealing over (venue, start) with exact incremental costs:
     hard violations, per-participant best-combination cost (absence + clash,
     with semis/finals weighted by the probability of advancing),
     equipment concurrency vs usable stock, volunteer peaks
  3. capacity-constrained heat membership (teams stay together)
  4. an independent validator re-checks everything (also after manual edits).
"""
import json
import math
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import product

from . import timeutil as T
from .db import get_setting, jl

BASE = datetime(2026, 10, 30)
SLOT = 15
NSLOT = 3 * 24 * 60 // SLOT
ROUND_IDX = {"prelims": 0, "single": 0, "semis": 1, "finals": 2}
W_HARD = 10000.0


def m2s(m):
    return (BASE + timedelta(minutes=int(m))).strftime(T.FMT)


def s2m(s):
    d = T.p(s)
    return int((d - BASE).total_seconds() // 60)


# ======================================================================= problem
class Problem:
    def __init__(self, con, fixed_heats=None):
        self.con = con
        g = lambda k: get_setting(con, k)
        self.semis_frac, self.finals_frac = g("semis_advance_frac"), g("finals_advance_frac")
        self.max_heat, self.changeover = g("max_heat_size"), g("changeover_min")
        self.gap, self.travel = g("round_gap_min"), g("travel_buffer_min")
        self.vol_pool = g("volunteer_pool")
        nc = g("count_needs_check_as_usable")

        self.venues = {r["vid"]: dict(r) for r in con.execute("SELECT * FROM venues")}
        for v in self.venues.values():
            wins = [(s2m(a), s2m(b)) for a, b in jl(v["windows"], [])]
            blocks = [(s2m(a), s2m(b)) for a, b, *_ in jl(v["blocks"], [])]
            v["wins"] = _subtract(wins, blocks)
            v["caps"] = set(jl(v["caps"], []))
        self.events = {r["eid"]: dict(r) for r in con.execute("SELECT * FROM events ORDER BY eid")}
        for e in self.events.values():
            e["rounds"] = jl(e["rounds"], [])
            e["equipment"] = jl(e["equipment"], {})
            waived = set(jl(e["waived_equipment"], []))
            e["equipment"] = {k: v for k, v in e["equipment"].items() if k not in waived}
            e["elig"] = jl(e["eligible_venues"], [])

        # stock usable for scheduling (pooled; movement plan handles locations)
        states = ("available", "inspect") if nc else ("available",)
        self.stock = defaultdict(int)
        for r in con.execute(f"SELECT item, SUM(qty) q FROM logistics WHERE state IN ({','.join('?'*len(states))}) GROUP BY item", states):
            self.stock[r["item"]] = r["q"]

        # people present & active
        self.people = {}
        for r in con.execute("SELECT pid, arrival, departure, status, timing_ok FROM people WHERE kind='participant'"):
            if r["status"] in ("active",) and r["timing_ok"]:
                self.people[r["pid"]] = (s2m(r["arrival"]), s2m(r["departure"]))
        self.reg = defaultdict(list)          # eid -> [pid]
        self.pev = defaultdict(list)          # pid -> [eid]
        self.team = {}                        # (eid,pid) -> team_id
        self.excluded = defaultdict(list)     # eid -> pids not schedulable (inactive / timing)
        for r in con.execute("SELECT pid, eid, team_id FROM registrations ORDER BY pid"):
            if r["pid"] in self.people:
                self.reg[r["eid"]].append(r["pid"])
                self.pev[r["pid"]].append(r["eid"])
                if r["team_id"]:
                    self.team[(r["eid"], r["pid"])] = r["team_id"]
            else:
                self.excluded[r["eid"]].append(r["pid"])

        # judges
        self.judge_events = {}
        for r in con.execute("SELECT jid, events FROM judges"):
            self.judge_events[r["jid"]] = jl(r["events"], [])
        self.event_judges = defaultdict(set)
        for j, evs in self.judge_events.items():
            for e in evs:
                self.event_judges[e].add(j)
        self.linked = defaultdict(set)        # events sharing a judge
        for evs in self.judge_events.values():
            for a in evs:
                for b in evs:
                    if a != b:
                        self.linked[a].add(b)
        self.fixed_heats = fixed_heats
        self.cap_override = get_setting(con, "heat_cap_override", {}) or {}

    # ------------------------------------------------------------------ heats
    def build_heats(self):
        heats = []
        for eid, e in self.events.items():
            n = len(self.reg[eid])
            rounds = e["rounds"] if e["rounds"] else ["single"]
            elig_caps = sorted((self.venues[v]["capacity"] for v in e["elig"]), reverse=True)
            maxcap = min(self.max_heat, elig_caps[0]) if elig_caps else self.max_heat
            for ri, rname in enumerate(rounds):
                maxcap_r = min(maxcap, self.cap_override.get(f"{eid}|{rname}", 10 ** 6))
                if rname in ("prelims", "single") or len(rounds) == 1:
                    pool, prob, ridx = n, 1.0, 0
                elif rname == "semis":
                    pool, prob, ridx = max(4, math.ceil(n * self.semis_frac)), self.semis_frac, 1
                else:
                    pool, prob, ridx = max(4, math.ceil(n * self.finals_frac)), self.finals_frac, 2
                    if "semis" not in rounds:
                        ridx = 1
                if pool == 0:
                    continue
                H = max(1, math.ceil(pool / maxcap_r))
                size = math.ceil(pool / H)
                elig = [v for v in e["elig"] if self.venues[v]["capacity"] >= size]
                for i in range(H):
                    heats.append({
                        "hid": f"{eid}-{ {'prelims': 'P', 'semis': 'S', 'finals': 'F'}.get(rname, 'H')}{i+1}", "eid": eid, "round": rname, "ridx": ridx,
                        "idx": i, "size": size, "pool": pool, "prob": prob, "dur": e["duration"],
                        "elig": elig or list(e["elig"]), "venue": None, "start": None,
                    })
        return heats


def _subtract(wins, blocks):
    out = []
    for a, b in wins:
        segs = [(a, b)]
        for x, y in blocks:
            nxt = []
            for s, e in segs:
                if y <= s or x >= e:
                    nxt.append((s, e))
                else:
                    if x > s:
                        nxt.append((s, x))
                    if y < e:
                        nxt.append((y, e))
            segs = nxt
        out.extend(segs)
    return [s for s in out if s[1] - s[0] >= 30]


# ======================================================================= solver
class Solver:
    def __init__(self, prob: Problem, heats, seed=7, stability=None):
        self.P = prob
        self.H = heats
        self.rnd = random.Random(seed)
        self.stability = stability or {}      # hid -> (venue,start) to stay close to
        self.by_event = defaultdict(list)
        for i, h in enumerate(heats):
            self.by_event[h["eid"]].append(i)
        self.event_rounds = {}
        for eid, idxs in self.by_event.items():
            rr = defaultdict(list)
            for i in idxs:
                rr[heats[i]["ridx"]].append(i)
            self.event_rounds[eid] = rr
        self.eq_dem = defaultdict(lambda: [0] * NSLOT)
        self.vol_dem = [0] * NSLOT
        self.pcost = {}
        self.fast = False         # repair mode: ignore participant costs while relocating

    # -------------------------------------------------------------- basics
    def end(self, h):
        return h["start"] + h["dur"]

    def in_window(self, v, s, e):
        for a, b in self.P.venues[v]["wins"]:
            if a <= s and e <= b:
                return True
        return False

    def ov(self, h1, h2, buf):
        b = buf if h1["venue"] != h2["venue"] else 0
        return h1["start"] < h2["start"] + h2["dur"] + b and h2["start"] < h1["start"] + h1["dur"] + b

    # -------------------------------------------------------------- hard pairs
    def pair_hard(self, i, j):
        a, b = self.H[i], self.H[j]
        if a["start"] is None or b["start"] is None:
            return 0
        pen = 0
        if a["venue"] == b["venue"] and a["eid"] != b["eid"]:
            if a["start"] < b["start"] + b["dur"] + self.P.changeover and b["start"] < a["start"] + a["dur"] + self.P.changeover:
                pen += 1
        if a["eid"] == b["eid"]:
            if a["ridx"] == b["ridx"]:
                if a["start"] < b["start"] + b["dur"] and b["start"] < a["start"] + a["dur"]:
                    pen += 1
            else:
                first, second = (a, b) if a["ridx"] < b["ridx"] else (b, a)
                if second["start"] < first["start"] + first["dur"] + self.P.gap:
                    pen += 1
        elif b["eid"] in self.P.linked[a["eid"]]:
            if self.ov(a, b, self.P.travel):
                pen += 1
        return pen

    def neighbors(self, i):
        h = self.H[i]
        out = set(self.by_event[h["eid"]])
        for j, g in enumerate(self.H):
            if g["venue"] == h["venue"] or g["eid"] in self.P.linked[h["eid"]]:
                out.add(j)
        out.discard(i)
        return out

    def unary(self, i):
        h = self.H[i]
        if h["start"] is None:
            return W_HARD * 5
        pen = 0.0
        if not self.in_window(h["venue"], h["start"], self.end(h)):
            pen += W_HARD
        if h["venue"] not in h["elig"]:
            pen += W_HARD
        # preferences
        e = h["start"] + h["dur"]
        if h["eid"] == "E31" and h["ridx"] == 2:
            target = s2m("2026-11-01 17:30")
            pen += abs(h["start"] - target) / 15 * 3
        if (e % 1440) > 21 * 60 + 30:
            pen += 4
        if h["ridx"] == 2:
            pen += max(0, s2m("2026-11-01 08:00") - h["start"]) / 60 * 0.6   # finals late in the fest
        st = self.stability.get(h["hid"])
        if st:
            if st[0] != h["venue"]:
                pen += 60
            pen += abs(st[1] - h["start"]) / 15 * 6
        return pen

    # -------------------------------------------------------------- resources
    def _res_span(self, h):
        s = max(0, (h["start"] - 15) // SLOT)
        e = min(NSLOT, (h["start"] + h["dur"] + 15 + SLOT - 1) // SLOT)
        return s, e

    def res_apply(self, i, sign):
        h = self.H[i]
        if h["start"] is None:
            return 0.0
        e = self.P.events[h["eid"]]
        s0, s1 = self._res_span(h)
        before = 0.0
        after = 0.0
        for item, q in e["equipment"].items():
            stock = self.P.stock.get(item, 0)
            if stock < q:
                continue              # unfixable by scheduling: reported separately
            arr = self.eq_dem[item]
            for t in range(s0, s1):
                before += max(0, arr[t] - stock)
                arr[t] += sign * q
                after += max(0, arr[t] - stock)
        vs0 = h["start"] // SLOT
        vs1 = min(NSLOT, (h["start"] + h["dur"] + SLOT - 1) // SLOT)
        for t in range(vs0, vs1):
            before += max(0, self.vol_dem[t] - self.P.vol_pool) * 0.2
            self.vol_dem[t] += sign * e["volunteers"]
            after += max(0, self.vol_dem[t] - self.P.vol_pool) * 0.2
        return (after - before) * 3.0

    # -------------------------------------------------------------- participants
    def present(self, pid, h):
        a, d = self.P.people[pid]
        return a <= h["start"] and d >= h["start"] + h["dur"]

    def participant_cost(self, pid):
        evs = self.P.pev[pid]
        opts, later = [], []
        fixed = 0.0
        for e in evs:
            rr = self.event_rounds[e]
            o = [self.H[i] for i in rr.get(0, []) if self.H[i]["start"] is not None]
            opts.append(o)
            lat = []
            for r in (1, 2):
                for i in rr.get(r, []):
                    h = self.H[i]
                    if h["start"] is not None:
                        lat.append(h)
                        if not self.present(pid, h):
                            fixed += 0.5 * h["prob"]
            later.append(lat)
        for x in range(len(evs)):
            for y in range(x + 1, len(evs)):
                for l in later[x]:
                    for m in later[y]:
                        if self.ov(l, m, self.P.travel):
                            fixed += l["prob"] * m["prob"]
        # order options: present first
        best = 1e9
        po = []
        for o in opts:
            pres = [h for h in o if self.present(pid, h)]
            po.append(pres if pres else (o[:1] if o else [None]))
        absent_pen = sum(1.0 for o, p in zip(opts, po) if o and not any(self.present(pid, h) for h in o))
        for combo in product(*po):
            c = absent_pen
            for x in range(len(combo)):
                hx = combo[x]
                if hx is None:
                    continue
                for y in range(x + 1, len(combo)):
                    hy = combo[y]
                    if hy is not None and self.ov(hx, hy, self.P.travel):
                        c += 1.0
                for y in range(len(combo)):
                    if y != x:
                        for l in later[y]:
                            if self.ov(hx, l, self.P.travel):
                                c += l["prob"]
                if c >= best:
                    break
            if c < best:
                best = c
                if best <= absent_pen:
                    break
        return fixed + best

    def event_pcost(self, eids):
        tot = 0.0
        pids = set()
        for e in eids:
            pids.update(self.P.reg[e])
        for pid in pids:
            c = self.participant_cost(pid)
            tot += c - self.pcost.get(pid, 0.0)
            self.pcost[pid] = c
        return tot

    # -------------------------------------------------------------- construction
    def construct(self):
        """Backward for later rounds (finals land late, semis before them), then
        forward set-cover for prelims inside the remaining window."""
        P, H = self.P, self.H
        order = sorted(P.events, key=lambda e: (len(P.events[e]["elig"]), -len(P.reg[e])))
        placed = []
        e31_target = s2m("2026-11-01 17:30")
        semis_cut = s2m("2026-11-01 14:00")
        for ridx in (2, 1, 0):
            for eid in order:
                rr = self.event_rounds[eid]
                idxs = rr.get(ridx, [])
                if not idxs:
                    continue
                pids = P.reg[eid]
                # deadline: next round of this event must still fit after us
                nxt = [H[i]["start"] for r in range(ridx + 1, 3) for i in rr.get(r, []) if H[i]["start"] is not None]
                deadline = (min(nxt) - P.gap) if nxt else 10 ** 9
                covered = set()
                for i in idxs:
                    h = H[i]
                    if h["hid"] in (self.stability or {}):
                        v, s = self.stability[h["hid"]]
                        h["venue"], h["start"] = v, s
                        if s + h["dur"] <= deadline and self._feasible_with(i, placed):
                            placed.append(i)
                            covered.update(p for p in pids if self.present(p, h))
                            continue
                    covs = None
                    if ridx == 0:
                        cov = [0] * (NSLOT + 1)
                        for p in pids:
                            if p in covered:
                                continue
                            a, d = P.people[p]
                            lo = (a + SLOT - 1) // SLOT
                            hi = (d - h["dur"]) // SLOT
                            if hi >= lo:
                                cov[max(0, lo)] += 1
                                cov[min(NSLOT, hi + 1)] -= 1
                        run, covs = 0, []
                        for t in range(NSLOT):
                            run += cov[t]
                            covs.append(run)
                    cands = []
                    for v in h["elig"]:
                        for a, b in P.venues[v]["wins"]:
                            t = ((a + SLOT - 1) // SLOT) * SLOT
                            while t + h["dur"] <= b:
                                late = 0 if t + h["dur"] <= deadline else 1
                                if ridx == 0:
                                    key = -covs[t // SLOT] * 10 + t / 1440.0
                                elif eid == "E31" and ridx == 2:
                                    key = abs(t - e31_target)
                                elif ridx == 1 and P.events[eid]["rounds"][-1] == "finals" and len(rr.get(2, [])):
                                    # semis: late, but leave Nov-1 morning free for late-arrival prelim heats
                                    key = (0 if t + h["dur"] <= semis_cut else 5000) - t / 1440.0
                                else:
                                    key = -t          # as late as possible
                                cands.append((late, key, -P.venues[v]["capacity"], t, v))
                                t += SLOT
                    cands.sort()
                    found = False
                    for late, _, _, t, v in cands:
                        h["venue"], h["start"] = v, t
                        if self._feasible_with(i, placed):
                            found = True
                            break
                    if not found:
                        if cands:
                            _, _, _, t, v = cands[0]
                            h["venue"], h["start"] = v, t
                        else:
                            h["venue"], h["start"] = (h["elig"][0] if h["elig"] else "V01"), 8 * 60
                    placed.append(i)
                    if ridx == 0:
                        covered.update(p for p in pids if self.present(p, h))

    def _feasible_with(self, i, placed):
        h = self.H[i]
        if not self.in_window(h["venue"], h["start"], self.end(h)):
            return False
        for j in placed:
            if self.pair_hard(i, j):
                return False
        return True

    def relocate(self, i):
        h = self.H[i]
        old = (h["venue"], h["start"])
        cands = []
        for v in h["elig"]:
            for a, b in self.P.venues[v]["wins"]:
                t = ((a + SLOT - 1) // SLOT) * SLOT
                while t + h["dur"] <= b:
                    cands.append((v, t))
                    t += SLOT
        self.rnd.shuffle(cands)
        found = None
        nbr_all = list(range(len(self.H)))
        for v, t in cands[:400]:
            h["venue"], h["start"] = v, t
            if all(self.pair_hard(i, j) == 0 for j in nbr_all if j != i):
                found = (v, t)
                break
        h["venue"], h["start"] = old
        return found

    # -------------------------------------------------------------- full cost
    def full_cost(self):
        hard = 0
        n = len(self.H)
        for i in range(n):
            for j in range(i + 1, n):
                hard += self.pair_hard(i, j)
        un = sum(self.unary(i) for i in range(n))
        self.eq_dem = defaultdict(lambda: [0] * NSLOT)
        self.vol_dem = [0] * NSLOT
        res = 0.0
        for i in range(n):
            res += self.res_apply(i, +1)
        self.pcost = {}
        pc = 0.0 if self.fast else self.event_pcost(list(self.P.events))
        return hard * W_HARD + un + res + pc

    # -------------------------------------------------------------- annealing
    def _local_hard(self, S):
        tot, seen = 0, set()
        for i in S:
            for j in self.neighbors(i) | (S - {i}):
                k = (i, j) if i < j else (j, i)
                if k in seen:
                    continue
                seen.add(k)
                tot += self.pair_hard(i, j)
        return tot

    def apply_moves(self, changes):
        """changes: [(i, venue, start)]. Returns (delta, undo)."""
        H = self.H
        S = {i for i, _, _ in changes}
        old = {i: (H[i]["venue"], H[i]["start"]) for i in S}
        d = -self._local_hard(S) * W_HARD - sum(self.unary(i) for i in S)
        for i in S:
            d += self.res_apply(i, -1)
        for i, v, s in changes:
            H[i]["venue"], H[i]["start"] = v, s
        self._last_hard = self._local_hard(S) + sum(1 for i in S if self.unary(i) >= W_HARD)
        d += self._local_hard(S) * W_HARD + sum(self.unary(i) for i in S)
        for i in S:
            d += self.res_apply(i, +1)
        evs = {H[i]["eid"] for i in S}
        saved = {}
        if not self.fast:
            for e in evs:
                for p in self.P.reg[e]:
                    saved[p] = self.pcost.get(p, 0.0)
            d += self.event_pcost(list(evs))

        def undo():
            for i in S:
                self.res_apply(i, -1)
            for i, (v, s) in old.items():
                H[i]["venue"], H[i]["start"] = v, s
            for i in S:
                self.res_apply(i, +1)
            self.pcost.update(saved)
        return d, undo

    def _cascade(self, i, nv, ns):
        """Move heat i; push later rounds of the same event so order still holds."""
        H = self.H
        h = H[i]
        changes = [(i, nv, ns)]
        rr = self.event_rounds[h["eid"]]
        prev_end = ns + h["dur"]
        for j in rr.get(h["ridx"], []):
            if j != i and H[j]["start"] is not None:
                prev_end = max(prev_end, H[j]["start"] + H[j]["dur"])
        for r in range(h["ridx"] + 1, 3):
            nxt = 0
            for j in rr.get(r, []):
                g = H[j]
                need = ((prev_end + self.P.gap + SLOT - 1) // SLOT) * SLOT
                if g["start"] < need:
                    changes.append((j, g["venue"], need))
                    nxt = max(nxt, need + g["dur"])
                else:
                    nxt = max(nxt, g["start"] + g["dur"])
            if nxt:
                prev_end = nxt
        return changes

    def anneal(self, seconds):
        P, H = self.P, self.H
        cost = self.full_cost()
        best_cost, best = cost, [(h["venue"], h["start"]) for h in H]
        t0 = time.time()
        it = acc = 0
        movable = [i for i, h in enumerate(H) if not h.get("locked")]
        if not movable:
            self.iterations = 0
            return cost, 0
        temp0 = 3.0
        bad = []
        while time.time() - t0 < seconds:
            it += 1
            frac = (time.time() - t0) / seconds
            temp = temp0 * (1 - frac) + 0.05
            if it % 100 == 1:
                bad = [k for k in movable if self.unary(k) >= W_HARD or any(self.pair_hard(k, j) for j in self.neighbors(k))]
            mv = self.rnd.random()
            if bad and mv < 0.5:
                i = self.rnd.choice(bad)
                spot = self.relocate(i)
                if spot is None:
                    continue
                changes = [(i, spot[0], spot[1])]
            else:
                i = self.rnd.choice(movable)
                h = H[i]
                r = self.rnd.random()
                if r < 0.45:
                    nv, ns = h["venue"], h["start"] + self.rnd.choice([-1, 1]) * self.rnd.randint(1, 8) * SLOT
                elif r < 0.6:
                    nv, ns = (self.rnd.choice(h["elig"]) if h["elig"] else h["venue"]), h["start"]
                else:
                    nv = self.rnd.choice(h["elig"]) if h["elig"] else h["venue"]
                    wins = P.venues[nv]["wins"]
                    if not wins:
                        continue
                    a, b = self.rnd.choice(wins)
                    if b - a < h["dur"]:
                        continue
                    ns = a + self.rnd.randint(0, (b - a - h["dur"]) // SLOT) * SLOT
                if (nv, ns) == (h["venue"], h["start"]):
                    continue
                changes = self._cascade(i, nv, ns) if r >= 0.6 or self.rnd.random() < 0.3 else [(i, nv, ns)]
            delta, undo = self.apply_moves(changes)
            if delta <= 0 or self.rnd.random() < math.exp(-delta / temp):
                cost += delta
                acc += 1
                if cost < best_cost - 1e-9:
                    best_cost, best = cost, [(g["venue"], g["start"]) for g in H]
            else:
                undo()
        for g, (v, s) in zip(H, best):
            g["venue"], g["start"] = v, s
        self.iterations, self.accepted = it, acc
        best_cost = self.full_cost()
        best_cost += self.polish()
        return best_cost, it

    def polish(self, rounds=4):
        """Deterministic clean-up: any heat still in a hard violation is moved to the
        best violation-free slot (cascading later rounds)."""
        H = self.H
        total = 0.0
        for _ in range(rounds):
            bad = [k for k in range(len(H)) if not H[k].get("locked") and
                   (self.unary(k) >= W_HARD or any(self.pair_hard(k, j) for j in self.neighbors(k)))]
            if not bad:
                break
            for i in bad:
                h = H[i]
                best = None
                for v in h["elig"]:
                    for a, b in self.P.venues[v]["wins"]:
                        t = ((a + SLOT - 1) // SLOT) * SLOT
                        while t + h["dur"] <= b:
                            d, undo = self.apply_moves(self._cascade(i, v, t))
                            clean = self._last_hard == 0
                            undo()
                            if clean and (best is None or d < best[0]):
                                best = (d, v, t)
                            t += SLOT * 2
                if best and best[0] < 0:
                    d, _ = self.apply_moves(self._cascade(i, best[1], best[2]))
                    total += d
        return total


# ======================================================================= membership
def assign_members(solver: Solver, sticky=None):
    """Capacity-constrained prelim/single-round heat membership. Teams stay together.
    sticky: {(pid,eid): hid} — keep current heat if still valid (minimal change)."""
    P, H = solver.P, solver.H
    sticky = sticky or {}
    cap = {}
    for h in H:
        if h["ridx"] == 0:
            cap[h["hid"]] = min(P.venues[h["venue"]]["capacity"], max(h["size"], math.ceil(h["size"] * 1.25)))
    load = defaultdict(int)
    team_heat = {}
    assign = {}
    order = sorted(P.pev, key=lambda p: (-len(P.pev[p]), P.people[p][1] - P.people[p][0]))
    for pid in order:
        evs = P.pev[pid]
        opts = []
        later = []
        for e in evs:
            rr = solver.event_rounds[e]
            o = [H[i] for i in rr.get(0, [])]
            tid = P.team.get((e, pid))
            if tid and (e, tid) in team_heat:
                o = [h for h in o if h["hid"] == team_heat[(e, tid)]]
            o = [h for h in o if load[h["hid"]] < cap[h["hid"]]]      # seats are a hard limit
            opts.append(o)
            later.append([H[i] for r in (1, 2) for i in rr.get(r, [])])
        best, bc = None, 1e9
        for combo in product(*[o if o else [None] for o in opts]):
            c = 0.0
            for x, hx in enumerate(combo):
                if hx is None:
                    continue
                if load[hx["hid"]] >= cap[hx["hid"]]:
                    c += 50
                if not solver.present(pid, hx):
                    c += 10
                if sticky.get((pid, evs[x])) and sticky[(pid, evs[x])] != hx["hid"]:
                    c += 0.3
                for y in range(x + 1, len(combo)):
                    hy = combo[y]
                    if hy is not None and solver.ov(hx, hy, P.travel):
                        c += 10
                for y in range(len(combo)):
                    if y != x:
                        for l in later[y]:
                            if solver.ov(hx, l, P.travel):
                                c += 10 * l["prob"]
                c += load[hx["hid"]] / max(1, cap[hx["hid"]]) * 0.05
            if c < bc:
                bc, best = c, combo
        for x, hx in enumerate(best):
            if hx is None:
                continue
            e = evs[x]
            assign[(pid, e)] = hx["hid"]
            load[hx["hid"]] += 1
            tid = P.team.get((e, pid))
            if tid:
                team_heat.setdefault((e, tid), hx["hid"])
    return assign, load


# ======================================================================= entry points
def solve(con, seconds=None, stability=None, seed=7, log=print, pinned=None):
    t0 = time.time()
    P = Problem(con)
    heats = P.build_heats()
    S = Solver(P, heats, seed=seed, stability=stability)
    secs = seconds if seconds is not None else get_setting(con, "schedule_search_seconds")
    if stability:
        c0, c1, it = _repair_solve(S, stability, secs, pinned or set())
    else:
        S.construct()
        c0 = S.full_cost()
        c1, it = S.anneal(secs)
    sticky = None
    if stability:
        sticky = {(r["pid"], r["eid"]): r["heat_id"] for r in con.execute("SELECT pid,eid,heat_id FROM registrations WHERE heat_id IS NOT NULL")}
    assign, load = assign_members(S, sticky)
    log(f"  schedule: {len(heats)} heats, cost {c0:.0f} → {c1:.0f} in {it} SA moves ({time.time()-t0:.1f}s)")
    return P, S, assign, load, {"heats": len(heats), "cost_construct": round(c0, 1), "cost_final": round(c1, 1),
                                "iterations": it, "seconds": round(time.time() - t0, 1)}


def _place_new(S, i):
    """Put a newly created heat into the first violation-free slot, preferring
    times when most of its event's registrants are on site."""
    H, P = S.H, S.P
    h = H[i]
    placed = [j for j, g in enumerate(H) if g["start"] is not None and j != i]
    cands = []
    for v in h["elig"]:
        for a, b in P.venues[v]["wins"]:
            t = ((a + SLOT - 1) // SLOT) * SLOT
            while t + h["dur"] <= b:
                h["venue"], h["start"] = v, t
                pres = sum(1 for p in P.reg[h["eid"]] if S.present(p, h))
                cands.append((-pres, t, v))
                t += SLOT
    cands.sort()
    for _, t, v in cands:
        h["venue"], h["start"] = v, t
        if all(S.pair_hard(i, j) == 0 for j in placed):
            return True
    if cands:
        h["venue"], h["start"] = cands[0][2], cands[0][1]
    return False


def _repair_solve(S, stability, secs, pinned):
    """Minimal change: every heat starts where it is. Only heats that are now
    infeasible (and the other rounds of their event) may move; everything else is
    locked. A move is only accepted if it leaves the moved heats violation-free.
    Participant seating is re-optimised afterwards by the membership step."""
    H = S.H
    S.fast = True
    for h in H:
        st = stability.get(h["hid"])
        if st:
            h["venue"], h["start"] = st
    missing = [i for i, h in enumerate(H) if h["start"] is None]
    for i in sorted(missing, key=lambda k: (H[k]["ridx"], H[k]["idx"])):   # new / re-split heats only
        _place_new(S, i)
    c0 = S.full_cost()
    bad = {i for i in range(len(H)) if S.unary(i) >= W_HARD or any(S.pair_hard(i, j) for j in S.neighbors(i))}
    bad |= set(missing)
    events = {H[i]["eid"] for i in bad}
    for h in H:
        h["locked"] = (h["eid"] not in events) or (h["hid"] in pinned)
    S.polish(rounds=4)
    for h in H:
        h["locked"] = h["hid"] in pinned
    it = 0
    still = [i for i in range(len(H)) if S.unary(i) >= W_HARD or any(S.pair_hard(i, j) for j in S.neighbors(i))]
    if still:
        # chain moves: anything unpinned may shift, but every shift is priced (stability)
        _, it = S.anneal(min(4, secs))
        S.polish(rounds=3)
    c1 = S.full_cost()
    S.fast = False
    return c0, c1, it


def save(con, P, S, assign, load, meta):
    con.execute("DELETE FROM heats")
    for h in S.H:
        con.execute("INSERT INTO heats(hid,eid,round,idx,vid,start,end,capacity,pool,locked,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (h["hid"], h["eid"], h["round"], h["idx"], h["venue"], m2s(h["start"]), m2s(h["start"] + h["dur"]),
                     P.venues[h["venue"]]["capacity"], h["size"] if h["ridx"] == 0 else h["pool"], 1 if h.get("locked") else 0,
                     "planned"))
    con.execute("UPDATE registrations SET heat_id=NULL")
    for (pid, eid), hid in assign.items():
        con.execute("UPDATE registrations SET heat_id=? WHERE pid=? AND eid=?", (hid, pid, eid))
    from .db import set_setting
    set_setting(con, "schedule_meta", meta)


def current_stability(con):
    return {r["hid"]: (r["vid"], s2m(r["start"])) for r in con.execute("SELECT hid, vid, start FROM heats")}


def build(con, seconds=None, log=print):
    P, S, assign, load, meta = solve(con, seconds, log=log)
    save(con, P, S, assign, load, meta)
    return meta


def repair(con, seconds=6, log=print):
    """Minimal-change reschedule: every heat is pulled toward its current slot,
    so only heats that are now infeasible (closed venue, failed equipment…) move.
    If a heat only fits a venue that is gone, its round is re-split into smaller
    heats that fit the next-largest eligible venue."""
    stab = current_stability(con)
    pinned = {r[0] for r in con.execute("SELECT hid FROM heats WHERE locked=1")}
    P, S, assign, load, meta = solve(con, seconds, stability=stab, seed=11, log=log, pinned=pinned)
    resplit = []
    stuck = [i for i, h in enumerate(S.H) if S.unary(i) >= W_HARD or any(S.pair_hard(i, j) for j in S.neighbors(i))]
    if stuck:
        from .db import set_setting
        ov = dict(get_setting(con, "heat_cap_override", {}) or {})
        for i in stuck:
            h = S.H[i]
            caps = sorted({P.venues[v]["capacity"] for v in P.events[h["eid"]]["elig"] if P.venues[v]["capacity"] < h["size"]}, reverse=True)
            if caps:
                key = f"{h['eid']}|{h['round']}"
                if ov.get(key, 10 ** 6) > caps[0]:
                    ov[key] = caps[0]
                    resplit.append({"event": P.events[h["eid"]]["name"], "round": h["round"], "new_heat_size": caps[0]})
        if resplit:
            set_setting(con, "heat_cap_override", ov)
            P, S, assign, load, meta = solve(con, seconds, stability=stab, seed=13, log=log, pinned=pinned)
    moved = []
    for h in S.H:
        old = stab.get(h["hid"])
        if old and (old[0] != h["venue"] or old[1] != h["start"]):
            moved.append({"hid": h["hid"], "event": P.events[h["eid"]]["name"], "from_venue": old[0],
                          "from": m2s(old[1]), "to_venue": h["venue"], "to": m2s(h["start"])})
    prev = {(r["pid"], r["eid"]): r["heat_id"] for r in con.execute("SELECT pid,eid,heat_id FROM registrations")}
    save(con, P, S, assign, load, meta)
    changed_people = sorted({pid for (pid, eid), hid in assign.items() if prev.get((pid, eid)) not in (None, hid)})
    return {"moved_heats": moved, "participants_reassigned": changed_people, "meta": meta, "resplit": resplit}


# ======================================================================= validator
def validate(con):
    """Independent re-check of the stored schedule. Returns list of conflicts."""
    P = Problem(con)
    heats = [dict(r) for r in con.execute("SELECT * FROM heats")]
    hmap = {h["hid"]: h for h in heats}
    for h in heats:
        h["s"], h["e"] = s2m(h["start"]), s2m(h["end"])
    out = []

    def add(kind, sev, msg, refs, count=1):
        out.append({"kind": kind, "severity": sev, "message": msg, "refs": refs, "count": count})

    ev = P.events
    # venue windows / capacity / requirements
    by_v = defaultdict(list)
    for h in heats:
        v = P.venues[h["vid"]]
        by_v[h["vid"]].append(h)
        if not any(a <= h["s"] and h["e"] <= b for a, b in v["wins"]):
            add("venue_window", "critical", f"{ev[h['eid']]['name']} {h['round']} heat {h['hid']} at {v['name']} {h['start'][5:]} is outside the venue's open window or a closure.", [h["hid"]])
        reqs = set(jl(ev[h["eid"]]["reqs"], []))
        if not reqs <= v["caps"]:
            add("venue_requirement", "high", f"{v['name']} lacks {sorted(reqs - v['caps'])} needed by {ev[h['eid']]['name']}.", [h["hid"]])
    members = defaultdict(list)
    for r in con.execute("SELECT pid, eid, heat_id FROM registrations WHERE heat_id IS NOT NULL"):
        members[r["heat_id"]].append(r["pid"])
    for hid, ms in members.items():
        h = hmap.get(hid)
        if h and len(ms) > P.venues[h["vid"]]["capacity"]:
            add("venue_capacity", "high", f"{hid} has {len(ms)} competitors in a {P.venues[h['vid']]['capacity']}-seat venue.", [hid])
    co = P.changeover
    for v, hs in by_v.items():
        hs.sort(key=lambda x: x["s"])
        for a, b in zip(hs, hs[1:]):
            gap = 0 if a["eid"] == b["eid"] else co
            if b["s"] < a["e"] + gap:
                add("venue_double_booked", "critical",
                    f"{P.venues[v]['name']}: {ev[a['eid']]['name']} ({a['start'][5:]}–{a['end'][11:]}) and {ev[b['eid']]['name']} ({b['start'][5:]}) overlap or lack {co}-min changeover.",
                    [a["hid"], b["hid"]])
    # event panel + precedence
    by_e = defaultdict(list)
    for h in heats:
        by_e[h["eid"]].append(h)
    for e, hs in by_e.items():
        for i in range(len(hs)):
            for j in range(i + 1, len(hs)):
                a, b = hs[i], hs[j]
                ra, rb = ROUND_IDX.get(a["round"], 0), ROUND_IDX.get(b["round"], 0)
                if ra == rb and a["s"] < b["e"] and b["s"] < a["e"]:
                    add("panel_overlap", "high", f"{ev[e]['name']}: heats {a['hid']} and {b['hid']} overlap but share one judging panel.", [a["hid"], b["hid"]])
                elif ra != rb:
                    f, s = (a, b) if ra < rb else (b, a)
                    if s["s"] < f["e"] + P.gap:
                        add("round_order", "critical", f"{ev[e]['name']}: {s['round']} ({s['start'][5:]}) starts before {f['round']} heat {f['hid']} is done + {P.gap} min.", [f["hid"], s["hid"]])
    # judges
    for j, evs in P.judge_events.items():
        hs = sorted([h for h in heats if h["eid"] in evs], key=lambda x: x["s"])
        for a, b in zip(hs, hs[1:]):
            if a["eid"] != b["eid"]:
                buf = P.travel if a["vid"] != b["vid"] else 0
                if b["s"] < a["e"] + buf:
                    add("judge_clash", "high", f"Judge {j} is on both {ev[a['eid']]['name']} ({a['start'][5:]}) and {ev[b['eid']]['name']} ({b['start'][5:]}).", [a["hid"], b["hid"], j])
    # participants
    pheats = defaultdict(list)
    for r in con.execute("SELECT pid, eid, heat_id FROM registrations"):
        if r["heat_id"]:
            pheats[r["pid"]].append(hmap[r["heat_id"]])
    clash_people, absent_people = set(), set()
    for pid, hs in pheats.items():
        if pid not in P.people:
            continue
        a0, d0 = P.people[pid]
        for h in hs:
            if not (a0 <= h["s"] and d0 >= h["e"]):
                absent_people.add(pid)
        hs = sorted(hs, key=lambda x: x["s"])
        for i in range(len(hs)):
            for k in range(i + 1, len(hs)):
                x, y = hs[i], hs[k]
                buf = P.travel if x["vid"] != y["vid"] else 0
                if y["s"] < x["e"] + buf and x["s"] < y["e"] + buf:
                    clash_people.add(pid)
    if clash_people:
        add("participant_clash", "high", f"{len(clash_people)} participants have overlapping heats (incl. {P.travel}-min walking buffer).", sorted(clash_people)[:200], len(clash_people))
    if absent_people:
        add("participant_absent", "medium", f"{len(absent_people)} participants are assigned to a heat outside their arrival–departure window (no heat of that event fits their stay).", sorted(absent_people)[:200], len(absent_people))
    unassigned = [r["pid"] for r in con.execute("SELECT r.pid FROM registrations r JOIN people p ON p.pid=r.pid WHERE r.heat_id IS NULL AND p.status='active' AND p.timing_ok=1")]
    if unassigned:
        add("participant_unassigned", "medium", f"{len(unassigned)} active registrations have no heat.", unassigned[:200], len(unassigned))
    excluded = [r["pid"] for r in con.execute("SELECT DISTINCT r.pid FROM registrations r JOIN people p ON p.pid=r.pid WHERE p.status!='active' OR p.timing_ok=0")]
    if excluded:
        add("participant_unconfirmed", "info", f"{len(excluded)} registrants are held (duplicate review / timing anomaly / cancelled) and not seated.", excluded, len(excluded))
    # teams split
    tm = defaultdict(set)
    for r in con.execute("SELECT eid, team_id, heat_id FROM registrations WHERE team_id IS NOT NULL AND heat_id IS NOT NULL"):
        tm[(r["eid"], r["team_id"])].add(r["heat_id"])
    split = [k for k, v in tm.items() if len(v) > 1]
    if split:
        add("team_split", "high", f"{len(split)} teams have members in different heats.", [f"{a}/{b}" for a, b in split][:50], len(split))
    # equipment
    eqd = defaultdict(lambda: [0] * NSLOT)
    for h in heats:
        for item, q in ev[h["eid"]]["equipment"].items():
            s0 = max(0, (h["s"] - 15) // SLOT)
            s1 = min(NSLOT, (h["e"] + 15 + SLOT - 1) // SLOT)
            for t in range(s0, s1):
                eqd[item][t] += q
    for item, arr in eqd.items():
        stock = P.stock.get(item, 0)
        peak = max(arr)
        if peak > stock:
            slots = [t for t in range(NSLOT) if arr[t] > stock]
            evs = sorted({ev[h["eid"]]["name"] for h in heats if item in ev[h["eid"]]["equipment"]})
            add("equipment_shortage", "critical" if stock == 0 else "high",
                f"{item}: peak need {peak} vs {stock} usable" + (" — every unit is under repair or unchecked" if stock == 0 else f" during {len(slots)*SLOT//60}h") + f". Affects: {', '.join(evs[:6])}.",
                evs, peak - stock)
    # volunteers
    vd = [0] * NSLOT
    for h in heats:
        for t in range(h["s"] // SLOT, min(NSLOT, (h["e"] + SLOT - 1) // SLOT)):
            vd[t] += ev[h["eid"]]["volunteers"]
    peak = max(vd)
    if peak > P.vol_pool:
        hrs = len([t for t in range(NSLOT) if vd[t] > P.vol_pool]) * SLOT / 60
        add("volunteer_shortfall", "medium", f"Volunteer demand peaks at {peak} vs an assumed pool of {P.vol_pool} ({hrs:.1f}h over). Only 6 rostered 'Volunteer' staff exist in core_team.csv.", [], peak - P.vol_pool)
    return out


def volunteer_curve(con):
    P = Problem(con)
    vd = [0] * NSLOT
    for h in con.execute("SELECT eid,start,end FROM heats"):
        s, e = s2m(h["start"]), s2m(h["end"])
        for t in range(s // SLOT, min(NSLOT, (e + SLOT - 1) // SLOT)):
            vd[t] += P.events[h["eid"]]["volunteers"]
    return vd, P.vol_pool


def equipment_curve(con):
    ev = {r["eid"]: jl(r["equipment"], {}) for r in con.execute("SELECT eid, equipment, waived_equipment FROM events")}
    for r in con.execute("SELECT eid, waived_equipment FROM events"):
        for w in jl(r["waived_equipment"], []):
            ev[r["eid"]].pop(w, None)
    eqd = defaultdict(lambda: [0] * NSLOT)
    for h in con.execute("SELECT eid,start,end FROM heats"):
        s, e = s2m(h["start"]), s2m(h["end"])
        for item, q in ev[h["eid"]].items():
            for t in range(max(0, (s - 15) // SLOT), min(NSLOT, (e + 15 + SLOT - 1) // SLOT)):
                eqd[item][t] += q
    return eqd
