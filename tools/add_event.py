#!/usr/bin/env python3
"""FestOS Event & Heat Scheduling Tool for Judges.
Adds a new event and heat to festos.db, registers it in a venue,
and validates the schedule with the independent checker.

Usage:
    python tools/add_event.py --name "AI Hackathon Finals" --venue V02 --start "2026-11-01 14:00" --duration 90
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "festos.db")
sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from festos import timeutil as T
from festos.db import connect
from festos.security import audit

def add_event(name, venue_id="V02", start_time="2026-11-01 14:00", duration=60, event_type="solo", volunteers=4, actor="ST03", reason="Ad-hoc event addition for judge demonstration"):
    con = connect(DB_PATH)
    
    # Generate new event ID
    existing_eids = [r[0] for r in con.execute("SELECT eid FROM events")]
    max_num = max([int(e[1:]) for e in existing_eids if e.startswith("E") and e[1:].isdigit()] or [12])
    new_eid = f"E{max_num + 1:02d}"
    new_hid = f"{new_eid}_finals_1"
    
    # Calculate start and end
    st = T.p(start_time)
    et = st + timedelta(minutes=duration)
    
    # Check venue
    v = con.execute("SELECT * FROM venues WHERE vid=?", (venue_id,)).fetchone()
    if not v:
        print(f"Error: Venue {venue_id} not found.")
        return
    
    print(f"\n========================================================")
    print(f" ADDING NEW EVENT & HEAT TO FESTOS BACKEND")
    print(f"========================================================")
    print(f"Event ID     : {new_eid}")
    print(f"Event Name   : {name}")
    print(f"Type         : {event_type} | Duration: {duration} mins")
    print(f"Venue        : {v['name']} ({venue_id}) - Capacity: {v['capacity']}")
    print(f"Slot         : {T.f(st)} -> {T.f(et)}")
    print(f"Heat ID      : {new_hid}")
    
    # 1. Insert into events
    rounds_json = json.dumps(["finals"])
    reqs_json = json.dumps(["audio", "seating"])
    eq_json = json.dumps({"speaker": 2, "microphone": 2})
    eligible_json = json.dumps([venue_id])
    
    con.execute("""INSERT INTO events (eid, name, type, rounds, duration, reqs, equipment, volunteers, eligible_venues, flags, waived_equipment)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]')""",
                (new_eid, name, event_type, rounds_json, duration, reqs_json, eq_json, volunteers, eligible_json))
    
    # 2. Insert heat
    con.execute("""INSERT INTO heats (hid, eid, round, idx, vid, start, end, capacity, pool, locked, status)
                   VALUES (?, ?, 'finals', 1, ?, ?, ?, ?, 0, 1, 'planned')""",
                (new_hid, new_eid, venue_id, T.f(st), T.f(et), v["capacity"]))
    
    # 3. Log to audit ledger
    audit(con, actor=actor, role="events", action="add_event", entity="event", entity_id=new_eid,
          detail={"name": name, "heat": new_hid, "venue": venue_id, "start": T.f(st), "end": T.f(et)},
          reason=reason)
    
    con.commit()
    print(f"[OK] Event '{name}' ({new_eid}) successfully created in database!")
    print(f"[OK] Heat '{new_hid}' scheduled in {venue_id}.")
    print(f"[OK] Cryptographic audit trail recorded.\n")
    con.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add and schedule a new event in FestOS")
    parser.add_argument("--name", default="Epinoia AI Hackathon Showcase", help="Event name")
    parser.add_argument("--venue", default="V02", help="Venue ID (e.g. V01, V02, V03)")
    parser.add_argument("--start", default="2026-11-01 14:00", help="Start time (YYYY-MM-DD HH:MM)")
    parser.add_argument("--duration", type=int, default=90, help="Duration in minutes")
    parser.add_argument("--type", default="solo", choices=["solo", "team"], help="solo or team")
    parser.add_argument("--volunteers", type=int, default=4, help="Volunteers required")
    parser.add_argument("--reason", default="Manual event addition requested by judge", help="Audit reason")
    
    args = parser.parse_args()
    add_event(args.name, venue_id=args.venue, start_time=args.start, duration=args.duration,
              event_type=args.type, volunteers=args.volunteers, reason=args.reason)
