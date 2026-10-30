"""Authentication, role-based access, field masking and a tamper-evident audit log."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from . import config
from .db import ROOT

SECRET_PATH = os.path.join(ROOT, ".festos_secret")


def _secret():
    if not os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "wb") as fh:
            fh.write(secrets.token_bytes(32))
        try:
            os.chmod(SECRET_PATH, 0o600)
        except OSError:
            pass
    with open(SECRET_PATH, "rb") as fh:
        return fh.read()


SECRET = None


def secret():
    global SECRET
    if SECRET is None:
        SECRET = _secret()
    return SECRET


# ---------------------------------------------------------------- passwords
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()
    return salt, h


def check_pw(pw, salt, h):
    return hmac.compare_digest(hash_pw(pw, salt)[1], h)


def seed_users(con, rows):
    salt, h = hash_pw(config.DEMO_PASSWORD)     # one hash, reused: demo credentials only
    for r in rows:
        rk = config.ROLE_KEYS.get(r["role"], "volunteer")
        con.execute("INSERT OR REPLACE INTO users(uid,name,role,role_key,shift,phone,salt,pw_hash) VALUES(?,?,?,?,?,?,?,?)",
                    (r["member_id"], r["name"], r["role"], rk, r["shift"], r["phone"], salt, h))


# ---------------------------------------------------------------- tokens
def make_token(uid, role_key, ttl=12 * 3600):
    body = json.dumps({"u": uid, "r": role_key, "e": int(time.time()) + ttl, "n": secrets.token_hex(4)}).encode()
    b = base64.urlsafe_b64encode(body).decode().rstrip("=")
    sig = hmac.new(secret(), b.encode(), hashlib.sha256).hexdigest()
    return f"{b}.{sig}"


def read_token(tok):
    try:
        b, sig = tok.split(".")
        if not hmac.compare_digest(hmac.new(secret(), b.encode(), hashlib.sha256).hexdigest(), sig):
            return None
        body = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
        if body["e"] < time.time():
            return None
        return body
    except Exception:
        return None


# ---------------------------------------------------------------- login throttling
_FAILS = {}


def throttled(key):
    n, t = _FAILS.get(key, (0, 0))
    return n >= 5 and time.time() - t < 60


def note_fail(key):
    n, t = _FAILS.get(key, (0, 0))
    if time.time() - t > 60:
        n = 0
    _FAILS[key] = (n + 1, time.time())


def clear_fail(key):
    _FAILS.pop(key, None)


# ---------------------------------------------------------------- RBAC
def can_view(role, module):
    p = config.PERMISSIONS.get(role, {})
    return p.get("view") == "*" or module in p.get("view", [])


def can_write(role, action):
    p = config.PERMISSIONS.get(role, {})
    return p.get("write") == "*" or action in p.get("write", [])


def field_ok(role, field):
    return role in config.FIELD_ACCESS.get(field, [])


def mask_phone(s):
    if not s:
        return s
    d = "".join(ch for ch in s if ch.isdigit())
    return "•••• ••" + d[-4:] if len(d) >= 4 else "••••"


def mask_person(p, role, reveal=False):
    """Data minimisation: return only what this role needs."""
    q = dict(p)
    if not (field_ok(role, "contact") or reveal):
        q["contact"] = mask_phone(q.get("contact") or q.get("contact_raw"))
        q["contact_raw"] = None
        q["contact_masked"] = True
    if not field_ok(role, "accessibility"):
        has = q.get("access_need") not in (None, "NONE")
        q["access_raw"] = "support needed" if has else None
        q["access_need"] = "SUPPORT" if has else "NONE"
        q["access_masked"] = True
    if not field_ok(role, "dietary"):
        q["diet_raw"] = None
        q["diet"] = None
        q["diet_masked"] = True
    if not field_ok(role, "age"):
        q["age"] = None
        q["age_raw"] = None
    if not field_ok(role, "gender"):
        q["gender_raw"] = None
        q["housing_basis"] = None
        q["gender_masked"] = True
    return q


# ---------------------------------------------------------------- audit (hash chain)
def audit(con, actor, role, action, entity=None, entity_id=None, detail=None, reason=None, ts=None, off_shift=0):
    prev = con.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    prev_h = prev[0] if prev else "GENESIS"
    d = json.dumps(_redact(detail or {}), sort_keys=True, default=str)
    payload = "|".join(str(x) for x in (ts, actor, role, action, entity, entity_id, d, reason, prev_h))
    h = hashlib.sha256(payload.encode()).hexdigest()
    con.execute("INSERT INTO audit(ts,actor,role,action,entity,entity_id,detail,reason,off_shift,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ts, actor, role, action, entity, entity_id, d, reason, off_shift, prev_h, h))
    return h


SENSITIVE_KEYS = {"contact", "contact_raw", "phone", "password", "access_raw", "diet_raw"}


def _redact(d):
    if isinstance(d, dict):
        return {k: ("[redacted]" if k in SENSITIVE_KEYS else _redact(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [_redact(x) for x in d]
    return d


def verify_chain(con):
    prev_h = "GENESIS"
    n = 0
    for r in con.execute("SELECT * FROM audit ORDER BY seq"):
        payload = "|".join(str(x) for x in (r["ts"], r["actor"], r["role"], r["action"], r["entity"], r["entity_id"], r["detail"], r["reason"], prev_h))
        h = hashlib.sha256(payload.encode()).hexdigest()
        if r["prev_hash"] != prev_h or r["hash"] != h:
            return {"ok": False, "broken_at": r["seq"], "checked": n}
        prev_h = h
        n += 1
    return {"ok": True, "checked": n, "head": prev_h[:16]}


def on_shift(shift, clock):
    try:
        a, b = shift.split("-")
        t = clock[11:16]
        return (a <= t < b) if a < b else (t >= a or t < b)
    except Exception:
        return True
