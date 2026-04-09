#!/usr/bin/env bash

set -euo pipefail

TOGGLE_BACKBLAZE_SCRIPT="$HOME/scripts/toggle_backblaze_settings/toggle_backblaze_settings.sh"

ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WanCheckScript="$ScriptDir/wan_check.py"
VenvActivate="$ScriptDir/.venv/bin/activate"

if [ ! -f "$WanCheckScript" ]; then
	echo "[manage_backblaze] Error: wan_check.py not found at $WanCheckScript" >&2
	exit 1
fi

if [ ! -f "$VenvActivate" ]; then
	echo "[manage_backblaze] Error: virtual environment activate script not found at $VenvActivate" >&2
	exit 1
fi

if [ ! -x "$TOGGLE_BACKBLAZE_SCRIPT" ]; then
	echo "[manage_backblaze] Error: toggle_backblaze_settings.sh is not executable at $TOGGLE_BACKBLAZE_SCRIPT" >&2
	exit 1
fi

. "$VenvActivate"

cd "$ScriptDir"

# Load environment variables from .env if present (in HomeDir)
# Note: this "sources" the file, so it should contain simple KEY=VALUE lines.
EnvFile=".env"
if [ -f "$EnvFile" ]; then
  set -a
  . "$EnvFile"
  set +a
fi


if ! command -v uv >/dev/null 2>&1; then
	echo "[manage_backblaze] Error: uv is not available in PATH" >&2
	exit 1
fi

uv sync

check_output="$(python3 "$WanCheckScript" --onprimary)"

if [ "$check_output" = "Yes" ]; then
	exec "$TOGGLE_BACKBLAZE_SCRIPT" continuous
fi

if [ "$check_output" = "No" ]; then
	exec "$TOGGLE_BACKBLAZE_SCRIPT" manual
fi

echo "[manage_backblaze] Error: unexpected wan_check.py output: $check_output" >&2
exit 1
