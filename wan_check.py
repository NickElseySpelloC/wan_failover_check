#!/usr/bin/env python3
"""
Failover Monitor — checks whether the home network is on NBN or 5G failover.

By comparing the current external IP against the known static NBN IP.

Environment variables:
  NBN_STATIC_IP        Your static NBN IP (default: 180.150.43.236)
    HEARTBEAT_PRIMARY    Better Stack heartbeat URL for NBN-up state
    HEARTBEAT_LTE        Better Stack heartbeat URL for 5G-failover state
  MONITOR_INTERVAL     Polling interval in seconds (default: 60)
    API_IP               Bind address for the optional status API
    FAILOVER_CSV_FILE    CSV file used to record backup WAN intervals
"""

import argparse
import csv
import json
import os
import pathlib
import sys
import threading
import time
from datetime import UTC, datetime

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

# ── Config ────────────────────────────────────────────────────────────────────

NBN_IP = os.getenv("NBN_STATIC_IP")
HEARTBEAT_PRIMARY = os.getenv("HEARTBEAT_PRIMARY")
HEARTBEAT_LTE = os.getenv("HEARTBEAT_LTE")
INTERVAL = int(os.getenv("MONITOR_INTERVAL", "60"))
HTTP_TIMEOUT = 8
STATUS_FILE = "status.json"
FAILOVER_CSV_FILE = os.getenv("FAILOVER_CSV_FILE", "failover_history.csv")
FAILOVER_CSV_HEADERS = ["Start Time", "End Time", "Duration Hours"]
LOCAL_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# Multiple IP-echo services, tried in order — protects against any one being down.
IP_ECHO_URLS = [
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
    "https://checkip.amazonaws.com",
]
API_IP = os.getenv("API_IP")
API_PORT = int(os.getenv("API_PORT", "8080"))
UA = {"User-Agent": "failover-monitor/1.0"}

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="WAN Failover Monitor")


@app.get("/status")
def get_status():
    """Return the current WAN status from the status file."""
    try:
        with pathlib.Path(STATUS_FILE).open(encoding="utf-8") as f:
            return JSONResponse(content=json.load(f))
    except (OSError, json.JSONDecodeError) as e:
        return JSONResponse(status_code=503, content={"error": str(e)})


# ── Core logic ────────────────────────────────────────────────────────────────


def get_external_ip() -> str:
    """
    Query external IP-echo services in turn.

    Returns:
        The external IP address as a string.

    Raises:
        RuntimeError: If all IP-echo services fail.
    """
    last_err = None
    for url in IP_ECHO_URLS:
        try:
            r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            ip = r.text.strip()
            if ip:
                return ip
        except requests.RequestException as e:
            last_err = e
            continue
    error_msg = f"All IP-echo services failed. Last error: {last_err}"
    raise RuntimeError(error_msg)


def on_nbn(external_ip: str, nbn_ip: str) -> bool:
    """True when the current external IP matches our static NBN address.

    Args:
        external_ip (str): The current external IP address to check.
        nbn_ip (str): The configured primary WAN IP address.

    Returns:
        bool: True if the external IP matches the NBN static IP, False otherwise.
    """
    return external_ip == nbn_ip


def ping_heartbeat(url: str) -> bool:
    """Fire a Better Stack heartbeat.

    Args:
        url (str): The Better Stack heartbeat URL to ping.

    Returns:
        bool: True if the heartbeat was successful, False otherwise.
    """
    try:
        r = requests.post(url, headers=UA, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  [heartbeat] Failed to reach {url}: {e}")
        return False
    else:
        return True


def heartbeat_enabled(*, noheartbeat: bool) -> bool:
    """Return whether heartbeats should be sent for the current run.

    Args:
        noheartbeat (bool): Whether heartbeats were disabled by CLI flag.

    Returns:
        bool: True when heartbeats are enabled, False otherwise.
    """
    return not noheartbeat and bool(HEARTBEAT_PRIMARY)


def write_status(is_primary: bool, state: str, ip: str) -> None:
    """Write the current WAN status to STATUS_FILE."""
    payload = {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "on_primary": is_primary,
        "status": state,
        "external_ip": ip,
    }
    try:
        with pathlib.Path(STATUS_FILE).open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
    except OSError as e:
        print(f"  [status] Failed to write {STATUS_FILE}: {e}")


def format_local_timestamp(timestamp: datetime) -> str:
    """Format a local timestamp for CSV output.

    Args:
        timestamp (datetime): The timestamp to format.

    Returns:
        str: The timestamp in local CSV format.
    """
    return timestamp.strftime(LOCAL_DATETIME_FORMAT)


def ensure_failover_csv_exists() -> pathlib.Path:
    """Create the failover CSV with headers when it does not yet exist.

    Returns:
        pathlib.Path: The path to the failover CSV file.
    """
    csv_path = pathlib.Path(FAILOVER_CSV_FILE)
    if csv_path.exists():
        return csv_path

    try:
        with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FAILOVER_CSV_HEADERS)
            writer.writeheader()
    except OSError as e:
        print(f"  [failover-csv] Failed to create {FAILOVER_CSV_FILE}: {e}")

    return csv_path


