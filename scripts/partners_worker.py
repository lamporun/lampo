#!/usr/bin/env python3
"""Lampo partner program worker (lampo-partners-worker.timer, every 5 minutes).

1. Attribution: read the Caddy log of partner-link requests (no IPs, no request
   headers) and remember which bundle token each 402 challenge offered, with
   the partner code of the link it came through.
2. Commissions: when Lightmeter records one of those bundles as paid, credit
   the partner PARTNER_COMMISSION_PERCENT of its price. Lampo's own test
   purchases (internal-bundles.txt) are never credited.
3. Payouts: a partner owed at least PARTNER_PAYOUT_THRESHOLD_SAT is paid to
   their Lightning address from Lampo's wallet, at most once at a time and
   within a daily cap. A payout whose outcome is unknown stops further payouts
   to that partner until it is resolved by hand, so nobody is paid twice.

Every payout is also written to the accounting ledger.
"""
import csv
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

import partners_lib as lib

LOG_GLOB = os.environ.get("PARTNER_LOG_GLOB", "/var/log/caddy/lampo-partners*.log")
LIGHTMETER_DB = os.environ.get("LAMPO_LIGHTMETER_DB", "file:/var/lib/lampo/lightmeter/lightmeter.db?mode=ro")
INTERNAL = os.environ.get("LAMPO_INTERNAL_BUNDLES", "/var/lib/lampo/ledger/internal-bundles.txt")
LEDGER = os.environ.get("PARTNER_LEDGER", "/var/lib/lampo/ledger/partner-payouts.csv")
RATES = os.environ.get("LAMPO_RATES_CACHE", "/var/lib/lampo/ledger/rates.json")
WCLI = os.environ.get("LAMPO_WCLI", "/opt/lampo/bin/wcli")
ALERT = os.environ.get("LAMPO_ALERT", "/opt/lampo/bin/alert.sh")
COMMISSION = int(os.environ.get("PARTNER_COMMISSION_PERCENT", "10"))
THRESHOLD = max(1000, int(os.environ.get("PARTNER_PAYOUT_THRESHOLD_SAT", "1000")))  # Wavelength minimum
DAILY_CAP = int(os.environ.get("PARTNER_DAILY_PAYOUT_CAP_SAT", "20000"))
PAYOUTS = os.environ.get("PARTNER_PAYOUTS_ENABLED", "0") == "1"
KEEP_SAT = 2000  # Wavelength refuses a payment that leaves 1-999 sats of change
RETRY_AFTER_S = 6 * 3600
URI_CODE = re.compile(r"^/r/([a-z2-7]{8})/v1/")


def alert(msg):
    if os.path.exists(ALERT):
        subprocess.run([ALERT, msg], check=False, timeout=30)
    else:
        print("ALERT:", msg)


# 1. Attribution -------------------------------------------------------------

def attributions_from_line(line):
    """[(code, payment_hash, token_id)] from one Caddy JSON log line."""
    try:
        entry = json.loads(line)
    except ValueError:
        return []
    if entry.get("status") != 402:
        return []
    code = entry.get("partner_code") or ""
    if not lib.CODE_RE.match(code):
        m = URI_CODE.match(str(entry.get("request", {}).get("uri", "")))
        code = m.group(1) if m else ""
    if not code:
        return []
    headers = entry.get("resp_headers") or {}
    values = headers.get("Www-Authenticate") or headers.get("WWW-Authenticate") or []
    out = []
    for value in values if isinstance(values, list) else [values]:
        for mac in lib.MACAROON_RE.findall(str(value)):
            try:
                payment_hash, token_id = lib.macaroon_ids(mac)
            except (ValueError, IndexError):
                continue
            out.append((code, payment_hash, token_id))
    return out


def ingest(con, pattern=LOG_GLOB):
    """Read new lines of every partner log file; returns how many new
    attributions were stored. Offsets are kept per inode, so rotation loses
    nothing."""
    known = {r[0] for r in con.execute("SELECT code FROM partners WHERE status = 'active'")}
    added = 0
    for path in sorted(glob.glob(pattern)):
        try:
            st = os.stat(path)
        except OSError:
            continue
        inode = f"{st.st_dev}:{st.st_ino}"
        row = con.execute("SELECT offset FROM log_offsets WHERE inode = ?", (inode,)).fetchone()
        offset = row[0] if row else 0
        if offset > st.st_size:
            offset = 0
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        end = data.rfind(b"\n") + 1  # only whole lines
        for raw in data[:end].splitlines():
            for code, payment_hash, token_id in attributions_from_line(raw.decode("utf-8", "replace")):
                if code in known:
                    cur = con.execute(
                        "INSERT OR IGNORE INTO attributions (token_id, code, payment_hash, seen_at) VALUES (?, ?, ?, ?)",
                        (token_id, code, payment_hash, lib.now()))
                    added += cur.rowcount
        con.execute("INSERT OR REPLACE INTO log_offsets (inode, offset, updated_at) VALUES (?, ?, ?)",
                    (inode, offset + end, lib.now()))
    # Forget offsets of files rotated away long ago.
    con.execute("DELETE FROM log_offsets WHERE updated_at < ?", (lib.now() - 30 * 86400,))
    return added


