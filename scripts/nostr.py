#!/usr/bin/env python3
"""Tiny Nostr client for Lampo's account: sign (BIP-340) and publish events.

Standard library only. The private key is read from a file and never printed.

  nostr.py whoami  KEYFILE              npub of the key (checked against Lampo's)
  nostr.py profile HEX_PUBKEY           show a profile (kind 0) from a relay
  nostr.py post    KEYFILE EVENT.json   sign and publish {"kind","content","tags"}
  nostr.py naddr   HEX_PUBKEY KIND D    address of a long-form article
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import time
from urllib.parse import urlparse

LAMPO_HEX = "9788247054450961dcc8a7ad6bf17c39730d8a8aeda7b2cdeb1f60e43b6cdfdc"
RELAYS = ["wss://relay.damus.io", "wss://nos.lol", "wss://relay.primal.net", "wss://relay.nostr.band", "wss://purplepag.es", "wss://relay.snort.social"]

# --- secp256k1 / BIP-340 (reference algorithm) ---------------------------------
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    if a == b:
        lam = 3 * a[0] * a[0] * pow(2 * a[1], P - 2, P) % P
    else:
        lam = (b[1] - a[1]) * pow(b[0] - a[0], P - 2, P) % P
    x = (lam * lam - a[0] - b[0]) % P
    return (x, (lam * (a[0] - x) - a[1]) % P)


def _mul(pt, k):
    r = None
    while k:
        if k & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        k >>= 1
    return r


def _thash(tag, msg):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + msg).digest()


def _i(b):
    return int.from_bytes(b, "big")


def _b(x):
    return x.to_bytes(32, "big")


def _lift_x(x):
    c = (pow(x, 3, P) + 7) % P
    y = pow(c, (P + 1) // 4, P)
    if pow(y, 2, P) != c:
        return None
    return (x, y if y % 2 == 0 else P - y)


def pubkey(sk):
    return _b(_mul(G, _i(sk))[0])


def sign(msg, sk):
    d0 = _i(sk)
    if not 0 < d0 < N:
        raise ValueError("bad key")
    pt = _mul(G, d0)
    d = d0 if pt[1] % 2 == 0 else N - d0
    t = bytes(a ^ b for a, b in zip(_b(d), _thash("BIP0340/aux", os.urandom(32))))
    k0 = _i(_thash("BIP0340/nonce", t + _b(pt[0]) + msg)) % N
    r = _mul(G, k0)
    k = k0 if r[1] % 2 == 0 else N - k0
    e = _i(_thash("BIP0340/challenge", _b(r[0]) + _b(pt[0]) + msg)) % N
    return _b(r[0]) + _b((k + e * d) % N)


def verify(msg, pk, sig):
    pt = _lift_x(_i(pk))
    r, s = _i(sig[:32]), _i(sig[32:])
    if pt is None or r >= P or s >= N:
        return False
    e = _i(_thash("BIP0340/challenge", sig[:32] + pk + msg)) % N
    rr = _add(_mul(G, s), _mul(pt, N - e))
    return rr is not None and rr[1] % 2 == 0 and rr[0] == r


# --- bech32 (NIP-19) ---------------------------------------------------------------
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _hrp_expand(hrp):
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convert(data, frm, to, pad):
    acc = bits = 0
    out = []
    for v in data:
        acc = (acc << frm) | v
        bits += frm
        while bits >= to:
            bits -= to
            out.append((acc >> bits) & ((1 << to) - 1))
    if pad and bits:
        out.append((acc << (to - bits)) & ((1 << to) - 1))
    elif not pad and (bits >= frm or (acc << (to - bits)) & ((1 << to) - 1)):
        raise ValueError("bad padding")
    return out


def b32encode(hrp, data):
    d = _convert(data, 8, 5, True)
    chk = _polymod(_hrp_expand(hrp) + d + [0] * 6) ^ 1
    return hrp + "1" + "".join(CHARSET[x] for x in d + [(chk >> 5 * (5 - i)) & 31 for i in range(6)])


def b32decode(s):
    s = s.strip().lower()
    hrp, _, rest = s.rpartition("1")
    d = [CHARSET.index(c) for c in rest]
    if _polymod(_hrp_expand(hrp) + d) != 1:
        raise ValueError("bad checksum")
    return hrp, bytes(_convert(d[:-6], 5, 8, False))


def read_secret(path):
    raw = open(os.path.expanduser(path)).read().strip()
    tok = next((w for w in raw.replace('"', " ").split() if w.startswith("nsec1")), None)
    if tok:
        hrp, sk = b32decode(tok)
        assert hrp == "nsec" and len(sk) == 32
        return sk
    word = next((w for w in raw.split() if len(w) == 64), "")
    return bytes.fromhex(word)


# --- websocket (just enough for relays) --------------------------------------------
def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def ws_open(url):
    u = urlparse(url)
    s = ssl.create_default_context().wrap_socket(
        socket.create_connection((u.hostname, u.port or 443), timeout=15), server_hostname=u.hostname)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               "User-Agent: lampo-nostr/1\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        head += _recv_exact(s, 1)
    if b" 101 " not in head.split(b"\r\n")[0]:
        raise ConnectionError(head.split(b"\r\n")[0].decode(errors="replace"))
    return s


def ws_send(s, text, opcode=1):
    data = text.encode() if isinstance(text, str) else text
    n = len(data)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    mask = os.urandom(4)
    s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def ws_recv(s):
    msg = b""
    while True:
        b1, b2 = _recv_exact(s, 2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", _recv_exact(s, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _recv_exact(s, 8))[0]
        mask = _recv_exact(s, 4) if b2 & 0x80 else None
        data = _recv_exact(s, n)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        op = b1 & 0x0F
        if op == 9:
            ws_send(s, data, opcode=10)
            continue
        if op == 8:
            raise ConnectionError("closed by relay")
        msg += data
        if b1 & 0x80:
            return msg.decode(errors="replace")


# --- commands -------------------------------------------------------------------
def make_event(sk, kind, content, tags):
    pk = pubkey(sk).hex()
    created = int(time.time())
    ser = json.dumps([0, pk, created, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    eid = hashlib.sha256(ser.encode()).digest()
    sig = sign(eid, sk)
    assert verify(eid, bytes.fromhex(pk), sig), "signature self-check failed"
    return {"id": eid.hex(), "pubkey": pk, "created_at": created, "kind": kind,
            "tags": tags, "content": content, "sig": sig.hex()}


def publish(event):
    ok = 0
    for url in RELAYS:
        try:
            s = ws_open(url)
            ws_send(s, json.dumps(["EVENT", event], ensure_ascii=False))
            deadline = time.time() + 15
            while time.time() < deadline:
                m = json.loads(ws_recv(s))
                if m[0] == "OK" and m[1] == event["id"]:
                    print(f"  {url}: {'ok' if m[2] else 'rejected'} {m[3] if len(m) > 3 else ''}".rstrip())
                    ok += bool(m[2])
                    break
            s.close()
        except Exception as e:  # one bad relay must not stop the others
            print(f"  {url}: error {e}")
    return ok


def fetch_profile(hexpk):
    for url in RELAYS:
        try:
            s = ws_open(url)
            ws_send(s, json.dumps(["REQ", "p", {"kinds": [0], "authors": [hexpk], "limit": 1}]))
            while True:
                m = json.loads(ws_recv(s))
                if m[0] == "EVENT":
                    s.close()
                    return json.loads(m[2]["content"])
                if m[0] == "EOSE":
                    break
            s.close()
        except Exception:
            continue
    return None


def fetch_event(eid, relays=("wss://relay.primal.net", "wss://nos.lol", "wss://relay.snort.social", "wss://relay.damus.io",
                             "wss://nostr.wine", "wss://search.nos.today")):
    """The signed event with this id from the first relay that has it, or None."""
    for url in relays:
        try:
            s = ws_open(url)
            ws_send(s, json.dumps(["REQ", "e", {"ids": [eid]}]))
            m = json.loads(ws_recv(s))
            s.close()
            if m[0] == "EVENT" and m[2]["id"] == eid:
                return m[2]
        except Exception:
            continue
    return None


def thread_root(event):
    """NIP-10: the root of the thread an event belongs to (None if it starts one)."""
    es = [t for t in event.get("tags", []) if len(t) > 1 and t[0] == "e"]
    for t in es:
        if len(t) > 3 and t[3] == "root":
            return t[1]
    return es[0][1] if es else None


def reply_tags(parent):
    """Tags for a reply to parent, per NIP-10 (marked e tags plus the author)."""
    root = thread_root(parent)
    tags = [["e", root, "wss://relay.primal.net", "root"], ["e", parent["id"], "wss://relay.primal.net", "reply"]] \
        if root else [["e", parent["id"], "wss://relay.primal.net", "root"]]
    return tags + [["p", parent["pubkey"]]]


def fetch_mentions(hexpk, since, relays=("wss://relay.primal.net", "wss://nos.lol", "wss://relay.snort.social")):
    """Notes (kind 1) that tag hexpk, newer than since, from a few relays."""
    found = {}
    for url in relays:
        try:
            s = ws_open(url)
            ws_send(s, json.dumps(["REQ", "m", {"kinds": [1], "#p": [hexpk], "since": int(since), "limit": 50}]))
            deadline = time.time() + 15
            while time.time() < deadline:
                m = json.loads(ws_recv(s))
                if m[0] == "EVENT" and m[2]["pubkey"] != hexpk:
                    found[m[2]["id"]] = m[2]
                elif m[0] == "EOSE":
                    break
            s.close()
        except Exception:
            continue
    return sorted(found.values(), key=lambda e: e["created_at"])


def naddr(hexpk, kind, d):
    tlv = bytes([0, len(d.encode())]) + d.encode() + bytes([2, 32]) + bytes.fromhex(hexpk) + bytes([3, 4]) + struct.pack(">I", kind)
    return b32encode("naddr", tlv)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "whoami":
        pk = pubkey(read_secret(sys.argv[2])).hex()
        print(b32encode("npub", bytes.fromhex(pk)), "(Lampo)" if pk == LAMPO_HEX else "(NOT Lampo's key)")
    elif cmd == "profile":
        p = fetch_profile(sys.argv[2]) or {}
        print({k: p.get(k) for k in ("name", "display_name", "nip05", "about", "website")})
    elif cmd == "npub":
        print(b32encode("npub", bytes.fromhex(sys.argv[2])))
    elif cmd == "naddr":
        print(naddr(sys.argv[2], int(sys.argv[3]), sys.argv[4]))
    elif cmd == "post":
        sk = read_secret(sys.argv[2])
        if pubkey(sk).hex() != LAMPO_HEX:
            sys.exit("Refusing: this key is not Lampo's.")
        spec = json.load(open(sys.argv[3]))
        ev = make_event(sk, spec["kind"], spec["content"], spec.get("tags", []))
        print("event", ev["id"])
        n = publish(ev)
        print(f"accepted by {n}/{len(RELAYS)} relays")
        if spec["kind"] == 1:
            print("nevent/note:", b32encode("note", bytes.fromhex(ev["id"])))
        sys.exit(0 if n else 1)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
