#!/usr/bin/env python3
"""Lampo: public counter, live prices and the accounting ledger.

Runs every 10 minutes (lampo-stats.timer) as the lampo user:
- reads Lightmeter's database read-only and its config;
- computes bundle prices in sats exactly as Lightmeter does (docs/pricing.md);
- writes /srv/lampo/site/stats.json for the website;
- appends every newly paid bundle to /var/lib/lampo/ledger/ledger.csv with
  its euro value at the time it was recorded (the tax register).

With --daily-report it also sends a summary to Telegram.
"""
import csv
import datetime as dt
import json
import math
import os
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request

import yaml

CONFIG = "/etc/lampo/lightmeter.yaml"
DB = "/var/lib/lampo/lightmeter/lightmeter.db"
SITE_STATS = "/srv/lampo/site/stats.json"
LEDGER = "/var/lib/lampo/ledger/ledger.csv"
RATES_CACHE = "/var/lib/lampo/ledger/rates.json"

# Upstream slug -> (model id the pi402 buyer selects, display name).
META = {
    "deepseek-ai/DeepSeek-V4-Flash-0731": ("pi402/deepseek-v4-flash", "DeepSeek V4 Flash"),
    "zai-org/GLM-5.2": ("pi402/glm-5.2", "GLM 5.2"),
    "moonshotai/Kimi-K3.1-Pro": ("pi402/kimi-k3.1-pro", "Kimi K3.1 Pro"),
    "moonshotai/Kimi-K3": ("pi402/kimi-k3", "Kimi K3"),
    # Not in pi402 0.0.6: sold to plain L402 / MPP clients.
    "zai-org/GLM-5.3": (None, "GLM 5.3"),
    "zai-org/GLM-5.3-Flash": (None, "GLM 5.3 Flash"),
    "deepseek-ai/DeepSeek-V4-Pro-0813": (None, "DeepSeek V4 Pro"),
    "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B": (None, "NVIDIA Nemotron 3 Ultra"),
}


# From Baseten's /v1/models (checked 2026-11-23): context window and input types.
CAPS = {
    "deepseek-ai/DeepSeek-V4-Flash-0731": (1048576, ["text"]),
    "deepseek-ai/DeepSeek-V4-Pro-0813": (1048576, ["text"]),
    "zai-org/GLM-5.2": (1048576, ["text", "image"]),
    "zai-org/GLM-5.3": (1048576, ["text", "image"]),
    "zai-org/GLM-5.3-Flash": (1048576, ["text", "image"]),
    "moonshotai/Kimi-K3": (1048576, ["text", "image"]),
    "moonshotai/Kimi-K3.1-Pro": (1048576, ["text", "image"]),
    "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B": (202800, ["text"]),
}
SITE_MODELS = "/srv/lampo/site/models.json"


def write_models(models, btc_usd, updated):
    """/v1/models with live prices in sats, so agents can pick a model by cost."""
    data = []
    for m in models:
        ctx, inputs = CAPS.get(m["slug"], (None, ["text"]))
        data.append({
            "id": m["slug"], "object": "model", "owned_by": m["slug"].split("/")[0],
            "name": m["name"], "pi402_model": m["pi_model"], "context_length": ctx,
            "input_modalities": inputs, "features": ["streaming", "tools", "json_mode", "reasoning"],
            "pricing": {
                "input_msat_per_token": m["input_msat_per_token"],
                "output_msat_per_token": m["output_msat_per_token"],
                "bundle_tokens": m["bundle_tokens"], "bundle_sats": m["bundle_sats"],
                "bundle_usd_approx": round(m["bundle_sats"] * btc_usd / 1e8, 2),
            },
        })
    doc = {"object": "list", "data": data, "updated_utc": updated,
           "payment": "L402 or Payment (MPP) over Lightning: one payment buys a token bundle; "
                      "GET /v1/balance shows what is left. Docs: https://lampo.run/llms.txt"}
    tmp = SITE_MODELS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, SITE_MODELS)


