# WAN Failover Check

WAN Failover Check monitors whether your connection is on the primary NBN service or on a backup WAN connection such as 5G failover.

It works by comparing the current public IP address with the expected NBN static IP. From that single check, the app can:

- report the current WAN state
- write the latest state to `status.json`
- keep a historical CSV log of failover periods
- optionally ping Better Stack heartbeats
- expose a small HTTP status endpoint
- support helper automation such as Backblaze mode switching

## What the app does

The main program is `wan_check.py`.

On each WAN check it:

- queries multiple public IP echo services until one succeeds
- compares the result to the configured primary WAN IP
- classifies the connection as `NBN (primary)` or `5G Backup`
- updates `status.json`
- updates `failover_history.csv`
- optionally sends a Better Stack heartbeat

When running in monitor mode, it repeats the check on a timer and logs transitions.

## Project files

- `wan_check.py`: main monitor and optional status API
- `launch.sh`: launcher script that loads `.env`, runs `uv sync`, and starts the app
- `manage_backblaze.sh`: helper that switches a Backblaze-related script based on current WAN state
- `status.json`: latest observed WAN state
- `failover_history.csv`: history of backup WAN periods, created automatically
- `pyproject.toml`: project metadata and dependencies

## Requirements

- Python 3.13 or newer
- `uv`
- internet access to at least one supported public IP echo service

The repository is already set up to use `uv` for dependency management.

## Installation

From the repository root:

```bash
uv sync
```

To run directly with `uv`:

```bash
uv run wan_check.py --onetime
```

## Configuration

Configuration is driven primarily by environment variables, with one command-line override for the NBN IP.

### Environment variables

- `NBN_STATIC_IP`: expected public IP of the primary NBN connection
- `HEARTBEAT_PRIMARY`: Better Stack heartbeat URL to use when on primary WAN
- `HEARTBEAT_LTE`: Better Stack heartbeat URL to use when on backup WAN
- `MONITOR_INTERVAL`: monitor polling interval in seconds, default `60`
- `API_IP`: bind address for the optional HTTP status API; if unset, the API is disabled
- `API_PORT`: port for the HTTP status API, default `8080`
- `FAILOVER_CSV_FILE`: path to the failover history CSV, default `failover_history.csv`

### Typical `.env` example

```dotenv
NBN_STATIC_IP=180.150.43.236
HEARTBEAT_PRIMARY=https://betterstack.example/primary
HEARTBEAT_LTE=https://betterstack.example/failover
MONITOR_INTERVAL=60
API_IP=0.0.0.0
API_PORT=8080
FAILOVER_CSV_FILE=failover_history.csv
```

### Command-line override

Use `--nbn-ip <ip>` to override `NBN_STATIC_IP` for any run mode.

Example:

```bash
uv run wan_check.py --nbn-ip 180.150.43.236 --onetime
```

If both are present, `--nbn-ip` takes precedence over `NBN_STATIC_IP`.

## Usage

### Continuous monitor mode

This is the default mode when no mutually exclusive mode flag is supplied.

```bash
uv run wan_check.py
```

Behavior:

- starts the optional HTTP API if `API_IP` is set
- checks WAN state every `MONITOR_INTERVAL` seconds
- writes `status.json` on every check
- updates the failover CSV on every check
- sends Better Stack heartbeats unless disabled
- prints transition and status messages to stdout
- backs off polling after repeated check failures

Disable heartbeats for a run:

```bash
uv run wan_check.py --noheartbeat
```

### One-time check

Runs one WAN check, writes outputs, prints the result, and exits.

```bash
uv run wan_check.py --onetime
```

Example output:

```text
[ok] NBN (primary)  ext-ip=180.150.43.236
```

### Primary-status check

Prints `Yes` when on the primary connection and `No` otherwise.

```bash
uv run wan_check.py --onprimary
```

This mode still performs a real WAN check using the same comparison logic as the other modes.

## Output files

### `status.json`

The app writes the most recent WAN state to `status.json`.

Example:

