#!/usr/bin/env python3
"""Judge Backend Demonstration Tool:
1. Query raw database records (people, alloc, audit).
2. Execute backend manual allocations with cryptographic audit trails.
3. Test independent invariant verification on the database.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "festos.db")

def show_person(pid="P0001"):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    p = con.execute("SELECT pid, name, college, housing_cat, access_need, nights, status FROM people WHERE pid=?", (pid,)).fetchone()
    a = con.execute("SELECT site_id, room_id, bed, nights, status, cost, source FROM alloc WHERE pid=?", (pid,)).fetchone()
    
    print(f"\n[1] RAW SQL QUERY: 'SELECT * FROM people WHERE pid=\"{pid}\"'")
    if p:
        print(f"    ID: {p['pid']} | Name: {p['name']} | College: {p['college']}")
        print(f"    Gender/Category: {p['housing_cat']} | Access: {p['access_need']} | Nights bitmask: {p['nights']}")
    
    print(f"\n[2] RAW SQL QUERY: 'SELECT * FROM alloc WHERE pid=\"{pid}\"'")
    if a:
        print(f"    Site: {a['site_id']} | Room: {a['room_id']} | Bed: #{a['bed']} | Status: {a['status']} | Source: {a['source']}")
    else:
        print("    [Unplaced / No allocation]")

def show_audit(limit=3):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT seq, ts, actor, action, entity_id, reason, prev_hash, hash FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
    print(f"\n[3] RAW SQL QUERY: 'SELECT seq, ts, actor, action, hash FROM audit ORDER BY seq DESC LIMIT {limit}'")
    for r in rows:
        print(f"    Seq #{r['seq']} [{r['ts']}] Actor: {r['actor']:<6} Action: {r['action']:<15} Entity: {r['entity_id']}")
        print(f"      Hash:     {r['hash'][:36]}...")
        print(f"      PrevHash: {r['prev_hash'][:36]}...")

def manual_backend_assign(pid, target_site, target_room, bed_num, actor="ST08", reason="Manual backend override for judge demonstration"):
    """Performs an authorized backend manual allocation directly into SQLite with cryptographic audit log."""
    from festos.db import connect
    from festos.security import audit as log_audit, verify_chain
    from festos import timeutil as T
    
    con = connect(DB_PATH)
    p = con.execute("SELECT * FROM people WHERE pid=?", (pid,)).fetchone()
    if not p:
        print(f"Error: Person {pid} not found.")
        return
    
    # 1. Update alloc table
    con.execute("INSERT OR REPLACE INTO alloc(pid, site_id, room_id, bed, nights, cost, status, explain, source) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid, target_site, target_room, bed_num, p["nights"], 0, "placed",
                 json.dumps([f"Manual backend assignment by {actor}: {reason}"]), "override"))
    
    # 2. Append to immutable hash-chained audit table
    log_audit(con, actor=actor, role="director", action="manual_bed_assign", entity="person", entity_id=pid,
              detail={"site": target_site, "room": target_room, "bed": bed_num}, reason=reason)
    con.commit()
    
    print(f"\n[OK] Successfully executed backend assignment for {pid} -> {target_room} (Bed #{bed_num})")
    
    # Verify chain
    chain_check = verify_chain(con)
    print(f"[OK] Audit SHA-256 chain verified: {chain_check['ok']} ({chain_check.get('checked')} verified blocks)")
    con.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Judge Backend Inspection & Edit Demo")
    parser.add_argument("--pid", default="P0001", help="Participant ID")
    parser.add_argument("--assign", action="store_true", help="Demonstrate manual backend assignment")
    parser.add_argument("--site", default="G2-F1", help="Target site ID")
    parser.add_argument("--room", default="G2-1-04", help="Target room ID")
    parser.add_argument("--bed", type=int, default=1, help="Bed number")
    parser.add_argument("--reason", default="Manual override approved by Festival Director for judge demo")
    
    args = parser.parse_args()
    
    if args.assign:
        print(f"Executing manual backend assignment for {args.pid}...")
        manual_backend_assign(args.pid, args.site, args.room, args.bed, reason=args.reason)
    
    show_person(args.pid)
    show_audit(3)
