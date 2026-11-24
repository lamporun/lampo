"""Tests for the partner program: python3 -m unittest discover -s tests"""
import base64
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ["PARTNER_PAYOUTS_ENABLED"] = "1"
os.environ["PARTNER_DAILY_PAYOUT_CAP_SAT"] = "5000"

import partners_api as api  # noqa: E402
import partners_lib as lib  # noqa: E402
import partners_worker as worker  # noqa: E402


def macaroon(payment_hash, token_id):
    """A v2 binary macaroon with Aperture's identifier layout."""
    ident = b"\x00\x00" + bytes.fromhex(payment_hash) + bytes.fromhex(token_id)
    loc = b"lsat"
    raw = b"\x02" + b"\x01" + bytes([len(loc)]) + loc + b"\x02" + bytes([len(ident)]) + ident + b"\x00" + b"\x06\x20" + b"s" * 32
    return base64.b64encode(raw).decode()


def log_line(code, payment_hash, token_id, status=402, uri=None, appended=True):
    entry = {
        "logger": "http.log.access.partners",
        "request": {"uri": uri or f"/r/{code}/v1/chat/completions", "method": "POST"},
        "status": status,
        "resp_headers": {"Www-Authenticate": [f'L402 macaroon="{macaroon(payment_hash, token_id)}", invoice="lnbc10u1xyz"', 'Payment id="x"']},
    }
    if appended:
        entry["partner_code"] = code
    return json.dumps(entry)


GOOD_META = {"tag": "payRequest", "callback": "https://wallet.example/cb", "minSendable": 1000, "maxSendable": 10**11}


