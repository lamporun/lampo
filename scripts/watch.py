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
    {"kind": "github", "repo": "lightninglabs/pi-402", "issue": 53},        # PR: GLM list
    {"kind": "github", "repo": "lightninglabs/pi-402-site", "issue": 5},    # PR: model command
    {"kind": "github", "repo": "lightninglabs/pi-402-site", "issue": 6},    # PR: list Lampo
    {"kind": "github", "repo": "lightninglabs/aperture", "issue": 344},     # issue: pricing
    {"kind": "github", "repo": "Fewsats/awesome-L402", "issue": 39},        # PR: list Lampo
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
    """Issue or pull request: new comments, new reviews, and state changes."""
    key = f"gh:{w['repo']}#{w['issue']}"
    name = f"{w['repo'].split('/')[1]}#{w['issue']}"
    base = f"https://api.github.com/repos/{w['repo']}/issues/{w['issue']}"
    issue = get(base)
    events = [(c["id"], c["user"]["login"], c["body"] or "", c["html_url"], "commento")
              for c in get(base + "/comments?per_page=100")]
    pr = issue.get("pull_request")
    if pr:
        rbase = f"https://api.github.com/repos/{w['repo']}/pulls/{w['issue']}"
        events += [(r["id"], r["user"]["login"], r["body"] or "", r["html_url"], f"revisione ({r['state']})")
                   for r in get(rbase + "/reviews?per_page=100")]
        events += [(c["id"], c["user"]["login"], c["body"] or "", c["html_url"], "commento sul codice")
                   for c in get(rbase + "/comments?per_page=100")]
    state = "unito" if pr and pr.get("merged_at") else ("aperto" if issue["state"] == "open" else "chiuso")
    prev = st.get(key)
    seen = set(prev["seen"]) if prev else set()
    if prev is not None:
        for eid, who, body, url, what in events:
            if eid not in seen:
                text = " ".join(body.split())[:300]
                alert(f"GitHub {name}: nuovo {what} da {who}:\n{text}\n{url}")
        if prev.get("state") not in (None, state, issue["state"]):
            alert(f"GitHub {name} adesso è: {state}\n{issue['html_url']}")
    st[key] = {"seen": sorted(seen | {e[0] for e in events}), "state": state}


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
