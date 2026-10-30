"""SQLite storage. Every engine takes a connection, so the same code runs on the
live database or on an in-memory copy (used to preview a disruption before
committing it)."""
import json
import os
import sqlite3
import threading

from . import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("FESTOS_DB", os.path.join(ROOT, "festos.db"))
DATA_DIR = os.path.join(ROOT, "data")
SNAP_DIR = os.path.join(ROOT, "snapshots")

LOCK = threading.RLock()

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS contingents (
  cid TEXT PRIMARY KEY, college TEXT, leader TEXT, escort TEXT, declared INTEGER,
  preferences_raw TEXT, prefs TEXT, actual INTEGER, delay_min INTEGER DEFAULT 0,
  leader_pid TEXT, leader_matches TEXT);

CREATE TABLE IF NOT EXISTS people (
  pid TEXT PRIMARY KEY, kind TEXT, name TEXT, college TEXT, cid TEXT,
  gender_raw TEXT, housing_cat TEXT, housing_basis TEXT,
  age_raw TEXT, age INTEGER, is_minor INTEGER,
  contact_raw TEXT, contact TEXT, contact_ok INTEGER,
  access_raw TEXT, access_need TEXT,
  diet_raw TEXT, diet TEXT,
  arrival TEXT, departure TEXT, arrival_orig TEXT, timing_ok INTEGER,
  accom_req TEXT, tshirt TEXT,
  status TEXT DEFAULT 'active', merged_into TEXT,
  checked_in TEXT, nights INTEGER DEFAULT 0, source_row INTEGER);
CREATE INDEX IF NOT EXISTS ix_people_cid ON people(cid);

CREATE TABLE IF NOT EXISTS registrations (pid TEXT, eid TEXT, team_id TEXT, heat_id TEXT,
  PRIMARY KEY (pid, eid));
CREATE INDEX IF NOT EXISTS ix_reg_eid ON registrations(eid);

CREATE TABLE IF NOT EXISTS events (eid TEXT PRIMARY KEY, name TEXT, type TEXT, rounds TEXT,
  duration INTEGER, reqs TEXT, equipment TEXT, volunteers INTEGER, eligible_venues TEXT,
  flags TEXT, waived_equipment TEXT DEFAULT '[]');

CREATE TABLE IF NOT EXISTS venues (vid TEXT PRIMARY KEY, name TEXT, capacity INTEGER,
  windows TEXT, indoor INTEGER, caps TEXT, blocks TEXT DEFAULT '[]');

CREATE TABLE IF NOT EXISTS heats (hid TEXT PRIMARY KEY, eid TEXT, round TEXT, idx INTEGER,
  vid TEXT, start TEXT, end TEXT, capacity INTEGER, pool INTEGER, locked INTEGER DEFAULT 0,
  status TEXT DEFAULT 'planned');
CREATE INDEX IF NOT EXISTS ix_heats_eid ON heats(eid);

CREATE TABLE IF NOT EXISTS judges (jid TEXT PRIMARY KEY, name TEXT, events TEXT, sample INTEGER);

CREATE TABLE IF NOT EXISTS rooms (room_id TEXT PRIMARY KEY, site_id TEXT, block TEXT, floor INTEGER,
  gender TEXT, capacity INTEGER, accessible INTEGER, status TEXT, closed_from TEXT);

CREATE TABLE IF NOT EXISTS sites (site_id TEXT PRIMARY KEY, kind TEXT, name TEXT, block TEXT,
  floor INTEGER, base_gender TEXT, beds INTEGER, acc_beds INTEGER, cost INTEGER, distance REAL,
  transport TEXT, designation TEXT, designation_locked INTEGER DEFAULT 0, closed_from TEXT,
  status TEXT DEFAULT 'open');

CREATE TABLE IF NOT EXISTS alloc (pid TEXT PRIMARY KEY, site_id TEXT, room_id TEXT, bed INTEGER,
  nights INTEGER, cost INTEGER, status TEXT, explain TEXT, source TEXT);