class Env(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.con = lib.connect(os.path.join(self.dir, "partners.db"))
        self.lm_path = os.path.join(self.dir, "lightmeter.db")
        lm = sqlite3.connect(self.lm_path)
        lm.execute("CREATE TABLE bundles (token_id TEXT, model TEXT, price_sats INTEGER, remaining_tokens INTEGER, created_at INTEGER, authorized INTEGER)")
        lm.commit()
        lm.close()
        self.log = os.path.join(self.dir, "lampo-partners.log")
        worker.LEDGER = os.path.join(self.dir, "ledger.csv")
        api._recent.clear()
        api._daily.clear()

    def register(self, address="alice@wallet.example", meta=GOOD_META):
        return api.register(self.con, address, resolve=lambda h: True, fetch=lambda url: meta)

    def bundle(self, token_id, price=1000, model="deepseek-ai/DeepSeek-V4-Flash-0731", paid=True):
        lm = sqlite3.connect(self.lm_path)
        lm.execute("INSERT INTO bundles VALUES (?, ?, ?, 0, 0, ?)", (token_id, model, price, 1 if paid else 0))
        lm.commit()
        lm.close()

    def write_log(self, *lines):
        with open(self.log, "a") as f:
            for line in lines:
                f.write(line + "\n")


class Registration(Env):
    def test_register_returns_code_and_link_and_is_idempotent(self):
        status, view = self.register()
        self.assertEqual(status, 201)
        self.assertRegex(view["code"], r"^[a-z2-7]{8}$")
        self.assertEqual(view["link"], f"https://lampo.run/r/{view['code']}/v1")
        self.assertEqual(view["lightning_address"], "al***@wallet.example")
        self.assertEqual(view["commission_percent"], 10)
        status2, view2 = self.register("ALICE@wallet.example ")
        self.assertEqual((status2, view2["code"]), (200, view["code"]))

    def test_bad_addresses_are_refused_with_a_reason(self):
        cases = [
            ("not-an-address", "does not look like"),
            ("bob@localhost", "does not look like"),
        ]
        for address, reason in cases:
            with self.assertRaisesRegex(ValueError, reason):
                self.register(address)
        with self.assertRaisesRegex(ValueError, "public server"):
            api.register(self.con, "bob@internal.example", resolve=lambda h: False, fetch=lambda u: GOOD_META)
        with self.assertRaisesRegex(ValueError, "did not return a Lightning payment request"):
            self.register(meta={"tag": "withdrawRequest"})
        with self.assertRaisesRegex(ValueError, "as small as"):
            self.register(meta={**GOOD_META, "minSendable": 5_000_000})

    def test_signup_rate_limit(self):
        for _ in range(api.PER_CLIENT_PER_HOUR):
            self.assertTrue(api.allow_signup("1.2.3.4", t=1000))
        self.assertFalse(api.allow_signup("1.2.3.4", t=1001))
        self.assertTrue(api.allow_signup("5.6.7.8", t=1001))
        self.assertTrue(api.allow_signup("1.2.3.4", t=1000 + 3601))


class AttributionAndCommissions(Env):
    def test_challenge_through_partner_link_is_credited_when_paid(self):
        _, view = self.register()
        code = view["code"]
        ph, tok = "ab" * 32, "cd" * 32
        self.write_log(log_line(code, ph, tok))
        self.assertEqual(worker.ingest(self.con, self.log), 1)
        # Not paid yet: nothing to credit.
        self.assertEqual(worker.credit(self.con, self.lm_path, internal=set()), [])
        self.bundle(tok, price=1000)
        self.assertEqual(worker.credit(self.con, self.lm_path, internal=set()), [(code, "deepseek-ai/DeepSeek-V4-Flash-0731", 100)])
        # Credited once only.
        self.assertEqual(worker.credit(self.con, self.lm_path, internal=set()), [])
        self.assertEqual(api.partner_view(self.con, code)["owed_sats"], 100)

    def test_log_is_read_incrementally_and_survives_rotation(self):
        _, view = self.register()
        code = view["code"]
        self.write_log(log_line(code, "01" * 32, "11" * 32))
        self.assertEqual(worker.ingest(self.con, self.log), 1)
        self.assertEqual(worker.ingest(self.con, self.log), 0)
        # A half-written line is left for the next run.
        with open(self.log, "a") as f:
            f.write(log_line(code, "02" * 32, "22" * 32)[:40])
        self.assertEqual(worker.ingest(self.con, self.log), 0)
        with open(self.log, "a") as f:
            f.write(log_line(code, "02" * 32, "22" * 32)[40:] + "\n")
        self.assertEqual(worker.ingest(self.con, self.log), 1)
        # Rotation: the old file is renamed, a new one starts.
        os.rename(self.log, self.log.replace(".log", "-2026.log"))
        self.write_log(log_line(code, "03" * 32, "33" * 32))
        self.assertEqual(worker.ingest(self.con, os.path.join(self.dir, "lampo-partners*.log")), 1)

    def test_what_is_never_credited(self):
        _, view = self.register()
        code = view["code"]
        self.write_log(
            log_line("zzzzzzzz", "01" * 32, "11" * 32),           # unknown partner code
            log_line(code, "02" * 32, "22" * 32, status=200),       # not a challenge
            log_line(code, "03" * 32, "33" * 32),                   # Lampo's own test purchase
            log_line(code, "04" * 32, "44" * 32, uri="/r/x/v1", appended=False),  # malformed
            "not json",
        )
        worker.ingest(self.con, self.log)
        for tok in ("11", "22", "33", "44"):
            self.bundle(tok * 32)
        self.assertEqual(worker.credit(self.con, self.lm_path, internal={"33" * 32}), [])

    def test_uri_is_a_fallback_for_the_partner_code(self):
        _, view = self.register()
        self.write_log(log_line(view["code"], "05" * 32, "55" * 32, appended=False))
        self.assertEqual(worker.ingest(self.con, self.log), 1)


class Payouts(Env):
    def setUp(self):
        super().setUp()
        _, view = self.register()
        self.code = view["code"]
        self.calls = []
        self.sold = 0

    def earn(self, n, price=1000):
        for _ in range(n):
            self.sold += 1
            tok = f"{self.sold:064x}"
            self.write_log(log_line(self.code, f"{self.sold + 100000:064x}", tok))
            self.bundle(tok, price=price)
        worker.ingest(self.con, self.log)
        worker.credit(self.con, self.lm_path, internal=set())

    def run_fake(self, status="COMPLETE", fee=3):
        def run(cmd, **kw):
            self.calls.append(cmd)
            if "balance" in cmd:
                return SimpleNamespace(stdout=json.dumps({"confirmed_sat": 50000}), stderr="", returncode=0)
            if status == "TIMEOUT":
                raise worker.subprocess.TimeoutExpired(cmd, 1)
            return SimpleNamespace(stdout=json.dumps({"status": status, "fee_sat": fee}), stderr="", returncode=0)
        return run

    def fetch_invoice(self, amount_sats=None):
        def fetch(url):
            msat = int(url.rsplit("amount=", 1)[1])
            sats = amount_sats if amount_sats is not None else msat // 1000
            return {"pr": f"lnbc{sats * 10}n1qqqqqq"}
        return fetch

    def lnurl(self, address):
        return {"address": address, "callback": "https://wallet.example/cb", "min_msat": 1000, "max_msat": 10**11}

    def test_nothing_is_paid_below_the_threshold(self):
        self.earn(9)  # 900 sats owed
        self.assertEqual(worker.payouts(self.con, coins=50000, lnurl=self.lnurl, fetch=self.fetch_invoice(), run=self.run_fake()), [])

    def test_payout_at_threshold_pays_the_owed_amount_once(self):
        self.earn(12)  # 1,200 sats owed
        done = worker.payouts(self.con, coins=50000, lnurl=self.lnurl, fetch=self.fetch_invoice(), run=self.run_fake())
        self.assertEqual(done, [(self.code, 1200, "paid")])
        self.assertEqual(api.partner_view(self.con, self.code)["paid_sats"], 1200)
        self.assertEqual(api.partner_view(self.con, self.code)["owed_sats"], 0)
        self.assertEqual(worker.payouts(self.con, coins=50000, lnurl=self.lnurl, fetch=self.fetch_invoice(), run=self.run_fake()), [])
        with open(worker.LEDGER) as f:
            self.assertIn(f",{self.code},1200,3,", f.read())

    def test_invoice_for_another_amount_is_not_paid(self):
        self.earn(10)
        done = worker.payouts(self.con, coins=50000, lnurl=self.lnurl, fetch=self.fetch_invoice(amount_sats=99999), run=self.run_fake())
        self.assertEqual(done, [(self.code, 1000, "failed")])
        self.assertFalse(any("send" in c for c in self.calls))
        # A recent failure waits before retrying.
        self.assertEqual(worker.payouts(self.con, coins=50000, lnurl=self.lnurl, fetch=self.fetch_invoice(), run=self.run_fake()), [])

    def test_unknown_outcome_blocks_further_payouts_to_that_partner(self):
        self.earn(10)
        done = worker.payouts(self.con, coins=50000, lnurl=self.lnurl, fetch=self.fetch_invoice(), run=self.run_fake(status="TIMEOUT"))
        self.assertEqual(done, [(self.code, 1000, "unknown")])
        self.earn(10)  # 1,000 more earned meanwhile: owed again, but blocked
        self.assertEqual(api.partner_view(self.con, self.code)["owed_sats"], 1000)
        self.assertEqual(worker.payouts(self.con, coins=50000, lnurl=self.lnurl, fetch=self.fetch_invoice(), run=self.run_fake()), [])
        self.assertEqual(api.partner_view(self.con, self.code)["in_payment_sats"], 1000)

    def test_daily_cap_and_wallet_floor(self):
        self.earn(60)  # 6,000 sats owed, over the 5,000 daily cap
        self.assertEqual(worker.payouts(self.con, coins=50000, lnurl=self.lnurl, fetch=self.fetch_invoice(), run=self.run_fake()), [])
        con2 = self.con
        con2.execute("DELETE FROM commissions WHERE rowid > 20")  # 2,000 owed
        self.assertEqual(worker.payouts(self.con, coins=3000, lnurl=self.lnurl, fetch=self.fetch_invoice(), run=self.run_fake()), [])

    def test_payouts_off_by_default(self):
        self.earn(10)
        worker.PAYOUTS = False
        try:
            self.assertEqual(worker.payouts(self.con, coins=50000, lnurl=self.lnurl, fetch=self.fetch_invoice(), run=self.run_fake()), [])
        finally:
            worker.PAYOUTS = True


class Helpers(unittest.TestCase):
    def test_invoice_amounts(self):
        self.assertEqual(lib.invoice_amount_msat("lnbc10u1qq"), 1_000_000)
        self.assertEqual(lib.invoice_amount_msat("lnbc12000n1qq"), 1_200_000)
        self.assertIsNone(lib.invoice_amount_msat("lnbc1qq"))
        self.assertIsNone(lib.invoice_amount_msat("lntb10u1qq"))

    def test_macaroon_ids_roundtrip(self):
        self.assertEqual(lib.macaroon_ids(macaroon("ab" * 32, "cd" * 32)), ("ab" * 32, "cd" * 32))


if __name__ == "__main__":
    unittest.main()
