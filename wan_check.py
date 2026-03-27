#!/usr/bin/env python3
"""
Failover Monitor — checks whether the home network is on NBN or 5G failover.

By comparing the current external IP against the known static NBN IP.

Environment variables:
  NBN_STATIC_IP        Your static NBN IP (default: 180.150.43.236)
  HEARTBEAT_PRIMARY    Better Stack heartbeat URL for NBN-up state
  HEARTBEAT_LTE        Better Stack heartbeat URL for 5G-failover state
  MONITOR_INTERVAL     Polling interval in seconds (default: 60)
"""

import os
import time

import requests

# ── Config ────────────────────────────────────────────────────────────────────

NBN_IP = os.getenv("NBN_STATIC_IP", "192.168.0.1.1")
HEARTBEAT_PRIMARY = os.getenv("HEARTBEAT_PRIMARY", "https://uptime.betterstack.com/api/v1/heartbeat/<YOUR_PRIMARY_ID>")
HEARTBEAT_LTE = os.getenv("HEARTBEAT_LTE", "https://uptime.betterstack.com/api/v1/heartbeat/<YOUR_LTE_ID>")
INTERVAL = int(os.getenv("MONITOR_INTERVAL", "60"))
HTTP_TIMEOUT = 8

UA = {"User-Agent": "failover-monitor/1.0"}

# Multiple IP-echo services, tried in order — protects against any one being down.
IP_ECHO_URLS = [
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
    "https://checkip.amazonaws.com",
]

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


def on_nbn(external_ip: str) -> bool:
    """True when the current external IP matches our static NBN address.

    Args:
        external_ip (str): The current external IP address to check.

    Returns:
        bool: True if the external IP matches the NBN static IP, False otherwise.
    """
    return external_ip == NBN_IP


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

# ── Main loop ─────────────────────────────────────────────────────────────────


def main():
    consecutive_errors = 0
    last_state = None  # track state changes for cleaner logging

    print(f"Monitoring started. NBN IP: {NBN_IP}  Interval: {INTERVAL}s")

    while True:
        try:
            ip = get_external_ip()
            primary = on_nbn(ip)
            state = "NBN (primary)" if primary else "5G FAILOVER (Optus)"
            url = HEARTBEAT_PRIMARY if primary else HEARTBEAT_LTE

            if state != last_state:
                print(f"[transition] → {state}  (external IP: {ip})")
                last_state = state

            ok = ping_heartbeat(url)

            if ok:
                consecutive_errors = 0
                print(f"[ok] {state}  ext-ip={ip}")
            else:
                consecutive_errors += 1

        except RuntimeError as e:
            consecutive_errors += 1
            print(f"[error] {e}")

        # Gentle back-off: x1 / x2 / x3 interval on repeated errors, then holds
        sleep_for = INTERVAL * min(3, max(1, consecutive_errors))
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