def load_failover_rows(csv_path: pathlib.Path) -> list[dict[str, str]]:
    """Load all failover CSV rows, returning an empty list on read errors.

    Args:
        csv_path (pathlib.Path): The CSV file to read.

    Returns:
        list[dict[str, str]]: The normalized CSV rows.
    """
    try:
        with csv_path.open(encoding="utf-8", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            return [
                {header: (row.get(header) or "") for header in FAILOVER_CSV_HEADERS}
                for row in reader
            ]
    except OSError as e:
        print(f"  [failover-csv] Failed to read {FAILOVER_CSV_FILE}: {e}")
    except csv.Error as e:
        print(f"  [failover-csv] Failed to parse {FAILOVER_CSV_FILE}: {e}")

    return []


def save_failover_rows(csv_path: pathlib.Path, rows: list[dict[str, str]]) -> None:
    """Persist the full failover CSV contents."""
    try:
        with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FAILOVER_CSV_HEADERS)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as e:
        print(f"  [failover-csv] Failed to write {FAILOVER_CSV_FILE}: {e}")


def find_open_failover_row(rows: list[dict[str, str]]) -> int | None:
    """Return the index of the first open failover row, if present."""
    for index, row in enumerate(rows):
        if not row["End Time"].strip():
            return index
    return None


def update_failover_csv(*, is_primary: bool, observed_at: datetime | None = None) -> None:
    """Open or close a failover CSV record based on the current WAN state."""
    csv_path = ensure_failover_csv_exists()
    if not csv_path.exists():
        return

    timestamp = observed_at or datetime.now().astimezone().replace(microsecond=0)
    rows = load_failover_rows(csv_path)
    open_row_index = find_open_failover_row(rows)

    if not is_primary:
        if open_row_index is not None:
            return

        rows.append(
            {
                "Start Time": format_local_timestamp(timestamp),
                "End Time": "",
                "Duration Hours": "",
            }
        )
        save_failover_rows(csv_path, rows)
        return

    if open_row_index is None:
        return

    rows[open_row_index]["End Time"] = format_local_timestamp(timestamp)

    try:
        start_time = datetime.strptime(
            rows[open_row_index]["Start Time"], LOCAL_DATETIME_FORMAT
        ).replace(tzinfo=timestamp.tzinfo)
    except ValueError as e:
        print(f"  [failover-csv] Failed to parse failover start time: {e}")
        rows[open_row_index]["Duration Hours"] = ""
    else:
        duration_hours = (timestamp - start_time).total_seconds() / 3600
        rows[open_row_index]["Duration Hours"] = f"{duration_hours:.2f}"

    save_failover_rows(csv_path, rows)


def check_wan(*, nbn_ip: str, write_file: bool, send_heartbeat: bool) -> tuple[bool, str, str, bool | None]:
    """Run a single WAN check and optionally persist status and send a heartbeat.

    Args:
        nbn_ip (str): The configured primary WAN IP address.
        write_file (bool): Whether to write the status file.
        send_heartbeat (bool): Whether to send a heartbeat to Better Stack.

    Returns:
        tuple: (is_primary, state, external_ip, heartbeat_ok)
        is_primary (bool): True if on primary WAN, False if on failover.
        state (str): Human-readable state string, e.g. "NBN (primary)" or "5G Backup".
        external_ip (str): The current external IP address.
        heartbeat_ok (bool | None): True if heartbeat succeeded, False if it failed, or None if not sent.
    """
    ip = get_external_ip()
    primary = on_nbn(ip, nbn_ip)
    state = "NBN (primary)" if primary else "5G Backup"

    if write_file:
        write_status(primary, state, ip)
        update_failover_csv(is_primary=primary)

    heartbeat_ok = None
    if send_heartbeat:
        url = HEARTBEAT_PRIMARY if primary else HEARTBEAT_LTE
        if url:
            heartbeat_ok = ping_heartbeat(url)

    return primary, state, ip, heartbeat_ok