# 2. Commissions -------------------------------------------------------------

def internal_ids(path=INTERNAL):
    try:
        with open(path) as f:
            return {l.split("#")[0].strip() for l in f if l.split("#")[0].strip()}
    except OSError:
        return set()


def credit(con, lightmeter=LIGHTMETER_DB, internal=None):
    """Credit commissions for attributed bundles Lightmeter records as paid."""
    internal = internal_ids() if internal is None else internal
    pending = {r[0]: r[1] for r in con.execute(
        "SELECT a.token_id, a.code FROM attributions a "
        "LEFT JOIN commissions c ON c.token_id = a.token_id WHERE c.token_id IS NULL")}
    if not pending:
        return []
    lm = sqlite3.connect(lightmeter, uri=lightmeter.startswith("file:"), timeout=15)
    try:
        rows = []
        ids = list(pending)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows += lm.execute(
                f"SELECT token_id, model, price_sats FROM bundles WHERE authorized = 1 AND token_id IN ({','.join('?' * len(chunk))})",
                chunk).fetchall()
    finally:
        lm.close()
    credited = []
    for token_id, model, price in rows:
        if token_id in internal:
            continue
        sats = int(price) * COMMISSION // 100
        cur = con.execute(
            "INSERT OR IGNORE INTO commissions (token_id, code, model, bundle_sats, commission_sats, credited_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (token_id, pending[token_id], model, int(price), sats, lib.now()))
        if cur.rowcount:
            credited.append((pending[token_id], model, sats))
    return credited


# 3. Payouts -----------------------------------------------------------------

def owed(con, code):
    earned = con.execute("SELECT COALESCE(SUM(commission_sats), 0) FROM commissions WHERE code = ?", (code,)).fetchone()[0]
    spent = con.execute("SELECT COALESCE(SUM(sats), 0) FROM payouts WHERE code = ? AND status IN ('paid', 'pending', 'unknown')",
                        (code,)).fetchone()[0]
    return earned - spent


def wallet_coins(run=subprocess.run):
    res = run([WCLI, "--json", "balance"], capture_output=True, text=True, timeout=120)
    return int(json.loads(res.stdout).get("confirmed_sat", 0))


def pay_invoice(pr, run=subprocess.run):
    """(status, fee_sats, detail): status is paid, failed or unknown."""
    try:
        res = run([WCLI, "--json", "send", pr, "--yes"], capture_output=True, text=True, timeout=400)
    except subprocess.TimeoutExpired:
        return "unknown", 0, "send timed out"
    try:
        out = json.loads(res.stdout or "{}")
    except ValueError:
        out = {}
    status = str(out.get("status", "")).upper()
    if status == "COMPLETE":
        return "paid", int(out.get("fee_sat") or 0), ""
    if status == "FAILED":
        return "failed", 0, str(out.get("failure_reason") or out)[:300]
    err = (res.stderr or "").strip()
    # An error before anything was dispatched (bad invoice, below minimum)
    # comes back as INVALID_ARGUMENT or FAILED_PRECONDITION.
    if res.returncode != 0 and re.search(r"INVALID_ARGUMENT|FAILED_PRECONDITION|NOT_FOUND", err):
        return "failed", 0, err[:300]
    return "unknown", 0, (err or json.dumps(out))[:300]


def eur_value(sats):
    try:
        with open(RATES) as f:
            return round(sats * float(json.load(f)["btc_eur"]) / 1e8, 4)
    except (OSError, ValueError, KeyError):
        return ""


def record_ledger(code, sats, fee, path=None):
    path = path or LEDGER
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date_utc", "partner_code", "payout_sats", "fee_sats", "eur_value_at_payout"])
        w.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), code, sats, fee, eur_value(sats + fee)])


