#!/usr/bin/env python3
"""Live check of cached-token pricing: buys one DeepSeek V4 Flash bundle with the
demo wallet, sends the same long prompt twice, and reads /v1/balance after each
call. The second call should report cached tokens and debit far fewer bundle
tokens. Marks the bundle as internal (not a sale). Runs on the server as root.
"""
import base64
import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

URL = "https://lampo.run/v1"
MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
W = ["wavecli", "--network=mainnet", "--datadir=/root/demo-buyer/waved",
     "--rpcserver=127.0.0.1:10079", "--timeout=3m"]
t0 = time.time()
para = ("Lightning lets software pay software. An agent that can pay a Lightning invoice can buy "
        "compute, data or storage without an account, an API key or a card. ")
prompt = "Reference text, section {}:\n".format(1) + "\n".join(f"[{i}] {para}" for i in range(140)) + \
    "\n\nQuestion: in five words, what does this text say?"
body = {"model": MODEL, "max_tokens": 30, "messages": [{"role": "user", "content": prompt}]}


def post(auth=None):
    h = {"Content-Type": "application/json"}
    if auth:
        h["Authorization"] = auth
    req = urllib.request.Request(URL + "/chat/completions", data=json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.load(r), r.headers
    except urllib.error.HTTPError as e:
        return e.code, None, e.headers


def balance(auth):
    req = urllib.request.Request(URL + "/balance", headers={"Authorization": auth})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["remaining_tokens"]


import os
SAVED = "/root/demo-buyer/cache-test.cred"
if os.path.exists(SAVED) and "--new" not in sys.argv:
    auth = open(SAVED).read().strip()
    print("reusing the saved credential, remaining:", f"{balance(auth):,}")
    code = None
else:
    code, _, hdrs = post()
    assert code == 402, code
if code == 402:
    l402 = [h for h in hdrs.get_all("WWW-Authenticate") if h.startswith("L402 ")][0]
    mac = l402.split('macaroon="', 1)[1].split('"', 1)[0]
    inv = l402.split('invoice="', 1)[1].split('"', 1)[0]
    pay = subprocess.run(W + ["--json", "send", inv, "--yes", "--max-credit", "5000", "--max-credit-topup", "5000"],
                         capture_output=True, text=True, timeout=240)
    if pay.returncode != 0:
        sys.exit("payment failed: " + pay.stderr[-300:])
    p = json.loads(pay.stdout)
    pre = p.get("preimage") or next(v for k, v in p.items() if "preimage" in k)
    auth = f"L402 {mac}:{pre}"
with open("/root/demo-buyer/cache-test.cred", "w") as f:   # root-only folder: keep the paid credential
    f.write(auth + "\n")
print("bundle bought, remaining:", f"{balance(auth):,}")

results = []
for n in (1, 2):
    before = balance(auth)
    code, d, _ = post(auth)
    after = balance(auth)
    u = (d or {}).get("usage", {})
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    results.append((n, code, u.get("prompt_tokens"), cached, u.get("completion_tokens"), before - after))
    print(f"call {n}: HTTP {code} · prompt {u.get('prompt_tokens')} (cached {cached}) · "
          f"completion {u.get('completion_tokens')} · bundle debit {before - after:,} tokens")
    time.sleep(3)

# Mark this bundle as internal (a test, not a sale), by the token id inside the macaroon.
mac_b64 = auth.split(" ", 1)[1].split(":", 1)[0]
raw = base64.b64decode(mac_b64 + "=" * (-len(mac_b64) % 4))
ident = raw[raw.index(b"\x02B", 1) + 2:][:66]   # field 2, length 66 (0x42)
token_id = ident[34:66].hex()
with open("/var/lib/lampo/ledger/internal-bundles.txt", "a") as f:
    f.write(f"{token_id}  # cache test {time.strftime('%Y-%m-%d')}\n")
print("marked internal:", token_id[:12] + "…")
