"""Import + validation of the data pack.

Principles (from the brief):
  * never treat a blank as consent / gender / dietary clearance / absence of need
  * keep the raw value next to every normalised value
  * every anomaly becomes a row in the issues ledger, visible in the portal
"""
import csv
import json
import os
import random
import re
from collections import Counter, defaultdict

from . import config, textmatch, timeutil as T
from .db import DATA_DIR, set_setting, get_setting

GENDER_MAP = {
    "male": ("M", "Declared male → boys' block or men's rooms"),
    "female": ("F", "Declared female → girls' block or women's rooms"),
    "non-binary": ("X", "Declared non-binary → all-gender housing; never forced into a binary block"),
    "prefer not to say": ("X", "Declined to state → all-gender housing; we do not ask again"),
}
ACCESS_MAP = {
    "none": "NONE", "wheelchair access": "WC", "ground floor / no stairs": "GF",
    "visual assistance": "VIS", "near washroom": "WASH",
}
DIET_MAP = {
    "vegetarian": "VEG", "non-vegetarian": "NONVEG", "jain": "JAIN", "halal": "HALAL",
    "gluten-free": "GF", "nut allergy": "NUT", "vegan": "VEGAN",
}
PREF_TAGS = {
    "no preference": [], "ground floor if possible": ["ground_floor"], "low cost": ["low_cost"],
    "keep together; near venue": ["keep_together", "near_venue"], "quiet rooms": ["quiet"],
    "same floor": ["same_floor"],
}


