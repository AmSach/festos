import os
import sys
import shutil

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Ensure database is accessible and writable in Vercel /tmp environment
TMP_DB = "/tmp/festos.db"
ORIG_DB = os.path.join(ROOT_DIR, "festos.db")
if not os.path.exists(TMP_DB) and os.path.exists(ORIG_DB):
    try:
        shutil.copyfile(ORIG_DB, TMP_DB)
    except Exception:
        pass

if os.path.exists(TMP_DB):
    os.environ["FESTOS_DB"] = TMP_DB
elif os.path.exists(ORIG_DB):
    os.environ["FESTOS_DB"] = ORIG_DB

from festos import api  # noqa: F401  (registers routes)
from festos import server

class handler(server.Handler):
    pass
