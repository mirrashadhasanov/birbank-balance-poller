# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

Use GitHub's private vulnerability reporting:

  https://github.com/Zeynal7/birbank-balance-poller/security/advisories/new

A maintainer will respond within 7 days. Fixes are released as a tagged
patch version.

## In scope

- Credential handling (`.env`, `token.json`, env var leakage).
- Network surface: login, refresh, accounts request paths.
- File handling: atomic writes, permission modes, lockfile.
- Base URL validation, response size handling.
- Anything that could cause the poller to send credentials, tokens, or
  account data to a destination other than `*.birbank.business` and the
  configured Telegram bot.

## Out of scope

- Compromise of the host machine running the poller (the poller cannot
  defend against an attacker with local user access).
- Telegram Bot API compromise.
- Issues that require modifying `.env` to exploit (the user controls it).
- Rate-limit / availability concerns on Kapital Bank's side.

## Supported versions

Only the latest tagged release is supported. There is no LTS branch.
