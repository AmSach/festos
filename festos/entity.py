"""Entity resolution (duplicate detection) — Fellegi–Sunter style.

Why not "same name = duplicate"?  In this pack each contingent re-uses about ten
names (Raghav Joshi appears 12 times in IIM Shillong).  Name agreement is
therefore weak evidence.  We *learn* how often two different people agree on each
field (u-probability) from the data itself, and weigh agreement by log2(m/u).
A match needs agreement on many independent attributes, which is what the two
planted duplicates show and what the 2,800 namesakes do not.
"""
import json
import math
from collections import defaultdict
from itertools import combinations

from . import textmatch

FIELDS = ["gender_raw", "age_raw", "arrival_orig", "departure", "diet_raw", "access_raw", "accom_req", "tshirt"]
M_PROB = {"name": 0.95, "gender_raw": 0.92, "age_raw": 0.95, "arrival_orig": 0.9, "departure": 0.9,
          "diet_raw": 0.9, "access_raw": 0.95, "accom_req": 0.95, "tshirt": 0.9, "contact": 0.9}
REVIEW_THRESHOLD = 14.0   # bits of evidence
AUTOHOLD_THRESHOLD = 18.0


def _name_agree(a, b):
    if textmatch.norm(a) == textmatch.norm(b):
        return 1.0
    jw = textmatch.jaro_winkler(a, b)
    if textmatch.phonetic(a) == textmatch.phonetic(b):
        return max(jw, 0.93)
    return jw