CREATE TABLE IF NOT EXISTS plans (plan_id TEXT PRIMARY KEY, created TEXT, label TEXT, params TEXT,
  metrics TEXT, adopted INTEGER DEFAULT 0, solution TEXT);

CREATE TABLE IF NOT EXISTS logistics (line_id TEXT PRIMARY KEY, item TEXT, qty INTEGER,
  location TEXT, condition TEXT, state TEXT, eta TEXT);
CREATE TABLE IF NOT EXISTS stock_moves (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, line_id TEXT,
  item TEXT, qty INTEGER, from_loc TEXT, to_loc TEXT, from_state TEXT, to_state TEXT,
  actor TEXT, reason TEXT);

CREATE TABLE IF NOT EXISTS merch_stock (item TEXT, size TEXT, qty INTEGER, reserved INTEGER,
  PRIMARY KEY (item, size));
CREATE TABLE IF NOT EXISTS merch_issues (id INTEGER PRIMARY KEY AUTOINCREMENT, pid TEXT, item TEXT,
  size TEXT, ts TEXT, actor TEXT, desk TEXT, kind TEXT, voided INTEGER DEFAULT 0, note TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_merch_once ON merch_issues(pid, item)
  WHERE kind = 'issue' AND voided = 0;
CREATE TABLE IF NOT EXISTS merch_blocked (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, pid TEXT,
  item TEXT, actor TEXT, desk TEXT, original_issue INTEGER);

CREATE TABLE IF NOT EXISTS vendors (vid TEXT PRIMARY KEY, name TEXT, menu TEXT, diets TEXT,
  cert_expiry TEXT, hours TEXT, capacity INTEGER, terms TEXT, flags TEXT,
  shutdown_from TEXT, shutdown_reason TEXT, rate INTEGER, cert_renewed TEXT);
CREATE TABLE IF NOT EXISTS vendor_covers (vid TEXT, meal_id TEXT, covers INTEGER, PRIMARY KEY (vid, meal_id));

CREATE TABLE IF NOT EXISTS issues (id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT, entity_id TEXT,
  code TEXT, severity TEXT, field TEXT, message TEXT, status TEXT DEFAULT 'open',
  resolution TEXT, resolved_by TEXT, ts TEXT);
CREATE INDEX IF NOT EXISTS ix_issues_entity ON issues(entity, entity_id);

CREATE TABLE IF NOT EXISTS dup_candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT, b TEXT,
  score REAL, evidence TEXT, status TEXT DEFAULT 'open', decided_by TEXT, reason TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS users (uid TEXT PRIMARY KEY, name TEXT, role TEXT, role_key TEXT,
  shift TEXT, phone TEXT, salt TEXT, pw_hash TEXT, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT,
  role TEXT, action TEXT, entity TEXT, entity_id TEXT, detail TEXT, reason TEXT,
  off_shift INTEGER DEFAULT 0, prev_hash TEXT, hash TEXT);

CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT,
  params TEXT, status TEXT, impact TEXT, reason TEXT, actor TEXT, snapshot TEXT, title TEXT);

CREATE TABLE IF NOT EXISTS overrides (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT,
  kind TEXT, entity_id TEXT, detail TEXT, risk TEXT, violations TEXT, reason TEXT, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, owner_role TEXT,
  title TEXT, detail TEXT, status TEXT DEFAULT 'open', link TEXT, source TEXT);
"""


def connect(path=None):
    con = sqlite3.connect(path or DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_schema(con):
    con.executescript(SCHEMA)
    con.commit()


def clone_to_memory(con):
    """Copy the live DB into memory: used to preview what-if scenarios."""
    mem = sqlite3.connect(":memory:", check_same_thread=False)
    mem.row_factory = sqlite3.Row
    con.backup(mem)
    return mem


# ---------------------------------------------------------------- settings
def get_setting(con, key, default=None):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r is None:
        if key in config.ASSUMPTIONS:
            return config.ASSUMPTIONS[key][0]
        return default
    return json.loads(r["value"])


def set_setting(con, key, value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))


def S(con, key):
    return get_setting(con, key)


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def jl(s, default=None):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except Exception:
        return default
