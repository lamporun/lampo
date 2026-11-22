#!/usr/bin/env python3
"""Records a REAL Lampo purchase for the demo video: 402 -> Lightning payment ->
streamed answer, with real timings. Writes cast.json for scripts/demo-render.py.

Runs on the server as root with a separate demo buyer wallet (never Lampo's).
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

URL = "https://lampo.run/v1/chat/completions"
MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
PROMPT = "Write a haiku about an AI agent paying for its own thoughts in bitcoin."
W = ["wavecli", "--network=mainnet", "--datadir=/root/demo-buyer/waved",
     "--rpcserver=127.0.0.1:10079", "--timeout=3m"]
OUT = sys.argv[1] if len(sys.argv) > 1 else "/root/demo-buyer/cast.json"

events, t0 = [], time.time()


def ev(kind, text):
    events.append({"t": round(time.time() - t0, 3), "k": kind, "s": text})


body = {"model": MODEL, "max_tokens": 400, "messages": [{"role": "user", "content": PROMPT}]}
short = '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"Write a haiku about an AI agent paying for its own thoughts in bitcoin."}]}'

# 1. Ask without paying: 402 with a Lightning invoice.
ev("cmd", f"curl -i https://lampo.run/v1/chat/completions -d '{short}'")
req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=60)
    sys.exit("expected a 402")
except urllib.error.HTTPError as e:
    l402 = [h for h in e.headers.get_all("WWW-Authenticate") or [] if h.startswith("L402 ")][0]
mac = l402.split('macaroon="', 1)[1].split('"', 1)[0]
inv = l402.split('invoice="', 1)[1].split('"', 1)[0]
ev("out", "HTTP/2 402 Payment Required")
ev("out", f'WWW-Authenticate: L402 macaroon="{mac[:12]}…", invoice="{inv[:18]}…"')
ev("note", "# 1,000 sats buys 1,000,000 tokens")

# 2. Pay the invoice with any Lightning wallet.
ev("cmd", f"wallet pay {inv[:18]}…")
pay = subprocess.run(W + ["--json", "send", inv, "--yes", "--max-credit", "5000", "--max-credit-topup", "5000"],
                     capture_output=True, text=True, timeout=240)
if pay.returncode != 0:
    sys.exit("payment failed: " + pay.stderr[-300:])
p = json.loads(pay.stdout)
pre = p.get("preimage") or next(v for k, v in p.items() if "preimage" in k)
amount = abs(int(p.get("amount_sat") or 1000))
ev("out", f"⚡ paid {int(amount):,} sats · preimage {pre[:12]}…")

# 3. Ask again with the proof of payment: the answer streams back.
ev("cmd", 'curl https://lampo.run/v1/chat/completions -H "Authorization: L402 $MACAROON:$PREIMAGE" -d \'{..., "stream": true}\'')
body.update({"stream": True, "stream_options": {"include_usage": True}})
req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json", "Authorization": f"L402 {mac}:{pre}"})
usage = None
with urllib.request.urlopen(req, timeout=180) as r:
    for raw in r:
        line = raw.decode().strip()
        if not line.startswith("data:") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[5:])
        if chunk.get("usage"):
            usage = chunk["usage"]
        for c in chunk.get("choices") or []:
            text = (c.get("delta") or {}).get("content")
            if text:
                ev("tok", text)
used = (usage or {}).get("total_tokens", 0)
ev("ok", f"✓ {used:,} tokens used · {1_000_000 - used:,} left in this bundle")
json.dump(events, open(OUT, "w"), ensure_ascii=False, indent=1)
print(f"recorded {len(events)} events in {events[-1]['t']:.1f} s; tokens used {used}")