def build_parser() -> argparse.ArgumentParser:
    """Create the command line parser for the monitor.

    Returns:
        argparse.ArgumentParser: The configured argument parser.
    """
    parser = argparse.ArgumentParser(description="Monitor WAN failover state.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--onetime",
        action="store_true",
        help="Do one WAN check, write status.json, and exit.",
    )
    mode_group.add_argument(
        "--onprimary",
        action="store_true",
        help='Print "Yes" when on the primary connection, otherwise print "false", and exit.',
    )
    parser.add_argument(
        "--nbn-ip",
        help="Override the primary WAN IP instead of using NBN_STATIC_IP from the environment.",
    )
    parser.add_argument(
        "--noheartbeat",
        action="store_true",
        help="Disable Better Stack heartbeats for this run.",
    )
    return parser


# ── Main loop ─────────────────────────────────────────────────────────────────


def _start_api(host: str):
    """Run the FastAPI server in a background daemon thread."""
    uvicorn.run(app, host=host, port=API_PORT, log_level="warning")


def run_onetime(*, nbn_ip: str) -> int:
    """Run one check, write the status file, and exit.

    Args:
        nbn_ip (str): The configured primary WAN IP address.

    Returns:
        int: Process exit code.
    """
    try:
        _, state, ip, _ = check_wan(nbn_ip=nbn_ip, write_file=True, send_heartbeat=False)
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    print(f"[ok] {state}  ext-ip={ip}")
    return 0


def run_onprimary(*, nbn_ip: str) -> int:
    """Print whether the connection is currently on the primary WAN.

    Args:
        nbn_ip (str): The configured primary WAN IP address.

    Returns:
        int: Process exit code.
    """
    try:
        primary, _, _, _ = check_wan(nbn_ip=nbn_ip, write_file=True, send_heartbeat=False)
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    print("Yes" if primary else "No")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    send_heartbeat = heartbeat_enabled(noheartbeat=args.noheartbeat)
    nbn_ip = args.nbn_ip or NBN_IP

    if not nbn_ip:
        print("Error: NBN_STATIC_IP environment variable is not set and --nbn-ip was not provided.", file=sys.stderr)
        return 1

    if args.onetime:
        return run_onetime(nbn_ip=nbn_ip)

    if args.onprimary:
        return run_onprimary(nbn_ip=nbn_ip)

    if API_IP:
        api_thread = threading.Thread(target=_start_api, args=(API_IP,), daemon=True, name="api")
        api_thread.start()
        print(f"API listening on http://{API_IP}:{API_PORT}/status")
    else:
        print("Status API disabled; set API_IP to enable it.")

    if not send_heartbeat:
        print("Heartbeats disabled.")

    consecutive_errors = 0
    last_state = None  # track state changes for cleaner logging

    print(f"Monitoring started. NBN IP: {nbn_ip}  Interval: {INTERVAL}s")
    print("Press Ctrl-C to stop.")

    try:
        while True:
            try:
                _, state, ip, heartbeat_ok = check_wan(
                    nbn_ip=nbn_ip,
                    write_file=True,
                    send_heartbeat=send_heartbeat,
                )

                if state != last_state:
                    print(f"[transition] → {state}  (external IP: {ip})")
                    last_state = state

                if heartbeat_ok is False:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0
                    print(f"[ok] {state}  ext-ip={ip}")

            except RuntimeError as e:
                consecutive_errors += 1
                print(f"[error] {e}")

            # Gentle back-off: x1 / x2 / x3 interval on repeated errors, then holds
            sleep_for = INTERVAL * min(3, max(1, consecutive_errors))
            time.sleep(sleep_for)

    except KeyboardInterrupt:
        print("\nShutting down.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
