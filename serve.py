#!/usr/bin/env python3
"""Affswap production entrypoint — one web service, both apps.

Render (and any Python host) gives you ONE port. We have two stdlib servers
that share one SQLite database:

    * the member app   (radar.app._H)      -> served at the site root
    * the back office  (radar.admin._Handler)

They must run in the SAME process so they share the same SQLite file, so we:

    1. start each on an internal localhost port (auto-assigned),
    2. run a small reverse proxy on 0.0.0.0:$PORT that routes by HOSTNAME:
         - Host starts with "admin."  -> back office (HTTP Basic Auth), and
         - anything else              -> member app.

Routing by hostname (a vhost) rather than a URL path keeps BOTH apps living at
their own "/", so every root-relative link and form action keeps working with
no rewriting.

First boot restores a committed seed DB onto the (empty) persistent disk so the
live site has real data immediately.

Stdlib only — no pip, no build step.

Env vars:
    PORT             public port to bind (Render sets this; default 8080)
    ADMIN_USER       Basic-Auth user for the back office (default "admin")
    ADMIN_PASS       Basic-Auth password. If UNSET the back office is disabled
                     (returns 503) so it can never be exposed open.
    ADMIN_HOSTNAME   exact hostname for the back office (default: any host
                     beginning "admin.")
    AFFSWAP_DB_PATH  where the live SQLite DB lives (point at the disk)
"""
from __future__ import annotations

import base64
import hmac
import http.client
import os
import shutil
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from radar import config, db
from radar.app import _H as AppHandler
from radar.admin import _Handler as AdminHandler

ROOT = Path(__file__).resolve().parent
SEED_DB = ROOT / "seed" / "affiliateradar.db"

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")
ADMIN_HOSTNAME = os.environ.get("ADMIN_HOSTNAME", "").strip().lower()

# Headers that must not be blindly forwarded across a proxy hop.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


def ensure_db() -> None:
    """Make sure the live DB exists and its schema is current.

    On an empty persistent disk we restore the committed seed (real catalogue
    data); with no seed we create a fresh empty schema. Either way we then run
    the idempotent additive migrations.
    """
    if not config.DB_PATH.exists():
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if SEED_DB.exists():
            print(f"[seed] restoring {SEED_DB} -> {config.DB_PATH}")
            shutil.copy2(SEED_DB, config.DB_PATH)
        else:
            print("[seed] no seed found; creating empty schema")
            db.init_db()
    conn = db.connect()
    try:
        applied = db.migrate(conn)
        conn.commit()
        if applied:
            print(f"[db] migrations applied: {applied}")
    finally:
        conn.close()


def _start_backend(handler) -> int:
    """Run a handler on an internal localhost port in a daemon thread.

    Returns the auto-assigned port.
    """
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return port


class Proxy(BaseHTTPRequestHandler):
    server_version = "AffswapProxy"
    protocol_version = "HTTP/1.0"  # one response per connection: simplest framing

    # ports of the internal backends (filled in by main())
    app_port = 0
    admin_port = 0
    # the polished single-page app (the public "face"); loaded in main()
    index_html = b""

    # -- routing ----------------------------------------------------------- #
    def _is_admin_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        if ADMIN_HOSTNAME:
            return host == ADMIN_HOSTNAME
        return host.startswith("admin.")

    def _admin_authorised(self) -> bool:
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(hdr[6:]).decode("utf-8", "replace")
        except Exception:
            return False
        user, _, pw = raw.partition(":")
        # constant-time compare on both fields
        ok_user = hmac.compare_digest(user, ADMIN_USER)
        ok_pass = hmac.compare_digest(pw, ADMIN_PASS)
        return ok_user and ok_pass

    def _deny_admin(self) -> None:
        if not ADMIN_PASS:
            body = (b"Back office is disabled: set the ADMIN_PASS environment "
                    b"variable on the host to enable it.")
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        body = b"Authentication required."
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Affswap back office"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- proxy ------------------------------------------------------------- #
    def _dispatch(self) -> None:
        if self._is_admin_host():
            if not ADMIN_PASS or not self._admin_authorised():
                self._deny_admin()
                return
            self._proxy(self.admin_port)
            return
        # Member "face" = the polished single-page app (static, self-contained).
        # Its /api/* JSON stays wired to the DB-backed app for future use.
        path = self.path.split("?", 1)[0]
        if path == "/api" or path.startswith("/api/"):
            self._proxy(self.app_port)
        else:
            self._serve_index()

    def _serve_index(self) -> None:
        body = self.index_html
        if not body:  # static file missing -> fall back to the DB-backed app UI
            self._proxy(self.app_port)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _proxy(self, port: int) -> None:
        # read request body (if any)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        # forward headers, minus hop-by-hop
        fwd = {}
        for k in self.headers.keys():
            if k.lower() in _HOP_BY_HOP:
                continue
            fwd[k] = self.headers.get(k)
        fwd.setdefault("X-Forwarded-Host", self.headers.get("Host", ""))
        fwd["X-Forwarded-For"] = self.client_address[0]

        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
            conn.request(self.command, self.path, body=body, headers=fwd)
            resp = conn.getresponse()
            payload = resp.read()
        except Exception as exc:  # backend down / timeout
            conn = None
            msg = f"Upstream error: {exc}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(msg)
            return

        try:
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in _HOP_BY_HOP or k.lower() == "content-length":
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        finally:
            conn.close()

    # every method funnels through the same dispatcher
    do_GET = _dispatch
    do_POST = _dispatch
    do_HEAD = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch
    do_PATCH = _dispatch

    def log_message(self, fmt, *args):  # concise, single-line access log
        host = (self.headers.get("Host") or "-").split(":")[0]
        print(f'[proxy] {host} {self.command} {self.path} {fmt % args}')


def main() -> None:
    ensure_db()
    idx = ROOT / "index.html"
    Proxy.index_html = idx.read_bytes() if idx.exists() else b""
    Proxy.app_port = _start_backend(AppHandler)
    Proxy.admin_port = _start_backend(AdminHandler)

    port = int(os.environ.get("PORT", "8080"))
    front = ThreadingHTTPServer(("0.0.0.0", port), Proxy)
    admin_state = "ENABLED" if ADMIN_PASS else "DISABLED (set ADMIN_PASS)"
    admin_host = ADMIN_HOSTNAME or "admin.<your-domain>"
    face = f"polished SPA ({len(Proxy.index_html)//1024} KB)" if Proxy.index_html else f"DB-backed app UI (internal :{Proxy.app_port})"
    print("=" * 60)
    print(f"Affswap live on 0.0.0.0:{port}")
    print(f"  member face -> {face}")
    print(f"  member /api -> DB-backed app (internal :{Proxy.app_port})")
    print(f"  back office -> {admin_host}  (internal :{Proxy.admin_port}) [{admin_state}]")
    print(f"  database    -> {config.DB_PATH}")
    print("=" * 60)
    try:
        front.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
