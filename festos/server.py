"""Tiny dependency-free HTTP server: routing, JSON, static files, auth guard."""
import json
import mimetypes
import os
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import security
from .db import ROOT

WEB = os.path.join(ROOT, "web")
ROUTES = []
AFTER = []


class HTTPError(Exception):
    def __init__(self, status, message, extra=None):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra or {}


def route(method, pattern, auth=True, view=None):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, auth, view))
        return fn
    return deco


class Req:
    def __init__(self, handler, method, path, query, body, user):
        self.h, self.method, self.path, self.query, self.body, self.user = handler, method, path, query, body, user

    def q(self, k, default=None):
        v = self.query.get(k)
        return v[0] if v else default

    @property
    def role(self):
        return self.user["r"] if self.user else None

    @property
    def uid(self):
        return self.user["u"] if self.user else None


class Handler(BaseHTTPRequestHandler):
    server_version = "FestOS/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, payload, ctype="application/json; charset=utf-8", extra_headers=None):
        data = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'")
        if ctype.startswith("application/json"):
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not full.startswith(WEB) or not os.path.isfile(full):
            full = os.path.join(WEB, "index.html")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype, {"Cache-Control": "no-cache"})

    def _handle(self, method):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            if method != "GET":
                return self._send(405, {"error": "method not allowed"})
            return self._static(u.path)
        body = {}
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            if n > 5_000_000:
                return self._send(413, {"error": "payload too large"})
            raw = self.rfile.read(n)
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                return self._send(400, {"error": "invalid JSON"})
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        if not tok and "token" in parse_qs(u.query):
            tok = parse_qs(u.query)["token"][0]
        user = security.read_token(tok) if tok else None
        path = u.path[4:]
        for m, rx, fn, auth, view in ROUTES:
            if m != method:
                continue
            mt = rx.match(path)
            if not mt:
                continue
            if auth and not user:
                return self._send(401, {"error": "Please sign in."})
            if view and user and not security.can_view(user["r"], view):
                return self._send(403, {"error": f"Your role ({user['r']}) cannot open {view}.", "need_view": view})
            req = Req(self, method, path, parse_qs(u.query), body, user)
            try:
                res = fn(req, **mt.groupdict())
                if isinstance(res, tuple) and len(res) == 3 and isinstance(res[0], (bytes, str)):
                    data, ctype, fname = res
                    data = data.encode() if isinstance(data, str) else data
                    return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{fname}"'})
                return self._send(200, res)
            except HTTPError as e:
                return self._send(e.status, {"error": e.message, **e.extra})
            except Exception as e:
                traceback.print_exc()
                return self._send(500, {"error": f"Server error: {e}"})
        return self._send(404, {"error": "not found"})

    def do_GET(self):
        try:
            self._handle("GET")
        finally:
            for f in AFTER:
                f()

    def do_POST(self):
        try:
            self._handle("POST")
        finally:
            for f in AFTER:
                f()


def serve(host, port):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