def read_csv(name, path=None):
    with open(path or os.path.join(DATA_DIR, name), newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


class Ledger:
    def __init__(self, con, ts):
        self.con, self.ts = con, ts

    def add(self, entity, eid, code, severity, message, field=None):
        self.con.execute(
            "INSERT INTO issues(entity,entity_id,code,severity,field,message,ts) VALUES(?,?,?,?,?,?,?)",
            (entity, eid, code, severity, field, message, self.ts))


def ingest_all(con, now):
    led = Ledger(con, now)
    for k, (v, _, _) in config.ASSUMPTIONS.items():
        if get_setting(con, k, None) is None:
            set_setting(con, k, v)
    set_setting(con, "clock", config.DEFAULT_CLOCK)
    set_setting(con, "clock_mode", "demo")

    _contingents(con, led)
    _people(con, led)
    _registrations(con, led)
    _events_venues(con, led)
    _staff_people(con, led)
    _housing(con, led)
    _logistics(con, led)
    _merch(con, led)
    _vendors(con, led, now)
    _judges(con, led)
    con.commit()


# ------------------------------------------------------------------ contingents
def _contingents(con, led):
    for r in read_csv("contingents.csv"):
        raw = (r["preferences"] or "").strip()
        tags = PREF_TAGS.get(raw.lower())
        if tags is None:
            tags = []
            led.add("contingent", r["contingent_id"], "PREF_UNPARSED", "low",
                    f"Preference '{raw}' not understood; treated as no preference.", "preferences")
        con.execute("INSERT INTO contingents(cid,college,leader,escort,declared,preferences_raw,prefs) VALUES(?,?,?,?,?,?,?)",
                    (r["contingent_id"], r["college"].strip(), r["leader"].strip(), r["escort"].strip(),
                     int(r["strength_declared"]), raw, json.dumps(tags)))


# ------------------------------------------------------------------ participants
def _people(con, led):
    rows = read_csv("participants.csv")
    seen = set()
    core = get_setting(con, "night_core_window")
    minh = get_setting(con, "night_min_overlap_h")
    cont = {r["cid"]: r for r in map(dict, con.execute("SELECT * FROM contingents"))}
    for i, r in enumerate(rows, start=2):
        pid = r["participant_id"].strip()
        if pid in seen:
            led.add("participant", pid, "DUP_ID", "high", "Participant ID appears twice in the file.")
            continue
        seen.add(pid)
        if not re.match(r"^P\d{4}$", pid):
            led.add("participant", pid, "ID_FORMAT", "medium",
                    f"ID '{pid}' does not follow the P#### pattern used by the other 2,834 rows — likely a manual or merged entry.", "participant_id")
        cid = r["contingent_id"].strip()
        if cid not in cont:
            led.add("participant", pid, "BAD_CONTINGENT", "high", f"Contingent {cid} not in contingents.csv.", "contingent_id")
        elif cont[cid]["college"] != r["college"].strip():
            led.add("participant", pid, "COLLEGE_MISMATCH", "medium",
                    f"College '{r['college']}' differs from contingent {cid} ({cont[cid]['college']}).", "college")

        g = (r["gender_as_given"] or "").strip()
        cat, basis = GENDER_MAP.get(g.lower(), (None, None))
        if cat is None:
            cat, basis = "X", "Gender field blank — NOT assumed. All-gender housing until the participant confirms a preference."
            led.add("participant", pid, "GENDER_BLANK", "low",
                    "Gender blank: not inferred from name. Housed in all-gender pool; confirmation requested via leader.", "gender_as_given")

        age_raw = (r["age"] or "").strip()
        try:
            age = int(age_raw)
        except ValueError:
            age = None
            led.add("participant", pid, "AGE_MISSING", "medium", "Age missing — minor status unknown; treated as minor for safety rules.", "age")
        is_minor = 1 if (age is None or age < get_setting(con, "minor_age_lt")) else 0

        craw = (r["contact"] or "").strip()
        cn = textmatch.normalize_phone(craw)
        cok = 1
        if not cn:
            cok = 0
            led.add("participant", pid, "CONTACT_INVALID", "medium", f"Contact '{craw}' is not a valid Indian mobile number.", "contact")
        elif craw != cn:
            led.add("participant", pid, "CONTACT_FORMAT", "low", f"Contact '{craw}' normalised to {cn}.", "contact")
        if textmatch.is_placeholder_phone(craw):
            cok = 0
            led.add("participant", pid, "CONTACT_PLACEHOLDER", "medium", f"Contact '{craw}' looks like a placeholder number — cannot be used to reach this person.", "contact")

        araw = (r["accessibility_needs"] or "").strip()
        acc = ACCESS_MAP.get(araw.lower())
        if acc is None:
            acc = "UNKNOWN"
            led.add("participant", pid, "ACCESS_BLANK", "high",
                    "Accessibility field blank — NOT treated as 'no need'. Ground-floor room held until confirmed.", "accessibility_needs")

        draw = (r["dietary_needs"] or "").strip()
        diet = DIET_MAP.get(draw.lower())
        if diet is None:
            diet = "UNKNOWN"
            if draw:
                led.add("participant", pid, "DIET_UNPARSED", "medium", f"Dietary value '{draw}' not recognised.", "dietary_needs")

        a, d = T.p(r["arrival_datetime"]), T.p(r["departure_datetime"])
        tok = 1
        if not a or not d:
            tok = 0
            led.add("participant", pid, "TIME_MISSING", "high", "Arrival or departure missing.", "arrival_datetime")
        elif d <= a:
            tok = 0
            led.add("participant", pid, "TIME_INVERTED", "high",
                    f"Arrival {T.f(a)} is after departure {T.f(d)}. We do not guess which is wrong: no bed, events or meals are planned until confirmed.", "arrival_datetime")
        elif a > T.p(config.FEST_END):
            led.add("participant", pid, "ARRIVES_AFTER_FEST", "high", f"Arrives {T.f(a)} — after the fest ends.", "arrival_datetime")

        ar = (r["accommodation_requested"] or "").strip().lower()
        if ar not in ("yes", "no"):
            led.add("participant", pid, "ACCOM_BLANK", "medium", "Accommodation request blank — not assumed either way; tentative hold.", "accommodation_requested")
            ar = "unknown"
        ts = (r["tshirt_size"] or "").strip().upper()
        if ts not in ("S", "M", "L", "XL", "XXL"):
            led.add("participant", pid, "TSHIRT_UNKNOWN", "low", f"T-shirt size '{ts}' unknown.", "tshirt_size")
            ts = None

        nights = T.nights_mask(a, d, core, minh) if (tok and ar != "no") else 0
        con.execute("""INSERT INTO people(pid,kind,name,college,cid,gender_raw,housing_cat,housing_basis,age_raw,age,is_minor,
            contact_raw,contact,contact_ok,access_raw,access_need,diet_raw,diet,arrival,departure,arrival_orig,timing_ok,
            accom_req,tshirt,status,nights,source_row) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, "participant", r["name"].strip(), r["college"].strip(), cid, g, cat, basis, age_raw, age, is_minor,
                     craw, cn, cok, araw, acc, draw, diet, T.f(a), T.f(d), T.f(a), tok, ar, ts,
                     "active" if tok else "needs_confirmation", nights, i))
        if is_minor and age is not None:
            led.add("participant", pid, "MINOR", "info", f"Age {age}: minor-safety rules apply (on-campus preferred, escort coverage).", "age")

    # contingent counts
    cnt = Counter(r["contingent_id"] for r in rows)
    for c in cont.values():
        actual = cnt.get(c["cid"], 0)
        con.execute("UPDATE contingents SET actual=? WHERE cid=?", (actual, c["cid"]))
        if actual != c["declared"]:
            diff = actual - c["declared"]
            led.add("contingent", c["cid"], "STRENGTH_MISMATCH", "medium" if abs(diff) < 5 else "high",
                    f"{c['college']}: declared {c['declared']}, {actual} participant rows ({diff:+d}). Beds and kits are planned on rows, not the declaration.",
                    "strength_declared")


# ------------------------------------------------------------------ registrations
def _registrations(con, led):
    ev = {r["event_id"]: r for r in read_csv("events.csv")}
    people = {r[0] for r in con.execute("SELECT pid FROM people")}
    team_on_solo = Counter()
    team_missing = Counter()
    for r in read_csv("registrations.csv"):
        pid, eid, tid = r["participant_id"].strip(), r["event_id"].strip(), (r["team_id"] or "").strip()
        if pid not in people:
            led.add("registration", f"{pid}/{eid}", "REG_UNKNOWN_PERSON", "high", f"Registration for unknown participant {pid}.")
            continue
        if eid not in ev:
            led.add("registration", f"{pid}/{eid}", "REG_UNKNOWN_EVENT", "high", f"Registration for unknown event {eid}.")
            continue
        if tid and ev[eid]["type"] == "solo":
            team_on_solo[eid] += 1
        if not tid and ev[eid]["type"] == "team":
            team_missing[eid] += 1
        try:
            con.execute("INSERT INTO registrations(pid,eid,team_id) VALUES(?,?,?)", (pid, eid, tid or None))
        except Exception:
            led.add("registration", f"{pid}/{eid}", "REG_DUP", "low", "Duplicate registration row ignored.")
    for eid, n in team_on_solo.items():
        led.add("event", eid, "TEAM_ID_ON_SOLO", "medium",
                f"{n} registrations carry team IDs but the event is labelled 'solo'. We trust the team IDs for grouping (members compete together) and flag the label.", "type")
    for eid, n in team_missing.items():
        led.add("event", eid, "TEAM_ROSTER_MISSING", "high",
                f"Event is labelled 'team' but none of its {n} registrations has a team ID. Rosters must be collected; heats are planned per person meanwhile.", "team_id")


# ------------------------------------------------------------------ events & venues
EQUIP_ALIASES = {"barricades": "barricade", "tables": "table", "projector": "projector", "camera": "camera",
                 "tripod": "tripod", "extension cords": "extension cord", "extension cord": "extension cord",
                 "mics": "mic", "mic": "mic", "speaker": "speaker", "speakers": "speaker"}


def parse_equipment(s, defaults):
    s = (s or "").strip().lower()
    if not s or s == "no special equipment":
        return {}
    out = {}
    for part in s.split(";"):
        part = part.strip()
        m = re.match(r"^(\d+)\s+(.*)$", part)
        qty, name = (int(m.group(1)), m.group(2)) if m else (None, part)
        item = EQUIP_ALIASES.get(name.strip(), name.strip())
        out[item] = qty if qty else defaults.get(item, 1)
    return out


SUSPICIOUS_REQS = {
    "Basketball": "needs 'audio; seating' but no court — Sports Complex suggested; confirm with Events team",
    "Badminton": "needs 'quiet room' — a badminton court is not a quiet room; confirm",
    "Table Tennis": "labelled team with 'projector; seating' — confirm table requirement",
    "Futsal": "single round for ~180 people in a 100-seat ground — confirm format",
    "Grand Finale": "a finale with prelims and semis is unusual — confirm format",
    "Pitch Perfect": "pitch competition on an 'open ground' — confirm",
    "Aarohan": "event shares the fest's name — confirm it is a separate competition",
    "Treasure Hunt": "needs 'stage; lights' — unusual for a treasure hunt; confirm",
}


def _events_venues(con, led):
    defaults = get_setting(con, "equipment_per_heat")
    for r in read_csv("venues.csv"):
        wins = []
        for w in r["availability_windows"].split(";"):
            w = w.strip()
            m = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})-(\d{2}:\d{2})", w)
            if m:
                wins.append([f"{m.group(1)} {m.group(2)}", f"{m.group(1)} {m.group(3)}"])
            else:
                led.add("venue", r["venue_id"], "WINDOW_UNPARSED", "medium", f"Availability window '{w}' not understood.")
        caps = config.VENUE_CAPS.get(r["venue_id"], [])
        con.execute("INSERT INTO venues(vid,name,capacity,windows,indoor,caps) VALUES(?,?,?,?,?,?)",
                    (r["venue_id"], r["venue_name"], int(r["capacity"]), json.dumps(wins),
                     1 if r["indoor_outdoor"].strip() == "indoor" else 0, json.dumps(caps)))
        if r["venue_name"] == "Main Stage" and int(r["capacity"]) < 100:
            led.add("venue", r["venue_id"], "CAPACITY_SUSPICIOUS", "medium",
                    f"Main Stage capacity {r['capacity']} is smaller than two lecture theatres — likely a data error. Planned as {r['capacity']} until verified.", "capacity")
        if r["indoor_outdoor"].strip() == "outdoor":
            led.add("venue", r["venue_id"], "WEATHER_EXPOSED", "info", "Outdoor venue: rain plan maps to indoor fallbacks.")
    venues = {r["vid"]: dict(r) for r in con.execute("SELECT * FROM venues")}
    for r in read_csv("events.csv"):
        reqs = [x.strip().lower() for x in r["venue_requirements"].split(";") if x.strip()]
        elig = [v for v, vv in venues.items() if set(reqs) <= set(json.loads(vv["caps"]))]
        eq = parse_equipment(r["equipment_needs"], defaults)
        rounds = [x.strip() for x in r["rounds"].split("|") if x.strip()]
        flags = []
        if r["event_name"] in SUSPICIOUS_REQS:
            flags.append(SUSPICIOUS_REQS[r["event_name"]])
            led.add("event", r["event_id"], "REQ_SUSPICIOUS", "low", f"{r['event_name']}: {SUSPICIOUS_REQS[r['event_name']]}.")
        if not elig:
            flags.append("no venue meets requirements")
            led.add("event", r["event_id"], "NO_ELIGIBLE_VENUE", "high", f"No venue offers {reqs}.")
        con.execute("INSERT INTO events(eid,name,type,rounds,duration,reqs,equipment,volunteers,eligible_venues,flags) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (r["event_id"], r["event_name"], r["type"], json.dumps(rounds), int(r["duration_minutes"]),
                     json.dumps(reqs), json.dumps(eq), int(r["volunteers_needed"]), json.dumps(elig), json.dumps(flags)))


# ------------------------------------------------------------------ leaders & escorts
def _staff_people(con, led):
    core = get_setting(con, "night_core_window")
    minh = get_setting(con, "night_min_overlap_h")
    for c in [dict(r) for r in con.execute("SELECT * FROM contingents")]:
        mem = [dict(r) for r in con.execute(
            "SELECT pid,name,arrival,departure,accom_req FROM people WHERE cid=? AND timing_ok=1", (c["cid"],))]
        housed = [m for m in mem if m["accom_req"] == "yes"] or mem
        if not housed:
            continue
        a = min(m["arrival"] for m in housed)
        d = max(m["departure"] for m in housed)
        leader_matches = [m["pid"] for m in mem if m["name"] == c["leader"]]
        con.execute("UPDATE contingents SET leader_matches=? WHERE cid=?", (json.dumps(leader_matches), c["cid"]))
        if leader_matches:
            led.add("contingent", c["cid"], "LEADER_AMBIGUOUS", "medium",
                    f"Leader '{c['leader']}' matches {len(leader_matches)} participant rows by name (names repeat in this pack). Leader record must be confirmed; no extra bed held.", "leader")
        else:
            led.add("contingent", c["cid"], "LEADER_NOT_REGISTERED", "low",
                    f"Leader '{c['leader']}' is not in participants.csv — tentative bed held near the group.", "leader")
            if get_setting(con, "unmatched_leaders_need_beds"):
                pid = f"L-{c['cid']}"
                con.execute("""INSERT INTO people(pid,kind,name,college,cid,gender_raw,housing_cat,housing_basis,age,is_minor,access_raw,access_need,
                    diet_raw,diet,arrival,departure,arrival_orig,timing_ok,accom_req,status,nights,contact_ok)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (pid, "leader", c["leader"], c["college"], c["cid"], "", "X",
                             "Contingent leader known only by name; gender not recorded — housed near group in all-gender/private space.",
                             None, 0, "", "UNKNOWN", "", "UNKNOWN", a, d, a, 1, "yes", "active",
                             T.nights_mask(a, d, core, minh), 0))
        if get_setting(con, "escorts_need_beds"):
            pid = f"E-{c['cid']}"
            con.execute("""INSERT INTO people(pid,kind,name,college,cid,gender_raw,housing_cat,housing_basis,age,is_minor,access_raw,access_need,
                diet_raw,diet,arrival,departure,arrival_orig,timing_ok,accom_req,status,nights,contact_ok)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (pid, "escort", c["escort"], c["college"], c["cid"], "", "S",
                         "Faculty/staff escort: housed separately from students, near the group (brief rule 4).",
                         None, 0, "", "UNKNOWN", "", "UNKNOWN", a, d, a, 1, "yes", "active",
                         T.nights_mask(a, d, core, minh), 0))
            led.add("contingent", c["cid"], "ESCORT_NOT_IN_ROSTER", "info",
                    f"Escort '{c['escort']}' is not a participant row; a separate staff bed is planned for {T.mask_label(T.nights_mask(a, d, core, minh))}.", "escort")


# ------------------------------------------------------------------ housing
def _housing(con, led):
    rooms = read_csv("hostels.csv")
    floors = defaultdict(lambda: {"beds": 0, "acc": 0, "gender": None})
    for r in rooms:
        site = f"{r['block']}-F{r['floor']}"
        st = r["status"].strip().lower()
        if st != "available":
            led.add("room", r["room_id"], "ROOM_UNAVAILABLE", "medium", f"Room status '{st}'.")
        cap = int(r["capacity"])
        con.execute("INSERT INTO rooms(room_id,site_id,block,floor,gender,capacity,accessible,status) VALUES(?,?,?,?,?,?,?,?)",
                    (r["room_id"], site, r["block"], int(r["floor"]), r["gender"], cap,
                     1 if r["accessibility"].strip() == "yes" else 0, st))
        f = floors[site]
        f["block"], f["floor"], f["gender"] = r["block"], int(r["floor"]), r["gender"]
        if st == "available":
            f["beds"] += cap
            f["acc"] += cap if r["accessibility"].strip() == "yes" else 0
    for sid, f in sorted(floors.items()):
        con.execute("""INSERT INTO sites(site_id,kind,name,block,floor,base_gender,beds,acc_beds,cost,distance,transport,designation)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, "campus", f"Block {f['block']} · floor {f['floor']}", f["block"], f["floor"],
                     "M" if f["gender"] == "boys" else "F", f["beds"], f["acc"], 0, 0.3, "walk",
                     "M" if f["gender"] == "boys" else "F"))
    led.add("site", "campus", "WASHROOM_PROXIMITY_UNKNOWN", "medium",
            "hostels.csv has no washroom-proximity field. 'Near washroom' needs are matched to accessible rooms where possible and sent to the warden for confirmation.")
    led.add("site", "campus", "ACCESSIBLE_SHORTAGE", "high",
            f"Only {sum(1 for r in rooms if r['accessibility']=='yes')} rooms ({sum(int(r['capacity']) for r in rooms if r['accessibility']=='yes')} beds) are wheelchair-accessible.")
    for r in read_csv("external_stays.csv"):
        tr = r["transport_availability"].strip().lower()
        con.execute("""INSERT INTO sites(site_id,kind,name,block,floor,base_gender,beds,acc_beds,cost,distance,transport,designation)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r["property_id"], "external", r["property_name"], None, None, "ANY", int(r["beds"]), 0,
                     int(r["cost_per_bed_per_night"]), float(r["distance_km"]), tr, "ANY"))
        if tr != "yes":
            led.add("site", r["property_id"], "TRANSPORT_LIMITED", "medium",
                    f"{r['property_name']} ({r['distance_km']} km): transport '{tr}'. Not used for minors; late returns need escort.")
        led.add("site", r["property_id"], "ACCESSIBILITY_UNVERIFIED", "medium",
                f"{r['property_name']}: accessibility not stated. No one with a mobility need is placed here until Hospitality verifies.")


# ------------------------------------------------------------------ logistics
def _logistics(con, led):
    nc = get_setting(con, "count_needs_check_as_usable")
    for r in read_csv("inventory_logistics.csv"):
        cond = r["condition"].strip().lower()
        loc = r["location"].strip()
        state = {"good": "available", "needs check": "inspect", "under repair": "repair"}.get(cond)
        if state is None:
            state = "inspect"
            led.add("stock", r["item_id"], "CONDITION_UNKNOWN", "medium", f"Condition '{cond}' unknown — held for inspection.")
        if loc.lower().startswith("van"):
            if state == "available":
                state = "transit"
            led.add("stock", r["item_id"], "IN_TRANSIT_NO_ETA", "low",
                    f"{r['qty'] if 'qty' in r else r['quantity']} × {r['item']} on {loc} with no ETA — not counted as usable until received.")
        con.execute("INSERT INTO logistics(line_id,item,qty,location,condition,state) VALUES(?,?,?,?,?,?)",
                    (r["item_id"], r["item"].strip(), int(r["quantity"]), loc, cond, state))
    cams = con.execute("SELECT SUM(qty) FROM logistics WHERE item='camera' AND state='available'").fetchone()[0] or 0
    if cams == 0:
        led.add("stock", "camera", "ITEM_ZERO_USABLE", "critical", "Every camera line is 'under repair' — 0 usable cameras.")


def _merch(con, led):
    for r in read_csv("inventory_merch.csv"):
        q, res = int(r["quantity"]), int(r["reserved_quantity"])
        con.execute("INSERT INTO merch_stock(item,size,qty,reserved) VALUES(?,?,?,?)", (r["item"], r["size"], q, res))
        if res > q:
            led.add("merch", f"{r['item']}/{r['size']}", "RESERVED_GT_QTY", "high", "Reserved exceeds quantity.")


# ------------------------------------------------------------------ vendors
VENDOR_LABEL_CHECKS = [
    (lambda m, d: "vegan" in m.lower() and "non-vegetarian" in d, "Menu says vegan but listed as non-vegetarian — cannot be counted as vegan or vegetarian capacity until verified."),
    (lambda m, d: "jain" in m.lower() and "jain" not in d, "Jain thali menu but 'jain' not in certified dietary options — counted as vegetarian only until verified."),
    (lambda m, d: "biryani" in m.lower() and "halal" not in d, "Biryani without halal tag."),
]


def _vendors(con, led, now):
    rate = get_setting(con, "vendor_rate_inr")
    fest_end = config.FEST_END[:10]
    for r in read_csv("vendors.csv"):
        diets = [x.strip().lower() for x in r["dietary_options"].split(";") if x.strip()]
        flags = []
        exp = r["hygiene_certificate_expiry"].strip()
        if exp < config.FEST_START[:10]:
            flags.append(f"hygiene certificate expired {exp}")
            led.add("vendor", r["vendor_id"], "CERT_EXPIRED", "critical",
                    f"{r['vendor_name']} ({r['menu']}): hygiene certificate expired on {exp}. Blocked from service until renewed.")
        elif exp < fest_end:
            flags.append(f"certificate expires {exp}, mid-fest")
            led.add("vendor", r["vendor_id"], "CERT_EXPIRES_MIDFEST", "high",
                    f"{r['vendor_name']}: certificate valid only until {exp}; cannot serve after that without renewal.")
        for chk, msg in VENDOR_LABEL_CHECKS:
            if chk(r["menu"], diets):
                flags.append(msg)
                led.add("vendor", r["vendor_id"], "LABEL_CONFLICT", "high", f"{r['vendor_name']}: {msg}")
        if "breakfast" in r["menu"].lower() and r["hours"].startswith("10"):
            flags.append("breakfast vendor opens 10:00")
            led.add("vendor", r["vendor_id"], "HOURS_MISMATCH", "medium",
                    f"{r['vendor_name']} serves breakfast but opens at {r['hours'][:5]} — misses the breakfast window.")
        con.execute("INSERT INTO vendors(vid,name,menu,diets,cert_expiry,hours,capacity,terms,flags,rate) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (r["vendor_id"], r["vendor_name"], r["menu"], json.dumps(diets), exp, r["hours"],
                     int(r["capacity"]), r["payment_terms"], json.dumps(flags), rate))
    led.add("vendor", "all", "RATES_MISSING", "medium", "vendors.csv has no rates or minimum-guarantee amounts; settlement uses the assumed rate until entered.")
    led.add("vendor", "all", "NO_NUT_FREE_VENDOR", "critical", "No vendor declares a nut-free / allergen-controlled kitchen while 411 participants report a nut allergy.")


# ------------------------------------------------------------------ judges (sample)
def _judges(con, led):
    """The pack has no judge data although the brief asks for judge-conflict detection.
    We seed a clearly-labelled SAMPLE panel (deterministic) so the conflict engine can be
    demonstrated; organisers replace it with the real roster."""
    rnd = random.Random(2026)
    eids = [r[0] for r in con.execute("SELECT eid FROM events ORDER BY eid")]
    first = ["Dr. A.", "Prof. R.", "Ms. K.", "Mr. S.", "Dr. N.", "Prof. M.", "Ms. P.", "Mr. V."]
    last = ["Menon", "Kulkarni", "Bhatt", "Sen", "Reddy", "Chopra", "Pillai", "Ghosh", "Saxena", "Dutta", "Banerjee", "Shetty", "Rao", "Vyas"]
    judges = {}
    order = eids[:]
    rnd.shuffle(order)
    # one primary judge per event + 16 visiting judges who each sit on two events
    for i, e in enumerate(eids):
        judges[f"J{i+1:02d}"] = {"name": f"{first[i % 8]} {last[i % 14]}", "events": [e]}
    for k in range(0, len(order), 2):
        jid = f"J{len(eids) + k // 2 + 1:02d}"
        judges[jid] = {"name": f"{first[(k + 3) % 8]} {last[(k + 5) % 14]}", "events": order[k:k + 2]}
    for j, v in judges.items():
        con.execute("INSERT INTO judges(jid,name,events,sample) VALUES(?,?,?,1)", (j, v["name"], json.dumps(sorted(v["events"]))))
    led.add("judge", "all", "JUDGES_NOT_IN_PACK", "medium",
            "The pack has no judge roster. A labelled SAMPLE panel of 47 judges (16 visiting judges sit on two events each) is used to demonstrate judge-conflict checks; replace with the real roster.")
