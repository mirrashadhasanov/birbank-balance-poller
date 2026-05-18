#!/usr/bin/env python3
"""Birbank Business balance poller.

Polls /api/b2b/accounts, diffs per-IBAN balance against last snapshot,
fires a notification when any balance increases.

Token acquisition is out of scope (ASAN İmza login). Supply a valid
bearer/session via env: see .env.example.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import FrameType
from typing import Any, Final
from urllib import error, request

LOG: Final = logging.getLogger("birbank")

_TIMEOUT_S: Final = 20
_MAX_BODY_BYTES: Final = 1 * 1024 * 1024
_DEFAULT_BASE: Final = "https://pre-my.birbank.business"
_ALLOWED_HOSTS: Final = frozenset(
    {
        "my.birbank.business",
        "pre-my.birbank.business",
    }
)
_ACCOUNTS_PATH: Final = "/api/b2b/accounts"
_LOGIN_PATH: Final = "/api/b2b/login"
_REFRESH_PATH: Final = "/api/b2b/refresh"
_STATE_FILE: Final = Path(os.getenv("BIRBANK_STATE", "state.json"))
_TOKEN_FILE: Final = Path(os.getenv("BIRBANK_TOKEN_FILE", "token.json"))
_LOCK_FILE: Final = Path(os.getenv("BIRBANK_LOCK", ".poller.lock"))
_BALANCE_KEYS: Final = ("currAmt", "plannedAmt")
_IBAN_KEYS: Final = ("ibanAcNo", "custAcNo")
_CURRENCY_KEYS: Final = ("ccy",)


@dataclass(frozen=True)
class _Account:
    iban: str
    balance: Decimal
    currency: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class _Notifier:
    telegram_token: str | None
    telegram_chat: str | None

    def send(self, text: str) -> None:
        if self.telegram_token and self.telegram_chat:
            self._send_telegram(text)
            return
        self._send_stdout(text)

    def _send_telegram(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        body = json.dumps({"chat_id": self.telegram_chat, "text": text}).encode()
        req = request.Request(
            url,
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                resp.read(_MAX_BODY_BYTES)
        except error.HTTPError as exc:
            LOG.error("telegram HTTP %s", exc.code)
        except error.URLError:
            LOG.error("telegram network error")
        except OSError:
            LOG.error("telegram socket error")

    @staticmethod
    def _send_stdout(text: str) -> None:
        print(f"[NOTIFY] {text}", flush=True)


@dataclass(frozen=True)
class _Credentials:
    username: str
    password: str


class _Client:
    def __init__(self, base: str, creds: _Credentials) -> None:
        self._base = _validate_base(base)
        self._creds = creds
        cached = _load_cached_tokens()
        self._token: str | None = cached.get("jwttoken")
        self._refresh: str | None = cached.get("jwtrefreshtoken")

    def fetch_accounts(self) -> list[dict[str, Any]]:
        try:
            payload = self._request_accounts()
        except error.HTTPError as exc:
            if exc.code != 401:
                raise
            LOG.info("401 on accounts, refreshing")
            if not self._try_refresh():
                self._login()
            payload = self._request_accounts()
        return _extract_account_list(payload)

    def _try_refresh(self) -> bool:
        if not self._refresh:
            return False
        try:
            payload = _post_json(
                self._base + _REFRESH_PATH,
                {"refreshToken": self._refresh},
            )
        except error.HTTPError as exc:
            LOG.info("refresh HTTP %s, will re-login", exc.code)
            return False
        except (error.URLError, OSError, json.JSONDecodeError):
            LOG.info("refresh failed (network/parse), will re-login")
            return False
        resp_meta = payload.get("response") or {}
        if str(resp_meta.get("code")) != "0":
            LOG.info("refresh response code != 0, re-login")
            return False
        data = payload.get("responseData") or {}
        token = data.get("jwttoken")
        if not token:
            return False
        self._token = token
        self._refresh = data.get("jwtrefreshtoken") or self._refresh
        _save_cached_tokens(self._token, self._refresh)
        LOG.info("refresh ok")
        return True

    def _request_accounts(self) -> Any:
        if not self._token:
            self._login()
        req = request.Request(self._base + _ACCOUNTS_PATH, method="GET")
        req.add_header("accept", "application/json")
        req.add_header("user-agent", "birbank-poller/1.0")
        req.add_header("authorization", f"Bearer {self._token}")
        with request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read(_MAX_BODY_BYTES).decode())

    def _login(self) -> None:
        payload = _post_json(
            self._base + _LOGIN_PATH,
            {"username": self._creds.username, "password": self._creds.password},
        )
        resp_meta = payload.get("response") or {}
        code = resp_meta.get("code")
        if str(code) != "0":
            msg = resp_meta.get("message")
            raise RuntimeError(f"login failed code={code!r} message={msg!r}")
        data = payload.get("responseData") or {}
        token = data.get("jwttoken")
        if not token:
            raise RuntimeError("login response missing jwttoken")
        self._token = token
        self._refresh = data.get("jwtrefreshtoken")
        _save_cached_tokens(self._token, self._refresh)
        LOG.info("login ok, token cached")


def _validate_base(base: str) -> str:
    stripped = base.rstrip("/")
    if not stripped.startswith("https://"):
        raise ValueError("BIRBANK_BASE must be https://")
    host = stripped[len("https://") :].split("/", 1)[0].split(":", 1)[0]
    if host not in _ALLOWED_HOSTS:
        raise ValueError(f"BIRBANK_BASE host not allowed: {host!r}")
    return stripped


def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode()
    req = request.Request(
        url,
        data=data,
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "birbank-poller/1.0",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        raw = resp.read(_MAX_BODY_BYTES).decode()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("non-object JSON response")
    return parsed


def _load_cached_tokens() -> dict[str, Any]:
    if not _TOKEN_FILE.exists():
        return {}
    try:
        loaded = json.loads(_TOKEN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_cached_tokens(token: str, refresh: str | None) -> None:
    payload = {"jwttoken": token, "jwtrefreshtoken": refresh}
    _atomic_write_private(_TOKEN_FILE, json.dumps(payload))


def _atomic_write_private(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def _extract_account_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    resp_meta = payload.get("response") or {}
    code = str(resp_meta.get("code", ""))
    if code and code != "0":
        LOG.warning("accounts response code=%s msg=%r", code, resp_meta.get("message"))
        return []
    data = payload.get("responseData")
    if not isinstance(data, dict):
        return []
    items = data.get("accountsList")
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict)]


def _pick(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for k in ("amount", "value", "available"):
            if k in value:
                return _to_decimal(value[k])
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_account(raw: dict[str, Any]) -> _Account | None:
    if str(raw.get("status", "")).upper() not in ("", "OPEN"):
        return None
    iban = _pick(raw, _IBAN_KEYS)
    balance = _to_decimal(_pick(raw, _BALANCE_KEYS))
    currency = _pick(raw, _CURRENCY_KEYS) or ""
    if not iban or balance is None:
        return None
    return _Account(iban=str(iban), balance=balance, currency=str(currency), raw=raw)


def _load_state() -> dict[str, str]:
    if not _STATE_FILE.exists():
        return {}
    try:
        loaded = json.loads(_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        LOG.warning("state file unreadable, starting fresh")
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): str(v) for k, v in loaded.items() if isinstance(v, (str, int))}


def _save_state(state: dict[str, str]) -> None:
    _atomic_write_private(_STATE_FILE, json.dumps(state, indent=2, sort_keys=True))


def _diff_and_notify(
    accounts: list[_Account],
    state: dict[str, str],
    notifier: _Notifier,
) -> dict[str, str]:
    next_state: dict[str, str] = {}
    for acc in accounts:
        key = f"{acc.iban}:{acc.currency}"
        prev_raw = state.get(key)
        prev: Decimal | None
        try:
            prev = Decimal(prev_raw) if prev_raw is not None else None
        except InvalidOperation:
            LOG.warning("bad prev value for %s in state, resetting", key)
            prev = None
        next_state[key] = str(acc.balance)
        if prev is None:
            LOG.info("baseline %s = %s %s", acc.iban, acc.balance, acc.currency)
            continue
        delta = acc.balance - prev
        if delta > 0:
            notifier.send(
                f"+{delta} {acc.currency} on {acc.iban}\n"
                f"{prev} -> {acc.balance}"
            )
        elif delta < 0:
            LOG.info("decrease %s: %s -> %s", acc.iban, prev, acc.balance)
    return next_state


def _build_client() -> _Client | None:
    username = os.getenv("BIRBANK_USERNAME") or ""
    password = os.getenv("BIRBANK_PASSWORD") or ""
    if not username or not password:
        return None
    try:
        return _Client(
            base=os.getenv("BIRBANK_BASE", _DEFAULT_BASE),
            creds=_Credentials(username=username, password=password),
        )
    except ValueError as exc:
        LOG.error("invalid BIRBANK_BASE: %s", exc)
        return None


def _build_notifier() -> _Notifier:
    return _Notifier(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat=os.getenv("TELEGRAM_CHAT_ID") or None,
    )


def _run_once() -> int:
    client = _build_client()
    notifier = _build_notifier()
    if client is None:
        LOG.error("missing/invalid credentials or base")
        return 2
    try:
        raw_accounts = client.fetch_accounts()
    except error.HTTPError as exc:
        LOG.error("HTTP %s on accounts", exc.code)
        return 3
    except error.URLError:
        LOG.error("network error on accounts")
        return 4
    except (json.JSONDecodeError, RuntimeError) as exc:
        LOG.error("response error: %s", exc)
        return 6
    parsed = [a for a in (_parse_account(r) for r in raw_accounts) if a]
    if not parsed:
        LOG.warning("no accounts parsed from %d raw items", len(raw_accounts))
        return 5
    state = _load_state()
    next_state = _diff_and_notify(parsed, state, notifier)
    _save_state(next_state)
    LOG.info("ok, %d accounts checked", len(parsed))
    return 0


_STOP = False


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    global _STOP
    _STOP = True
    LOG.info("signal %d received, stopping", signum)


def _run_loop(interval_s: int) -> int:
    LOG.info("loop mode, every %ds", interval_s)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    while not _STOP:
        try:
            _run_once()
        except Exception:
            LOG.exception("iteration crashed")
        # interruptible sleep in 1-second ticks so SIGTERM is responsive
        deadline = time.monotonic() + interval_s
        while not _STOP and time.monotonic() < deadline:
            time.sleep(1)
    return 0


def _parse_interval() -> int:
    raw = os.getenv("POLL_INTERVAL_S", "0")
    try:
        value = int(raw)
    except ValueError:
        LOG.warning("invalid POLL_INTERVAL_S=%r, using 0", raw)
        return 0
    return max(0, value)


def _acquire_single_instance_lock() -> Any:
    fd = os.open(str(_LOCK_FILE), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        LOG.error("another instance is running (lock held)")
        return None
    return fd


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    lock_fd = _acquire_single_instance_lock()
    if lock_fd is None:
        return 7
    try:
        interval = _parse_interval()
        if interval > 0:
            return _run_loop(interval)
        return _run_once()
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
