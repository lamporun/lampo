#!/usr/bin/env python3
"""Lampo: tells the operator on Telegram when someone replies to our launch posts.

Runs hourly (lampo-watch.timer) as the lampo user. The first run only records
the current state, so old activity never triggers an alert.
"""
import json
import subprocess
import urllib.request

STATE = "/var/lib/lampo/watch-state.json"
WATCH = [
    {"kind": "github", "repo": "lightninglabs/pi-402", "issue": 52},
    {"kind": "stacker", "item": 1619024},
]
UA = {"User-Agent": "lampo-watch", "Accept": "application/json"}


def get(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = dict(UA, **({"Content-Type": "application/json"} if data else {}))
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def alert(msg):
    subprocess.run(["/opt/lampo/bin/alert.sh", msg], check=False)


def github(w, st):
    key = f"gh:{w['repo']}#{w['issue']}"
    base = f"https://api.github.com/repos/{w['repo']}/issues/{w['issue']}"
    issue = get(base)
    comments = get(base + "/comments?per_page=100")
    prev = st.get(key)
    seen = set(prev["seen"]) if prev else set()
    if prev is not None:
        for c in comments:
            if c["id"] not in seen:
                body = " ".join(c["body"].split())[:300]
                alert(f"Nuovo commento su GitHub #{w['issue']} da {c['user']['login']}:\n{body}\n{c['html_url']}")
        if prev.get("state") != issue["state"]:
            alert(f"L'issue GitHub #{w['issue']} adesso è: {issue['state']}\n{issue['html_url']}")
    st[key] = {"seen": sorted(seen | {c["id"] for c in comments}), "state": issue["state"]}


def stacker(w, st):
    key = f"sn:{w['item']}"
    q = {"query": "{ item(id: %d) { ncomments comments { comments { id text user { name } } } } }" % w["item"]}
    item = get("https://stacker.news/api/graphql", q)["data"]["item"]
    tops = item["comments"]["comments"] or []
    prev = st.get(key)
    if prev is not None and item["ncomments"] > prev["n"]:
        seen = set(prev["seen"])
        fresh = [c for c in tops if c["id"] not in seen]
        lines = "\n".join(f"- {c['user']['name']}: {' '.join(c['text'].split())[:200]}" for c in fresh[:3])
        alert(f"{item['ncomments'] - prev['n']} nuovi commenti su Stacker News:\n"
              f"{lines or '(risposta dentro una discussione)'}\nhttps://stacker.news/items/{w['item']}")
    st[key] = {"n": item["ncomments"], "seen": [c["id"] for c in tops]}


def main():
    try:
        with open(STATE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    for w in WATCH:
        try:
            (github if w["kind"] == "github" else stacker)(w, st)
        except Exception as e:  # one failing source must not stop the others
            print(f"watch {w}: {type(e).__name__}: {e}")
    with open(STATE, "w") as f:
        json.dump(st, f)
    print("watch ok:", ", ".join(f"{k}={v.get('n', len(v.get('seen', [])))}" for k, v in st.items()))


if __name__ == "__main__":
    main()
