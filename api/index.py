import os
import sys
import shutil
import tempfile
from urllib.parse import urlparse, parse_qs, urlencode

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# SQLite writable database handling for Vercel/serverless environments
ORIG_DB = os.path.join(ROOT_DIR, "festos.db")
# In Linux/Lambda serverless, /tmp is writable; on Windows local dev, use tempfile if needed
if os.name == "nt":
    TMP_DIR = os.path.join(tempfile.gettempdir(), "festos")
    os.makedirs(TMP_DIR, exist_ok=True)
    TMP_DB = os.path.join(TMP_DIR, "festos.db")
else:
    TMP_DB = "/tmp/festos.db"

if not os.path.exists(TMP_DB) and os.path.exists(ORIG_DB):
    try:
        shutil.copyfile(ORIG_DB, TMP_DB)
    except Exception:
        pass

ACTIVE_DB = TMP_DB if os.path.exists(TMP_DB) else ORIG_DB
os.environ["FESTOS_DB"] = ACTIVE_DB

from festos import db
db.DB_PATH = ACTIVE_DB

from festos import api  # noqa: F401  (registers routes)
from festos import server


class handler(server.Handler):
    def _resolve_target_path(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query, keep_blank_values=True)
        
        # 1. Check __route__ parameter passed via vercel.json rewrite
        if "__route__" in qs:
            route = qs.pop("__route__")[0]
            new_q = urlencode(qs, doseq=True)
            p = "/" + route.lstrip("/")
            return "/api" + p + ("?" + new_q if new_q else "")
            
        # 2. Check standard Vercel / proxy routing headers
        for h in [
            "x-invoke-path",
            "x-matched-path",
            "x-original-url",
            "x-forwarded-uri",
            "x-vercel-original-url",
        ]:
            val = self.headers.get(h)
            if val:
                up = urlparse(val)
                p = up.path
                if not p.startswith("/api/"):
                    p = "/api/" + p.lstrip("/")
                q = up.query or u.query
                return p + ("?" + q if q else "")
                
        # 3. Fallback: normalize self.path
        p = u.path
        if p == "/api/index.py" or p == "/api/index":
            # Default to /api/demo-accounts or /api/me if root invoked
            p = "/api/demo-accounts"
        elif not p.startswith("/api/"):
            p = "/api/" + p.lstrip("/")
            
        return p + ("?" + u.query if u.query else "")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        self.path = self._resolve_target_path()
        try:
            self._handle("GET")
        finally:
            for f in server.AFTER:
                f()

    def do_POST(self):
        self.path = self._resolve_target_path()
        try:
            self._handle("POST")
        finally:
            for f in server.AFTER:
                f()
