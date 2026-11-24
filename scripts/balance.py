#!/usr/bin/env python3
"""Lampo bundle balance: GET /v1/balance with the same Authorization header used
for paid requests ("L402 <macaroon>:<preimage>", or LSAT) returns how many tokens
are left in that bundle. Free, read-only, no logs.

Ownership check: sha256(preimage) must equal the payment hash inside the macaroon
identifier (version | payment hash | token id). Reads the Lightmeter database
read-only. Listens on 127.0.0.1:10031, behind Caddy (lampo-balance.service).
"""
import base64
import hashlib
import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("LAMPO_BALANCE_DB", "file:/var/lib/lampo/lightmeter/lightmeter.db?mode=ro")
STATS = "/srv/lampo/site/stats.json"
AUTH = re.compile(r"^\s*(?:L402|LSAT)\s+([A-Za-z0-9+/=_-]+):([0-9a-fA-F]{64})\s*$", re.I)


def _varint(b, i):
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return n, i


def macaroon_ids(b64):
    """(payment_hash, token_id_hex) from a v2 binary macaroon."""
    b64 = b64.replace("-", "+").replace("_", "/")
    raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
    if not raw or raw[0] != 2:
        raise ValueError("not a v2 macaroon")
    i, fields = 1, {}
    while i < len(raw):
        t = raw[i]
        i += 1
        if t == 0:
            break
        n, i = _varint(raw, i)
        fields[t] = raw[i:i + n]
        i += n
    ident = fields.get(2, b"")
    if len(ident) != 66:
        raise ValueError("unexpected identifier")
    return ident[2:34], ident[34:66].hex()


def bundle_sizes():
    try:
        with open(STATS) as f:
            return {m["slug"]: m["bundle_tokens"] for m in json.load(f).get("models", [])}
    except (OSError, ValueError, KeyError):
        return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "lampo-balance"
    sys_version = ""

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.reply(204, {})

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/v1/balance":
            return self.reply(404, {"error": "not found"})
        m = AUTH.match(self.headers.get("Authorization", ""))
        if not m:
            return self.reply(401, {"error": "send the same header you use for paid requests: "
                                             "Authorization: L402 <macaroon>:<preimage>"})
        try:
            payment_hash, token_id = macaroon_ids(m.group(1))
        except Exception:
            return self.reply(400, {"error": "the macaroon could not be read"})
        if hashlib.sha256(bytes.fromhex(m.group(2))).digest() != payment_hash:
            return self.reply(403, {"error": "the preimage does not match this macaroon"})
        con = sqlite3.connect(DB, uri=True, timeout=5)
        try:
            row = con.execute("SELECT model, price_sats, remaining_tokens, authorized FROM bundles "
                              "WHERE token_id = ?", (token_id,)).fetchone()
        finally:
            con.close()
        if not row:
            return self.reply(404, {"error": "no bundle for this credential"})
        model, price, remaining, authorized = row
        # A matching preimage proves the invoice was paid. Lightmeter flags a
        # bundle as authorized only on its first paid request, so a bundle
        # paid but not used yet is reported too, with activated: false.
        return self.reply(200, {"model": model, "remaining_tokens": remaining,
                                "bundle_tokens": bundle_sizes().get(model), "price_sats": price,
                                "activated": bool(authorized)})

    def log_message(self, *args):  # no access log: we keep no IPs or requests
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("LAMPO_BALANCE_PORT", "10031"))), Handler).serve_forever()
