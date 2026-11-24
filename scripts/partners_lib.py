"""Lampo partner program: shared pieces for the API (partners_api.py) and the
worker (partners_worker.py).

A partner registers a Lightning address and gets a code. Buyers who use the
partner link https://lampo.run/r/<code>/v1 as their API base URL buy bundles
as usual; Caddy strips the code and logs the 402 challenges those requests
receive (no IP addresses, no request headers). The worker reads that log,
matches each challenge's macaroon to the bundle Lightmeter records once it is
paid, credits the partner a share of the price, and pays partners out to
their Lightning address once they are owed enough.

Lampo never holds anyone else's money: commissions come out of Lampo's own
revenue, and payouts are ordinary Lightning payments to the partner.
"""
import base64
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import time
import urllib.request

DB_PATH = os.environ.get("LAMPO_PARTNERS_DB", "/var/lib/lampo/partners/partners.db")
CODE_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
CODE_RE = re.compile(r"^[a-z2-7]{8}$")
LN_ADDRESS_RE = re.compile(r"^[a-z0-9._+-]{1,64}@([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS partners (
    code TEXT PRIMARY KEY,
    ln_address TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS attributions (
    token_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    payment_hash TEXT NOT NULL,
    seen_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS commissions (
    token_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    model TEXT NOT NULL,
    bundle_sats INTEGER NOT NULL,
    commission_sats INTEGER NOT NULL,
    credited_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    sats INTEGER NOT NULL,
    fee_sats INTEGER NOT NULL DEFAULT 0,
    payment_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS log_offsets (
    inode TEXT PRIMARY KEY,
    offset INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), mode=0o750, exist_ok=True)
    con = sqlite3.connect(path, timeout=15, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.executescript(SCHEMA)
    return con


def new_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def mask_address(addr):
    """al***@wallet.example: enough for a partner to recognize, little for others."""
    user, _, host = addr.partition("@")
    return f"{user[:2]}***@{host}"


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
    """(payment_hash_hex, token_id_hex) from an Aperture v2 macaroon, whose
    identifier is version (2 bytes) | payment hash (32) | token id (32)."""
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
    return ident[2:34].hex(), ident[34:66].hex()


MACAROON_RE = re.compile(r'L402\s+macaroon="([A-Za-z0-9+/=_-]+)"', re.I)

# msat per unit of each BOLT11 amount multiplier (1 BTC = 10^11 msat).
_MSAT_PER_UNIT = {"": 10**11, "m": 10**8, "u": 10**5, "n": 10**2}


def invoice_amount_msat(pr):
    """Amount of a mainnet BOLT11 invoice in msat from its human-readable part,
    or None for an amountless or unreadable one."""
    m = re.match(r"^lnbc(\d+)([munp]?)1[02-9ac-hj-np-z]+$", pr.lower())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    if unit == "p":
        return value // 10
    return value * _MSAT_PER_UNIT[unit]


def public_host(host):
    """True when every address the host resolves to is a public one, so a
    Lightning address cannot point the server at its own network."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch_json(url, timeout=10, max_bytes=65536):
    req = urllib.request.Request(url, headers={"User-Agent": "lampo-partners", "Accept": "application/json"})
    with _OPENER.open(req, timeout=timeout) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("response too large")
    return json.loads(data)


def lnurl_pay_params(address, resolve=public_host, fetch=fetch_json):
    """Resolve a Lightning address (LUD-16) to its LNURL-pay parameters.
    Raises ValueError with a message meant for the person registering."""
    address = address.strip().lower()
    if not LN_ADDRESS_RE.match(address):
        raise ValueError("that does not look like a Lightning address (name@domain)")
    user, _, host = address.partition("@")
    if not resolve(host):
        raise ValueError("the address's domain does not resolve to a public server")
    try:
        meta = fetch(f"https://{host}/.well-known/lnurlp/{user}")
    except Exception:
        raise ValueError("the address did not answer (LNURL-pay); check it in your wallet") from None
    if not isinstance(meta, dict) or meta.get("tag") != "payRequest":
        raise ValueError("the address did not return a Lightning payment request")
    callback = meta.get("callback")
    if not isinstance(callback, str) or not callback.startswith("https://"):
        raise ValueError("the address returned an unusable callback")
    try:
        lo, hi = int(meta["minSendable"]), int(meta["maxSendable"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("the address did not say how much it can receive") from None
    return {"address": address, "callback": callback, "min_msat": lo, "max_msat": hi}


def now():
    return int(time.time())
