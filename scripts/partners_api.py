#!/usr/bin/env python3
"""Lampo partner program API, behind Caddy on 127.0.0.1:10032.

  POST /v1/partners          {"lightning_address": "name@domain"} -> the code
                             and the partner link (the same address gets the
                             same code again)
  GET  /v1/partners          the program's terms in numbers
  GET  /v1/partners/<code>   a partner's public stats

No accounts, no email: a partner is a Lightning address. Registrations are
rate limited per client and per day.
"""
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import partners_lib as lib

PORT = int(os.environ.get("LAMPO_PARTNERS_PORT", "10032"))
BASE = os.environ.get("LAMPO_PUBLIC_URL", "https://lampo.run")
COMMISSION = int(os.environ.get("PARTNER_COMMISSION_PERCENT", "10"))
THRESHOLD = int(os.environ.get("PARTNER_PAYOUT_THRESHOLD_SAT", "1000"))
PER_CLIENT_PER_HOUR = int(os.environ.get("PARTNER_SIGNUPS_PER_CLIENT_HOUR", "5"))
PER_DAY = int(os.environ.get("PARTNER_SIGNUPS_PER_DAY", "300"))

_lock = threading.Lock()
_recent = {}  # client -> [timestamps]
_daily = []


def allow_signup(client, t=None):
    """Sliding-window rate limits: per client per hour, and overall per day."""
    t = t or time.time()
    with _lock:
        _daily[:] = [x for x in _daily if t - x < 86400]
        hits = [x for x in _recent.get(client, []) if t - x < 3600]
        if len(hits) >= PER_CLIENT_PER_HOUR or len(_daily) >= PER_DAY:
            return False
        hits.append(t)
        _recent[client] = hits
        _daily.append(t)
        if len(_recent) > 10000:
            _recent.clear()
        return True


def program():
    return {
        "commission_percent": COMMISSION,
        "payout_threshold_sats": THRESHOLD,
        "duration": "every bundle bought through the partner link, with no end date",
        "how": f"use {BASE}/r/<code>/v1 as the OpenAI base URL (pi402: L402_PROXY_URL)",
        "terms": f"{BASE}/partners.html#terms",
    }


def partner_view(con, code):
    row = con.execute("SELECT ln_address, created_at, status FROM partners WHERE code = ?", (code,)).fetchone()
    if not row:
        return None
    addr, created, status = row
    bundles, earned = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(commission_sats), 0) FROM commissions WHERE code = ?", (code,)).fetchone()
    paid, last = con.execute(
        "SELECT COALESCE(SUM(sats), 0), MAX(created_at) FROM payouts WHERE code = ? AND status = 'paid'", (code,)).fetchone()
    held = con.execute(
        "SELECT COALESCE(SUM(sats), 0) FROM payouts WHERE code = ? AND status IN ('pending', 'unknown')", (code,)).fetchone()[0]
    return {
        "code": code,
        "status": status,
        "lightning_address": lib.mask_address(addr),
        "link": f"{BASE}/r/{code}/v1",
        "dashboard": f"{BASE}/r/{code}",
        "created_at": created,
        "referred_bundles": bundles,
        "earned_sats": earned,
        "paid_sats": paid,
        "in_payment_sats": held,
        "owed_sats": earned - paid - held,
        "last_payout_at": last,
        **program(),
    }


def register(con, address, resolve=lib.public_host, fetch=lib.fetch_json):
    """Returns (status, body). The same address always maps to one code."""
    params = lib.lnurl_pay_params(address, resolve=resolve, fetch=fetch)
    if params["min_msat"] > THRESHOLD * 1000:
        raise ValueError(f"that address cannot receive payments as small as {THRESHOLD} sats")
    if params["max_msat"] < THRESHOLD * 1000:
        raise ValueError(f"that address cannot receive {THRESHOLD} sats")
    row = con.execute("SELECT code FROM partners WHERE ln_address = ?", (params["address"],)).fetchone()
    if row:
        return 200, partner_view(con, row[0])
    for _ in range(5):
        code = lib.new_code()
        try:
            con.execute("INSERT INTO partners (code, ln_address, created_at) VALUES (?, ?, ?)",
                        (code, params["address"], lib.now()))
            return 201, partner_view(con, code)
        except sqlite3.IntegrityError:
            row = con.execute("SELECT code FROM partners WHERE ln_address = ?", (params["address"],)).fetchone()
            if row:  # a concurrent registration of the same address won
                return 200, partner_view(con, row[0])
    raise RuntimeError("could not allocate a code")


class Handler(BaseHTTPRequestHandler):
    server_version = "lampo-partners"
    sys_version = ""

    def log_message(self, *args):  # no request logs: they would hold IP addresses
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def client(self):
        # Caddy sets X-Forwarded-For; only trust it from Caddy on localhost.
        if self.client_address[0] in ("127.0.0.1", "::1"):
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0]

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path == "/v1/partners":
            return self.reply(200, program())
        code = path.rsplit("/", 1)[-1]
        if path.startswith("/v1/partners/") and lib.CODE_RE.match(code):
            con = lib.connect()
            try:
                view = partner_view(con, code)
            finally:
                con.close()
            return self.reply(200, view) if view else self.reply(404, {"error": "no partner with that code"})
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != "/v1/partners":
            return self.reply(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > 2048:
            return self.reply(400, {"error": "send a small JSON body: {\"lightning_address\": \"name@domain\"}"})
        try:
            body = json.loads(self.rfile.read(length))
            address = str(body.get("lightning_address", ""))
        except (ValueError, AttributeError):
            return self.reply(400, {"error": "the body is not valid JSON"})
        if not allow_signup(self.client()):
            return self.reply(429, {"error": "too many registrations; try again later"})
        con = lib.connect()
        try:
            status, view = register(con, address)
            return self.reply(status, view)
        except ValueError as e:
            return self.reply(400, {"error": str(e)})
        finally:
            con.close()


def main():
    lib.connect().close()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
