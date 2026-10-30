#!/usr/bin/env python3
"""FestOS — run the control portal.

    python3 run.py            # builds the plan on first run, then serves http://localhost:8000
    python3 run.py --reset    # rebuild everything from the data pack
    python3 run.py --port 9000 --host 0.0.0.0

No third-party packages are required. If SciPy is installed, the accommodation
optimiser uses the HiGHS MILP solver; otherwise a deterministic greedy fallback.
"""
import argparse
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from festos import api  # noqa: F401  (registers routes)
from festos import accommodation, pipeline, server
server.AFTER.append(api.cleanup)
from festos.db import DB_PATH


def main():
    ap = argparse.ArgumentParser(description="FestOS control portal")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--reset", action="store_true", help="rebuild the plan from the data pack")
    ap.add_argument("--fast", action="store_true", help="shorter schedule search (for quick restarts)")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if sys.version_info < (3, 9):
        sys.exit("FestOS needs Python 3.9+")
    if a.reset or not os.path.exists(DB_PATH):
        print("Solver:", "HiGHS MILP (SciPy)" if accommodation.HAVE_SCIPY else "greedy fallback (install scipy for the MILP)")
        pipeline.build(schedule_seconds=8 if a.fast else None, frontier=False).close()
        api._frontier_async()          # cost/cohesion alternatives compute in the background
    httpd = server.serve(a.host, a.port)
    url = f"http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}"
    print(f"\n  FestOS is running → {url}\n  Demo password for every staff ID: aarohan2026  (e.g. ST08 = Festival Director)\n  Ctrl+C to stop.\n")
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
