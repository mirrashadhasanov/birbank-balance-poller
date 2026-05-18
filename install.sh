#!/bin/sh
# Install birbank-poller as a macOS launchd agent.
#
# Copies the project to a TCC-friendly location and registers a per-user
# launchd job that polls /api/b2b/accounts on a fixed interval.
set -eu

LABEL="com.user.birbank-poller"
DEST_DEFAULT="$HOME/Library/Application Support/birbank-poller"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="${BIRBANK_INSTALL_DIR:-$DEST_DEFAULT}"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_N=$(id -u)

if [ ! -f "$SRC_DIR/.env" ]; then
    echo "error: $SRC_DIR/.env not found." >&2
    echo "       cp .env.example .env  and fill in credentials first." >&2
    exit 1
fi

mkdir -p "$DEST_DIR"
mkdir -p "$HOME/Library/LaunchAgents"

# Copy code + config. Do not copy state / token / log files.
cp -p "$SRC_DIR/birbank_poller.py" "$DEST_DIR/"
cp -p "$SRC_DIR/run.sh"            "$DEST_DIR/"
cp -p "$SRC_DIR/.env"              "$DEST_DIR/"
cp -p "$SRC_DIR/.env.example"      "$DEST_DIR/"
chmod 600 "$DEST_DIR/.env"
chmod +x "$DEST_DIR/run.sh" "$DEST_DIR/birbank_poller.py"

# Render plist with absolute path. sed -i differs across macOS/GNU; use redirect.
sed "s|__PROJECT_DIR__|$DEST_DIR|g" \
    "$SRC_DIR/com.user.birbank-poller.plist" > "$PLIST_DST"
chmod 644 "$PLIST_DST"

# Reload job (idempotent).
launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_N" "$PLIST_DST"
launchctl enable "gui/$UID_N/$LABEL"
launchctl kickstart -k "gui/$UID_N/$LABEL"

echo
echo "Installed."
echo "  project : $DEST_DIR"
echo "  plist   : $PLIST_DST"
echo "  logs    : $DEST_DIR/poller.err"
echo
echo "Control:"
echo "  launchctl print  gui/$UID_N/$LABEL"
echo "  launchctl kickstart -k gui/$UID_N/$LABEL   # run now"
echo "  launchctl bootout      gui/$UID_N/$LABEL   # stop"
