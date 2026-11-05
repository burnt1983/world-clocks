#!/usr/bin/env bash
set -euo pipefail
rm -f \
  "${HOME}/.local/bin/worldclock" \
  "${HOME}/.local/bin/lee-worldclock-widget" \
  "${HOME}/.local/share/applications/lee-worldclock.desktop"
rm -rf "${HOME}/.local/share/lee-worldclock"
echo "Removed the program. Saved settings under ~/.config were left in place."