def learn_u(people):
    """Probability that two *random* people in the same contingent agree on each field."""
    by_c = defaultdict(list)
    for p in people:
        by_c[p["cid"]].append(p)
    agree = defaultdict(int)
    total = 0
    for grp in by_c.values():
        n = len(grp)
        for f in FIELDS + ["name"]:
            vals = defaultdict(int)
            for p in grp:
                vals[p[f] if f != "name" else textmatch.norm(p["name"])] += 1
            agree[f] += sum(v * (v - 1) // 2 for v in vals.values())
        total += n * (n - 1) // 2
    u = {f: max(agree[f] / total, 1e-4) for f in FIELDS + ["name"]}
    u["contact"] = 1e-4
    return u


def score_pair(a, b, u):
    ev = []
    total = 0.0
    na = _name_agree(a["name"], b["name"])
    if na >= 0.9:
        w = math.log2(M_PROB["name"] / u["name"]) * (1 if na == 1.0 else 0.85)
        ev.append({"field": "name", "a": a["name"], "b": b["name"], "agree": True, "similarity": round(na, 3), "weight": round(w, 2)})
    else:
        w = math.log2((1 - M_PROB["name"]) / (1 - u["name"]))
        ev.append({"field": "name", "a": a["name"], "b": b["name"], "agree": False, "similarity": round(na, 3), "weight": round(w, 2)})
    total += w
    for f in FIELDS:
        va, vb = a[f], b[f]
        if va in (None, "") or vb in (None, ""):
            ev.append({"field": f, "a": va, "b": vb, "agree": None, "weight": 0.0, "note": "blank — no evidence either way"})
            continue
        if va == vb:
            w = math.log2(M_PROB[f] / u[f])
        else:
            w = math.log2((1 - M_PROB[f]) / (1 - u[f]))
        ev.append({"field": f, "a": va, "b": vb, "agree": va == vb, "weight": round(w, 2)})
        total += w
    # contact: ignore placeholders (they are not evidence)
    ca, cb = a.get("contact"), b.get("contact")
    if a.get("contact_ok") and b.get("contact_ok") and ca and cb:
        w = math.log2(M_PROB["contact"] / u["contact"]) if ca == cb else math.log2((1 - M_PROB["contact"]) / (1 - u["contact"]))
        ev.append({"field": "contact", "a": ca, "b": cb, "agree": ca == cb, "weight": round(w, 2)})
        total += w
    else:
        ev.append({"field": "contact", "a": a.get("contact_raw"), "b": b.get("contact_raw"), "agree": None, "weight": 0.0,
                   "note": "one side is a placeholder/invalid number — ignored"})
    return round(total, 2), ev


def find_duplicates(con, ts, only_pids=None):
    people = [dict(r) for r in con.execute(
        "SELECT * FROM people WHERE kind='participant' AND status NOT IN ('merged','cancelled')")]
    u = learn_u(people)
    by_c = defaultdict(list)
    for p in people:
        by_c[p["cid"]].append(p)
    existing = {(r[0], r[1]) for r in con.execute("SELECT a,b FROM dup_candidates")}
    found = []
    for grp in by_c.values():
        for a, b in combinations(grp, 2):
            if only_pids and a["pid"] not in only_pids and b["pid"] not in only_pids:
                continue
            # cheap blocking: names must be similar
            if _name_agree(a["name"], b["name"]) < 0.85:
                continue
            s, ev = score_pair(a, b, u)
            if s >= REVIEW_THRESHOLD:
                # survivor = the canonical-looking ID (P####) / lower row number
                keep, dup = sorted([a, b], key=lambda x: (not x["pid"][1:].isdigit(), x["source_row"] or 1e9))
                if (keep["pid"], dup["pid"]) in existing:
                    continue
                found.append((keep["pid"], dup["pid"], s, ev))
    for keep, dup, s, ev in found:
        con.execute("INSERT INTO dup_candidates(a,b,score,evidence,status,ts) VALUES(?,?,?,?,?,?)",
                    (keep, dup, s, json.dumps({"u": {k: round(v, 4) for k, v in u.items()}, "fields": ev}), "open", ts))
        if s >= AUTOHOLD_THRESHOLD:
            con.execute("UPDATE people SET status='held_duplicate' WHERE pid=? AND status='active'", (dup,))
            con.execute("INSERT INTO issues(entity,entity_id,code,severity,message,ts) VALUES(?,?,?,?,?,?)",
                        ("participant", dup, "PROBABLE_DUPLICATE", "high",
                         f"Probable duplicate of {keep} ({s:.0f} bits of evidence). Held out of bed, kit and meal plans until a person confirms or rejects the merge.", ts))
    return found, u


def merge(con, keep, dup, actor, reason, ts):
    """Merge dup into keep: move registrations, void dup's merch, release dup's bed."""
    moved = 0
    for r in con.execute("SELECT eid, team_id FROM registrations WHERE pid=?", (dup,)).fetchall():
        exists = con.execute("SELECT 1 FROM registrations WHERE pid=? AND eid=?", (keep, r[0])).fetchone()
        if not exists:
            con.execute("UPDATE registrations SET pid=? WHERE pid=? AND eid=?", (keep, dup, r[0]))
            moved += 1
        else:
            con.execute("DELETE FROM registrations WHERE pid=? AND eid=?", (dup, r[0]))
    voided = con.execute("UPDATE merch_issues SET voided=1, note='voided: merged duplicate' WHERE pid=? AND voided=0", (dup,)).rowcount
    freed = con.execute("DELETE FROM alloc WHERE pid=?", (dup,)).rowcount
    con.execute("UPDATE people SET status='merged', merged_into=? WHERE pid=?", (keep, dup))
    con.execute("UPDATE dup_candidates SET status='merged', decided_by=?, reason=?, ts=? WHERE a=? AND b=?", (actor, reason, ts, keep, dup))
    con.execute("UPDATE issues SET status='resolved', resolution=?, resolved_by=? WHERE entity_id=? AND code='PROBABLE_DUPLICATE'",
                (f"merged into {keep}", actor, dup))
    return {"registrations_moved": moved, "merch_voided": voided, "beds_freed": freed}


def reject(con, keep, dup, actor, reason, ts):
    con.execute("UPDATE dup_candidates SET status='rejected', decided_by=?, reason=?, ts=? WHERE a=? AND b=?", (actor, reason, ts, keep, dup))
    con.execute("UPDATE people SET status='active' WHERE pid=? AND status='held_duplicate'", (dup,))
    con.execute("UPDATE issues SET status='resolved', resolution='not a duplicate', resolved_by=? WHERE entity_id=? AND code='PROBABLE_DUPLICATE'", (actor, dup))
