#!/usr/bin/env python3
"""FestOS Interactive / Direct SQL Runner for Judges.
Allows running SQL queries directly from CMD without installing sqlite3 CLI:
    python tools/sql.py "SELECT pid, name, college FROM people LIMIT 5"
Or simply:
    python tools/sql.py   (opens an interactive SQL terminal)
"""
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "festos.db")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def run_query(sql):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute(sql)
        if sql.strip().upper().startswith(("SELECT", "PRAGMA")):
            rows = cur.fetchall()
            if not rows:
                print("(0 rows returned)")
                return
            cols = rows[0].keys()
            # calculate widths
            col_widths = {c: len(c) for c in cols}
            str_rows = []
            for r in rows:
                srow = {c: str(r[c]) if r[c] is not None else "NULL" for c in cols}
                for c in cols:
                    if len(srow[c]) > col_widths[c]:
                        col_widths[c] = min(40, max(col_widths[c], len(srow[c])))
                str_rows.append(srow)
            
            header = " | ".join(f"{c:<{col_widths[c]}}" for c in cols)
            sep = "-+-".join("-" * col_widths[c] for c in cols)
            print("\n" + header)
            print(sep)
            for sr in str_rows:
                print(" | ".join(f"{sr[c][:col_widths[c]]:<{col_widths[c]}}" for c in cols))
            print(f"({len(rows)} row{'s' if len(rows) != 1 else ''})\n")
        else:
            con.commit()
            print(f"Query executed successfully ({cur.rowcount} rows affected).\n")
    except Exception as e:
        print(f"SQL Error: {e}\n")
    finally:
        con.close()

def interactive():
    print("=" * 60)
    print(" FestOS Interactive SQL Console")
    print(f" Connected to: {DB_PATH}")
    print(" Type your SQL query and press Enter. Type 'exit' or 'quit' to leave.")
    print("=" * 60)
    while True:
        try:
            query = input("sql> ").strip()
            if not query:
                continue
            if query.lower() in ("exit", "quit", "q"):
                break
            run_query(query)
        except (KeyboardInterrupt, EOFError):
            print("\nExiting SQL console.")
            break

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        run_query(query)
    else:
        interactive()
