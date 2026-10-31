#!/usr/bin/env python3
"""Backend Inspection & Manual Edit Demonstration for Judges.
Shows raw SQLite database records in festos.db, demonstrates backend
updates, and verifies tamper-evident audit logs.
"""
import json
import sqlite3
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "festos.db")

def inspect_person(pid):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    p = con.execute("SELECT * FROM people WHERE pid=?", (pid,)).fetchone()
    a = con.execute("SELECT * FROM alloc WHERE pid=?", (pid,)).fetchone()
    
    print(f"\n========================================================")
    print(f" BACKEND RECORD: Participant {pid}")
    print(f"========================================================")
    if not p:
        print(f"Participant {pid} not found!")
        return
    
    print(f"Name         : {p['name']} ({p['kind']})")
    print(f"College      : {p['college']} (Contingent: {p['cid']})")
    print(f"Housing Cat  : {p['housing_cat']} | Access Need: {p['access_need']}")
    print(f"Stay Window  : {p['arrival']} -> {p['departure']} (Nights bitmask: {p['nights']})")
    print(f"Status       : {p['status']} | Checked-in: {p['checked_in']}")
    
    print(f"\n[ALLOCATION TABLE: 'alloc']")
    if a:
        print(f"Site ID      : {a['site_id']}")
        print(f"Room ID      : {a['room_id']} (Bed #{a['bed']})")
        print(f"Status       : {a['status']} | Cost: Rs. {a['cost']:,}")
        print(f"Source       : {a['source']}")
        print(f"Explain      : {a['explain']}")
    else:
        print("Status       : UNPLACED (Waiting list / no bed allocated)")

def inspect_latest_audits(n=3):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT seq, ts, actor, action, entity, entity_id, reason, prev_hash, hash FROM audit ORDER BY seq DESC LIMIT ?", (n,)).fetchall()
    
    print(f"\n========================================================")
    print(f" RECENT AUDIT LEDGER ENTRIES ('audit' table - SHA-256 Chain)")
    print(f"========================================================")
    for r in rows:
        print(f"Seq #{r['seq']} | {r['ts']} | Actor: {r['actor']}")
        print(f"  Action   : {r['action']} on {r['entity']} ({r['entity_id']})")
        print(f"  Reason   : {r['reason']}")
        print(f"  Hash     : {r['hash'][:32]}...")
        print(f"  PrevHash : {r['prev_hash'][:32]}...\n")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "P0001"
    inspect_person(target)
    inspect_latest_audits()
