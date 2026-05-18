from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import birbank_poller as bp


class _StubNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


class ValidateBase(unittest.TestCase):
    def test_accepts_known_hosts(self) -> None:
        self.assertEqual(
            bp._validate_base("https://my.birbank.business"),
            "https://my.birbank.business",
        )
        self.assertEqual(
            bp._validate_base("https://pre-my.birbank.business/"),
            "https://pre-my.birbank.business",
        )

    def test_rejects_http(self) -> None:
        with self.assertRaises(ValueError):
            bp._validate_base("http://my.birbank.business")

    def test_rejects_unknown_host(self) -> None:
        with self.assertRaises(ValueError):
            bp._validate_base("https://evil.example.com")

    def test_rejects_subdomain_spoof(self) -> None:
        with self.assertRaises(ValueError):
            bp._validate_base("https://my.birbank.business.evil.com")


class ToDecimal(unittest.TestCase):
    def test_string(self) -> None:
        self.assertEqual(bp._to_decimal("12.34"), Decimal("12.34"))

    def test_int(self) -> None:
        self.assertEqual(bp._to_decimal(0), Decimal(0))

    def test_nested_dict(self) -> None:
        self.assertEqual(bp._to_decimal({"amount": "5.50"}), Decimal("5.50"))

    def test_none(self) -> None:
        self.assertIsNone(bp._to_decimal(None))

    def test_garbage(self) -> None:
        self.assertIsNone(bp._to_decimal("not-a-number"))


class ParseAccount(unittest.TestCase):
    def _raw(self, **kw):
        base = {
            "branchCode": "103",
            "custAcNo": "40160000000000000000",
            "ibanAcNo": "AZ21AIIB40160000000000000000",
            "ccy": "AZN",
            "acDesc": "TEST MMC",
            "status": "OPEN",
            "plannedAmt": "30",
            "currAmt": "30",
            "hold": 0,
        }
        base.update(kw)
        return base

    def test_happy_path(self) -> None:
        acc = bp._parse_account(self._raw())
        self.assertIsNotNone(acc)
        assert acc is not None
        self.assertEqual(acc.iban, "AZ21AIIB40160000000000000000")
        self.assertEqual(acc.balance, Decimal("30"))
        self.assertEqual(acc.currency, "AZN")

    def test_prefers_currAmt_over_plannedAmt(self) -> None:
        acc = bp._parse_account(self._raw(currAmt="100", plannedAmt="200"))
        assert acc is not None
        self.assertEqual(acc.balance, Decimal("100"))

    def test_falls_back_to_plannedAmt(self) -> None:
        raw = self._raw(plannedAmt="200")
        del raw["currAmt"]
        acc = bp._parse_account(raw)
        assert acc is not None
        self.assertEqual(acc.balance, Decimal("200"))

    def test_filters_non_open(self) -> None:
        self.assertIsNone(bp._parse_account(self._raw(status="CLOSED")))

    def test_missing_iban(self) -> None:
        raw = self._raw()
        del raw["ibanAcNo"]
        del raw["custAcNo"]
        self.assertIsNone(bp._parse_account(raw))


class ExtractAccountList(unittest.TestCase):
    def test_happy_path(self) -> None:
        payload = {
            "responseData": {"accountsList": [{"a": 1}, {"b": 2}]},
            "response": {"code": "0"},
        }
        self.assertEqual(bp._extract_account_list(payload), [{"a": 1}, {"b": 2}])

    def test_error_code_returns_empty(self) -> None:
        payload = {
            "responseData": {"accountsList": [{"a": 1}]},
            "response": {"code": "401", "message": "Token expired"},
        }
        self.assertEqual(bp._extract_account_list(payload), [])

    def test_missing_responseData(self) -> None:
        self.assertEqual(bp._extract_account_list({"response": {"code": "0"}}), [])

    def test_non_dict(self) -> None:
        self.assertEqual(bp._extract_account_list([]), [])


class DiffAndNotify(unittest.TestCase):
    def _acct(self, iban: str, balance: str, ccy: str = "AZN") -> bp._Account:
        return bp._Account(iban=iban, balance=Decimal(balance), currency=ccy, raw={})

    def test_baseline_no_notify(self) -> None:
        n = _StubNotifier()
        nxt = bp._diff_and_notify([self._acct("X", "10")], {}, n)
        self.assertEqual(n.sent, [])
        self.assertEqual(nxt, {"X:AZN": "10"})

    def test_increase_fires(self) -> None:
        n = _StubNotifier()
        nxt = bp._diff_and_notify(
            [self._acct("X", "25.50")], {"X:AZN": "10"}, n
        )
        self.assertEqual(len(n.sent), 1)
        self.assertIn("+15.50", n.sent[0])
        self.assertIn("X", n.sent[0])
        self.assertEqual(nxt, {"X:AZN": "25.50"})

    def test_decrease_silent(self) -> None:
        n = _StubNotifier()
        bp._diff_and_notify([self._acct("X", "5")], {"X:AZN": "10"}, n)
        self.assertEqual(n.sent, [])

    def test_corrupt_prev_resets(self) -> None:
        n = _StubNotifier()
        nxt = bp._diff_and_notify(
            [self._acct("X", "10")], {"X:AZN": "not-a-number"}, n
        )
        self.assertEqual(n.sent, [])
        self.assertEqual(nxt, {"X:AZN": "10"})

    def test_currency_in_key(self) -> None:
        n = _StubNotifier()
        # same IBAN, different ccy → independent baselines
        nxt = bp._diff_and_notify(
            [self._acct("X", "10", "USD"), self._acct("X", "20", "EUR")],
            {},
            n,
        )
        self.assertEqual(nxt, {"X:USD": "10", "X:EUR": "20"})


class ParseInterval(unittest.TestCase):
    def test_valid(self) -> None:
        import os
        os.environ["POLL_INTERVAL_S"] = "300"
        self.assertEqual(bp._parse_interval(), 300)

    def test_invalid_returns_zero(self) -> None:
        import os
        os.environ["POLL_INTERVAL_S"] = "abc"
        self.assertEqual(bp._parse_interval(), 0)

    def test_negative_clamped(self) -> None:
        import os
        os.environ["POLL_INTERVAL_S"] = "-5"
        self.assertEqual(bp._parse_interval(), 0)


if __name__ == "__main__":
    unittest.main()