def payout_one(con, code, address, amount, lnurl=lib.lnurl_pay_params, fetch=lib.fetch_json, run=subprocess.run):
    """Pay one partner. Returns the payout's final status."""
    cur = con.execute("INSERT INTO payouts (code, sats, status, created_at) VALUES (?, ?, 'pending', ?)",
                      (code, amount, lib.now()))
    pid = cur.lastrowid

    def finish(status, fee=0, detail="", payment_hash=""):
        con.execute("UPDATE payouts SET status = ?, fee_sats = ?, detail = ?, payment_hash = ? WHERE id = ?",
                    (status, fee, detail[:300], payment_hash, pid))
        return status

    try:
        params = lnurl(address)
        msat = min(amount * 1000, params["max_msat"])
        if msat < params["min_msat"] or msat < THRESHOLD * 1000:
            return finish("failed", detail="the address cannot receive this amount now")
        if msat != amount * 1000:
            con.execute("UPDATE payouts SET sats = ? WHERE id = ?", (msat // 1000, pid))
            amount = msat // 1000
        sep = "&" if "?" in params["callback"] else "?"
        res = fetch(f"{params['callback']}{sep}amount={msat}")
        pr = str(res.get("pr", "")) if isinstance(res, dict) else ""
        if lib.invoice_amount_msat(pr) != msat:
            return finish("failed", detail="the address returned an invoice for a different amount")
    except Exception as e:  # nothing was paid yet
        return finish("failed", detail=f"could not get an invoice: {e}")
    status, fee, detail = pay_invoice(pr, run=run)
    finish(status, fee, detail)
    if status == "paid":
        try:
            record_ledger(code, amount, fee)
        except OSError as e:  # the payout itself is recorded in the database
            alert(f"Partner {code}: pagamento riuscito ma registro contabile non scritto ({e}).")
    return status


def payouts(con, coins=None, lnurl=lib.lnurl_pay_params, fetch=lib.fetch_json, run=subprocess.run):
    """Pay every partner owed at least the threshold, within the daily cap."""
    if not PAYOUTS:
        return []
    done = []
    today = con.execute("SELECT COALESCE(SUM(sats), 0) FROM payouts WHERE created_at > ? AND status IN ('paid', 'pending', 'unknown')",
                        (lib.now() - 86400,)).fetchone()[0]
    coins = wallet_coins(run=run) if coins is None else coins
    for code, address in con.execute("SELECT code, ln_address FROM partners WHERE status = 'active'").fetchall():
        blocked = con.execute("SELECT COUNT(*) FROM payouts WHERE code = ? AND status IN ('pending', 'unknown')",
                              (code,)).fetchone()[0]
        # After a failed payout, wait a few hours before trying that partner
        # again, so a broken address does not fail every five minutes.
        recent_failure = con.execute("SELECT COUNT(*) FROM payouts WHERE code = ? AND status = 'failed' AND created_at > ?",
                                     (code, lib.now() - RETRY_AFTER_S)).fetchone()[0]
        amount = owed(con, code)
        if blocked or recent_failure or amount < THRESHOLD:
            continue
        if today + amount > DAILY_CAP:
            alert(f"Partner {code}: {amount} sats da pagare, ma il tetto giornaliero dei pagamenti ({DAILY_CAP}) è raggiunto.")
            continue
        if coins - amount - 20 < KEEP_SAT:
            alert(f"Partner {code}: {amount} sats da pagare, ma il wallet ha solo {coins} sats.")
            continue
        status = payout_one(con, code, address, amount, lnurl=lnurl, fetch=fetch, run=run)
        done.append((code, amount, status))
        if status in ("paid", "unknown"):
            today += amount
            coins -= amount
    return done


def main():
    con = lib.connect()
    try:
        added = ingest(con)
        credited = credit(con)
        paid = payouts(con)
    finally:
        con.close()
    for code, model, sats in credited:
        alert(f"Partner {code}: +{sats} sats di commissione (pacchetto {model}).")
    for code, amount, status in paid:
        if status == "paid":
            alert(f"Partner {code}: pagati {amount} sats.")
        elif status == "unknown":
            alert(f"Partner {code}: pagamento di {amount} sats con esito incerto. Pagamenti a questo partner sospesi: controllare a mano.")
        else:
            alert(f"Partner {code}: pagamento di {amount} sats non riuscito; si riprova al prossimo giro.")
    print(f"partners ok: {added} new attributions, {len(credited)} commissions, {len(paid)} payouts")


if __name__ == "__main__":
    sys.exit(main())
