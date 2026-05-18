#!/bin/sh
# Wrapper for cron/launchd. Loads .env literally (no shell expansion).
set -eu
cd "$(dirname "$0")"
exec /usr/bin/env python3 -c '
import os, sys, runpy
env = ".env"
if os.path.exists(env):
    with open(env, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n").rstrip("\r")
            if not line or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if not key:
                continue
            # strip surrounding single or double quotes only when matched
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("\"", "'"'"'"):
                val = val[1:-1]
            os.environ[key] = val
runpy.run_path("birbank_poller.py", run_name="__main__")
'
