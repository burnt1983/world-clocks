#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SHARE="${HOME}/.local/share/lee-worldclock"
BIN="${HOME}/.local/bin"
mkdir -p "$SHARE" "$BIN"
install -m 0755 "$ROOT/lee_worldclock_widget.py" "$SHARE/lee_worldclock_widget.py"
install -m 0644 "$ROOT/desklet_geometry.py" "$SHARE/desklet_geometry.py"
ln -sfn "$SHARE/lee_worldclock_widget.py" "$BIN/worldclock"
ln -sfn "$SHARE/lee_worldclock_widget.py" "$BIN/lee-worldclock-widget"
echo "Installed."
echo "  Run: worldclock"
echo "  It does not start at login until you tick Open at login."