```json
{
	"timestamp": "2026-04-09T07:01:13Z",
	"on_primary": true,
	"status": "NBN (primary)",
	"external_ip": "180.150.43.236"
}
```

Fields:

- `timestamp`: UTC timestamp of the recorded status
- `on_primary`: `true` when the app believes the connection is on NBN
- `status`: human-readable state label
- `external_ip`: detected public IP address

### `failover_history.csv`

The app writes failover periods to `failover_history.csv`, or to the path defined by `FAILOVER_CSV_FILE`.

The CSV is created automatically with these columns:

- `Start Time`
- `End Time`
- `Duration Hours`

Behavior:

- when WAN moves to backup, the app opens a new CSV row if there is no open row already
- while WAN remains on backup, no duplicate open row is created
- when WAN returns to primary, the app fills in `End Time`
- the app calculates `Duration Hours` when the failover closes
- `Start Time` and `End Time` are written in local time as `yyyy-mm-dd hh:mm:ss`

Example:

```csv
Start Time,End Time,Duration Hours
2026-04-09 10:00:00,2026-04-09 12:30:00,2.50
```

## Better Stack heartbeats

If heartbeats are enabled, the app sends a POST request to:

- `HEARTBEAT_PRIMARY` when on primary WAN
- `HEARTBEAT_LTE` when on backup WAN

Heartbeats are enabled only when:

- `--noheartbeat` is not used
- `HEARTBEAT_PRIMARY` is configured

If the selected heartbeat URL is missing, that heartbeat is skipped.

## HTTP status API

If `API_IP` is set, the app starts a FastAPI server and exposes:

- `GET /status`

Default port is `8080`, configurable via `API_PORT`.

Example:

```text
http://<API_IP>:8080/status
```

The endpoint returns the contents of `status.json`.

If the file is unavailable or invalid JSON, the endpoint returns HTTP `503` with an error payload.

## Supported IP echo services

The app tries the following services in order until one returns a usable IP:

- `https://api.ipify.org`
- `https://icanhazip.com`
- `https://ifconfig.me/ip`
- `https://checkip.amazonaws.com`

If they all fail, the check fails for that cycle.

## Helper scripts

### `launch.sh`

`launch.sh` is a convenience wrapper for starting the application consistently.

It:

- changes to the chosen home directory
- loads `.env` if present
- reads the launch target from `pyproject.toml`
- ensures `uv` is available
- runs `uv sync`
- starts the app with `uv run`
- exits cleanly on `SIGINT` or `SIGTERM`

Default usage:

```bash
./launch.sh
```

Override the working directory:

```bash
./launch.sh --homedir /path/to/wan_failover_check
```

Pass normal app arguments through the launcher:

```bash
./launch.sh --onetime --nbn-ip 180.150.43.236
```

### `manage_backblaze.sh`

`manage_backblaze.sh` uses the current WAN state to switch an external Backblaze helper script between two modes.

It:

- activates the local virtual environment
- loads `.env` if present
- runs `uv sync`
- executes `wan_check.py --onprimary`
- runs the external toggle script in `continuous` mode when WAN is primary
- runs the external toggle script in `manual` mode when WAN is backup

This script expects the external toggle script to exist at:

```text
$HOME/scripts/toggle_backblaze_settings/toggle_backblaze_settings.sh
```

## Exit behavior

- a normal successful one-time run exits with code `0`
- monitor mode exits with code `0` on Ctrl-C
- configuration errors return a non-zero exit code
- total IP lookup failure for a one-time run returns a non-zero exit code

## Notes

- `status.json` timestamps are UTC
- failover CSV timestamps are local time
- the app uses a simple IP comparison model: if the current public IP matches the configured NBN IP, the connection is treated as primary
- the labels are currently `NBN (primary)` and `5G Backup`, but the backup path is effectively any non-primary public IP

## Quick test commands

```bash
uv run wan_check.py --onetime
uv run wan_check.py --onprimary
uv run wan_check.py --nbn-ip 180.150.43.236 --onetime
uv run wan_check.py --noheartbeat
curl http://127.0.0.1:8080/status
```
