# birbank-business-listener

Polls the Birbank Business B2B API (`/api/b2b/accounts`) on a schedule, diffs
each account's balance against the previous snapshot, and sends a Telegram
notification when any balance increases.

Unofficial. Not affiliated with Kapital Bank.

## Requirements

- macOS (uses `launchd`), or Linux (`systemd` / `cron`). See
  [`examples/`](examples/) for unit files and a crontab line.
- Python 3.9+ (stdlib only — no third-party packages).
- A Birbank Business account with the API portal contract signed via ASAN
  İmza, and access granted to the "Current account information" product.

## How credentials work

1. Register at <https://api.birbank.business>.
2. Sign the integration contract via ASAN İmza (must be the company's
   official representative).
3. Subscribe to the "Cari hesab məlumatları" / "Current account information"
   product.
4. Kapital Bank emails the API username (7-digit customer code + a trailing
   letter, e.g. `0123456A`). The password is the one you set yourself.

See `.env.example` for the full list of environment variables.

## Quick start

```sh
git clone https://github.com/Zeynal7/birbank-balance-poller.git
cd birbank-balance-poller

# tighten default file mode for the duration of this shell so .env starts 0600
umask 077
cp .env.example .env
# edit .env: fill BIRBANK_USERNAME, BIRBANK_PASSWORD, Telegram creds

# one-shot run from the repo directory
./run.sh
```

The first run establishes a baseline (no notifications). Each subsequent run
notifies on positive balance deltas only.

## Install as launchd agent (macOS)

```sh
./install.sh
```

Defaults to `~/Library/Application Support/birbank-poller/` because macOS TCC
blocks `launchd` from running scripts inside `~/Documents/`, `~/Desktop/`,
etc. Override the target with `BIRBANK_INSTALL_DIR=/some/path ./install.sh`.

The agent polls every 300 seconds (`StartInterval` in the plist). Edit the
template before installing if you need a different cadence; the upstream API
is rate-limited — going below 60 s is unwise.

### Manage the agent

```sh
launchctl print     gui/$(id -u)/com.user.birbank-poller
launchctl kickstart -k gui/$(id -u)/com.user.birbank-poller   # run now
launchctl bootout      gui/$(id -u)/com.user.birbank-poller   # stop
rm "$HOME/Library/LaunchAgents/com.user.birbank-poller.plist" # uninstall
```

Logs land in `<install dir>/poller.err` (stdlib `logging` writes to stderr).

## Install on Linux (systemd)

```sh
sudo useradd -r -s /usr/sbin/nologin -d /opt/birbank-poller birbank
sudo install -d -o birbank -g birbank -m 750 /opt/birbank-poller

sudo install -o birbank -g birbank -m 750 run.sh birbank_poller.py /opt/birbank-poller/
sudo install -o birbank -g birbank -m 600 .env                     /opt/birbank-poller/

sudo cp examples/birbank-poller.service /etc/systemd/system/
sudo cp examples/birbank-poller.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now birbank-poller.timer
journalctl -u birbank-poller -f
```

For plain `cron` on a system without systemd, see
[`examples/crontab.example`](examples/crontab.example). The wrapper
(`run.sh`) `cd`s to its own directory before invoking Python, so
`state.json` and `token.json` resolve correctly regardless of cron's `PATH`
or `cwd`.

## Notifications

Telegram is the only built-in channel. Set `TELEGRAM_BOT_TOKEN` (from
`@BotFather`) and `TELEGRAM_CHAT_ID` (run `getUpdates` after messaging the
bot). Leave either blank to fall back to stdout.

Notification text:

```
+150.00 AZN on AZ57AIIB410400K9440591522238
0 -> 150.00
```

## Security model

- Credentials live in `.env` (mode `0600`). Never commit it.
- The cached JWT and refresh token live in `token.json` (mode `0600`, written
  atomically via `os.open(O_CREAT|O_WRONLY|O_TRUNC, 0o600)`).
- `state.json` (mode `0600`) stores last-seen balances per IBAN — PII, not
  secret, but locked down anyway.
- `BIRBANK_BASE` is whitelisted to `{my,pre-my}.birbank.business` to prevent
  redirection by a hostile env var.
- HTTP response bodies are read with a 1 MiB cap.
- An `flock`-based lockfile prevents overlapping runs when `launchd` fires
  on top of an existing iteration.
- The Telegram error path does not log the bot-token URL.
- The login error path does not log the bank's full response payload.

If you fork, audit `_validate_base`, `_post_json`, and `_atomic_write_private`
before extending the auth or storage surface.

## API surface used

- `POST /api/b2b/login`     `{username,password}` → `jwttoken`, `jwtrefreshtoken`
- `POST /api/b2b/refresh`   `{refreshToken}` → new `jwttoken`, `jwtrefreshtoken`
  (response code `100` = invalid → fall back to full login)
- `GET  /api/b2b/accounts`  → `responseData.accountsList[]`
  - `ibanAcNo`, `custAcNo`, `ccy`, `acDesc`, `status`,
    `plannedAmt`, `currAmt`, `hold`

The poller uses `currAmt` as the source of truth, with `plannedAmt` as a
fallback only when `currAmt` is absent. Only accounts with `status == "OPEN"`
are tracked.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

stdlib `unittest` only. No fixtures, no network calls.

## Reporting vulnerabilities

See [SECURITY.md](SECURITY.md). Use GitHub's private advisory form rather
than a public issue.

## Contributing

PRs welcome. Keep:

- stdlib-only (no `requests`, no `python-dotenv`),
- functions/classes private by default (`_` prefix),
- error messages free of tokens, passwords, and full response bodies,
- a test in `tests/` for any new parsing or validation logic.

## License

[MIT](LICENSE).