def read_env(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return out


def spot(pair):
    url = f"https://api.coinbase.com/v2/prices/{pair}/spot"
    with urllib.request.urlopen(url, timeout=15) as r:
        return float(json.load(r)["data"]["amount"])


def rates():
    try:
        out = {"btc_usd": spot("BTC-USD"), "btc_eur": spot("BTC-EUR"),
               "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        with open(RATES_CACHE, "w") as f:
            json.dump(out, f)
        return out
    except Exception:
        with open(RATES_CACHE) as f:
            return json.load(f)


def msat_per_token(usd_per_mtok, margin, btc_usd):
    # Lightmeter: usd_per_mtok * 1e5 * (1 + margin/100) / btc_usd, rounded up,
    # and any positive price floors at 1 msat.
    if usd_per_mtok <= 0:
        return 0
    return max(1, math.ceil(usd_per_mtok * 1e5 * (1 + margin / 100) / btc_usd))


def prices(cfg, btc_usd):
    margin = float(cfg.get("pricing", {}).get("marginpercent", 0))
    default_bundle = int(cfg.get("bundletokens", 1_000_000))
    out = []
    for slug, m in (cfg.get("models") or {}).items():
        pin = msat_per_token(float(m["inputusdpermtok"]), margin, btc_usd)
        pout = msat_per_token(float(m["outputusdpermtok"]), margin, btc_usd)
        bundle = int(m.get("bundletokens", default_bundle))
        pi_id, name = META.get(slug, (slug, slug))
        out.append({
            "slug": slug, "pi_model": pi_id, "name": name,
            "input_msat_per_token": pin, "output_msat_per_token": pout,
            "bundle_tokens": bundle,
            "bundle_sats": math.ceil(bundle * (pin + pout) / 2 / 1000),
            "sats_per_million_tokens": math.ceil(1_000_000 * (pin + pout) / 2 / 1000),
        })
    return sorted(out, key=lambda p: p["sats_per_million_tokens"])


def to_epoch(v):
    v = int(v)
    if v > 10**17:
        return v / 1e9
    if v > 10**14:
        return v / 1e6
    if v > 10**11:
        return v / 1e3
    return float(v)


INTERNAL = "/var/lib/lampo/ledger/internal-bundles.txt"


def internal_ids():
    """Our own purchases after launch (demo video, checks): one token id per line.
    They stay in the tax ledger, flagged, but never count as sales on the site."""
    try:
        with open(INTERNAL) as f:
            return {l.split("#")[0].strip() for l in f if l.split("#")[0].strip()}
    except OSError:
        return set()


def db_rows():
    if not os.path.exists(DB):
        return [], []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        bundles = con.execute(
            "SELECT token_id, model, price_sats, created_at FROM bundles WHERE authorized = 1"
        ).fetchall()
        usage = con.execute("SELECT model, tokens, created_at, token_id FROM usage_events").fetchall()
    finally:
        con.close()
    return bundles, usage


def update_ledger(bundles, rate, launch_ts):
    seen = set()
    exists = os.path.exists(LEDGER)
    internal = internal_ids()
    if exists:
        with open(LEDGER, newline="") as f:
            rows = list(csv.DictReader(f))
        seen = {row["token_id"] for row in rows}
        # Flag rows added to the internal list after they were recorded.
        if any(r["token_id"] in internal and r["internal_test"] != "1" for r in rows):
            for r in rows:
                if r["token_id"] in internal:
                    r["internal_test"] = "1"
            tmp = LEDGER + ".tmp"
            with open(tmp, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            os.replace(tmp, LEDGER)
    new = [b for b in bundles if b[0] not in seen]
    if not new:
        return 0
    with open(LEDGER, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["token_id", "created_utc", "model", "price_sats", "btc_eur",
                        "eur_value", "recorded_utc", "internal_test"])
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for token_id, model, sats, created in sorted(new, key=lambda b: b[3]):
            ts = to_epoch(created)
            w.writerow([
                token_id,
                dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds"),
                model, sats, f"{rate['btc_eur']:.2f}",
                f"{sats / 1e8 * rate['btc_eur']:.4f}", now,
                "1" if ts < launch_ts or token_id in internal else "0",
            ])
    return len(new)


def summarize(bundles, usage, launch_ts, since=0.0):
    internal = internal_ids()
    paid = [b for b in bundles if to_epoch(b[3]) >= max(launch_ts, since) and b[0] not in internal]
    served = [u for u in usage if to_epoch(u[2]) >= max(launch_ts, since) and u[3] not in internal]
    by_model = {}
    for _, model, sats, _ in paid:
        d = by_model.setdefault(model, {"bundles": 0, "sats": 0})
        d["bundles"] += 1
        d["sats"] += int(sats)
    return {
        "paid_bundles": len(paid),
        "sats_earned": sum(int(b[2]) for b in paid),
        "tokens_served": sum(int(u[1]) for u in served),
        "by_model": by_model,
    }


def telegram(text):
    env = read_env("/etc/lampo/alert.env")
    token, chat = env.get("TELEGRAM_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(text)
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15)


def wallet_balance():
    try:
        out = subprocess.run(["/opt/lampo/bin/wcli", "--json", "balance"],
                             capture_output=True, text=True, timeout=60, check=True)
        b = json.loads(out.stdout)
        return int(b.get("confirmed_sat", 0) or 0) + int(b.get("credit_available_sat", 0) or 0)
    except Exception:
        return None


DEMAND = "/var/lib/lampo/ledger/demand.json"
HEALTH = "/var/lib/lampo/ledger/health.json"
SERVICES = ["waved", "lightmeterd", "aperture", "caddy"]


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def track_demand(launch_ts):
    """Counts 402 challenges that were not paid. Each 402 books an unpaid bundle
    in Lightmeter (one per payment door, so L402 and MPP share a timestamp);
    unpaid bookings expire after 10 minutes, and this runs every 10 minutes.
    Note: the directories' health probes (402 Index, Satring) also show up here."""
    st = load(DEMAND, {"seen": {}, "days": {}})
    rows = []
    if os.path.exists(DB):
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
        try:
            rows = con.execute("SELECT token_id, created_at FROM bundles WHERE authorized = 0").fetchall()
        finally:
            con.close()
    new_events = set()
    for token_id, created in rows:
        ts = to_epoch(created)
        if token_id not in st["seen"] and ts >= launch_ts:
            st["seen"][token_id] = ts
            new_events.add(int(ts))
    for ts in new_events:
        day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
        st["days"][day] = st["days"].get(day, 0) + 1
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - 2 * 86400
    st["seen"] = {k: v for k, v in st["seen"].items() if v >= cutoff}
    save(DEMAND, st)
    today = dt.datetime.now(dt.timezone.utc)
    last = [(today - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1)]
    return {"unpaid_challenges_today": sum(st["days"].get(d, 0) for d in last),
            "unpaid_challenges_total": sum(st["days"].values())}


def track_health():
    """Samples whether every service is up; returns the 30-day uptime percent."""
    ok = all(subprocess.run(["systemctl", "is-active", "--quiet", s]).returncode == 0 for s in SERVICES)
    st = load(HEALTH, {"days": {}})
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    d = st["days"].setdefault(day, [0, 0])
    d[0] += 1 if ok else 0
    d[1] += 1
    keep = sorted(st["days"])[-30:]
    st["days"] = {k: st["days"][k] for k in keep}
    save(HEALTH, st)
    up = sum(v[0] for v in st["days"].values())
    total = sum(v[1] for v in st["days"].values())
    return {"all_services_up": ok, "uptime_30d_percent": round(100 * up / total, 2) if total else None}


def main():
    with open(CONFIG) as f:
        cfg = yaml.safe_load(f)
    net = read_env("/etc/lampo/network.env")
    launch_ts = float(read_env("/etc/lampo/launch.env").get("LAUNCH_TS", "0") or 0)
    rate = rates()
    bundles, usage = db_rows()
    recorded = update_ledger(bundles, rate, launch_ts)
    now = dt.datetime.now(dt.timezone.utc)
    day_ago = now.timestamp() - 86400
    stats = {
        "updated_utc": now.isoformat(timespec="seconds"),
        "network": net.get("LAMPO_NETWORK", "unknown"),
        "btc_usd": round(rate["btc_usd"]),
        "margin_percent": cfg.get("pricing", {}).get("marginpercent"),
        "models": prices(cfg, rate["btc_usd"]),
        "totals": summarize(bundles, usage, launch_ts),
        "last_24h": summarize(bundles, usage, launch_ts, since=day_ago),
        "demand": track_demand(launch_ts),
        "health": track_health(),
    }
    tmp = SITE_STATS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(stats, f, indent=1)
    os.replace(tmp, SITE_STATS)
    write_models(stats["models"], stats["btc_usd"], stats["updated_utc"])
    print(f"stats ok: {stats['totals']['paid_bundles']} paid bundles, {recorded} new ledger rows")

    if "--daily-report" in sys.argv:
        t, d = stats["totals"], stats["last_24h"]
        bal = wallet_balance()
        lines = [
            f"Lampo - riepilogo ({stats['network']})",
            f"Ultime 24 ore: {d['paid_bundles']} pacchetti, {d['sats_earned']} sats, "
            f"{d['tokens_served']:,} token".replace(",", "."),
            f"Dal lancio: {t['paid_bundles']} pacchetti, {t['sats_earned']} sats",
            f"Saldo wallet: {bal} sats" if bal is not None else "Saldo wallet: non leggibile",
            f"Fatture chieste e non pagate oggi: {stats['demand']['unpaid_challenges_today']} "
            "(include i controlli automatici degli elenchi)",
            f"Servizi: {'tutti accesi' if stats['health']['all_services_up'] else 'QUALCUNO FERMO'}"
            f" · disponibilità 30 giorni: {stats['health']['uptime_30d_percent']}%",
        ]
        for model, v in sorted(d["by_model"].items()):
            lines.append(f"  {META.get(model, (model, model))[1]}: {v['bundles']} pacchetti")
        telegram("\n".join(lines))


if __name__ == "__main__":
    main()
