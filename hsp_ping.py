#!/usr/bin/env python3
"""One-shot HSP connectivity diagnostic.

Makes a single serviceMetrics call and reports timing so we can tell
apart the two failure modes we might be seeing:

  - connect succeeds but the read hangs  -> application-level throttle or
    block (HSP accepts the TCP/TLS connection then never replies)
  - connect itself times out or is refused -> network/firewall IP block

Prints raw status, elapsed seconds, and a body snippet. Does not retry,
does not write any files. Safe to run any time.
"""

import os
import socket
import ssl
import sys
import time

import requests

HSP_USER = os.environ.get("HSP_USER")
HSP_PASS = os.environ.get("HSP_PASS")
HOST = "hsp-prod.rockshore.net"
METRICS_URL = f"https://{HOST}/api/v1/serviceMetrics"


def tls_handshake_timing() -> None:
    """Time a bare TCP + TLS connect, independent of the HTTP request."""
    print(f"1. Raw TCP+TLS connect to {HOST}:443 ...")
    start = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((HOST, 443), timeout=15) as sock:
            tcp = time.monotonic() - start
            with ctx.wrap_socket(sock, server_hostname=HOST):
                tls = time.monotonic() - start
        print(f"   OK: TCP {tcp:.2f}s, TLS {tls:.2f}s (connection layer is fine)")
    except Exception as e:
        print(f"   FAILED after {time.monotonic() - start:.2f}s: "
              f"{type(e).__name__}: {e}")
        print("   -> a failure here points to a network/firewall IP block.")


def single_metrics_call() -> None:
    """One serviceMetrics POST, short timeouts, connect timed separately."""
    print("2. Single serviceMetrics POST (connect timeout 10s, read 25s) ...")
    body = {
        "from_loc": "PRP",
        "to_loc": "LBG",
        "from_time": "0700",
        "to_time": "0800",
        "from_date": "2026-07-01",
        "to_date": "2026-07-01",
        "days": "WEEKDAY",
    }
    start = time.monotonic()
    try:
        r = requests.post(
            METRICS_URL,
            json=body,
            auth=(HSP_USER, HSP_PASS),
            headers={"Content-Type": "application/json"},
            timeout=(10, 25),  # (connect, read)
        )
        elapsed = time.monotonic() - start
        print(f"   HTTP {r.status_code} in {elapsed:.2f}s")
        snippet = r.text[:300].replace("\n", " ")
        print(f"   body[:300]: {snippet}")
    except requests.exceptions.ConnectTimeout:
        print(f"   CONNECT TIMEOUT after {time.monotonic() - start:.2f}s "
              "-> firewall/IP block: HSP is not accepting our connection.")
    except requests.exceptions.ReadTimeout:
        print(f"   READ TIMEOUT after {time.monotonic() - start:.2f}s "
              "-> app-level block: HSP accepts the connection then stays "
              "silent. Classic rate-limit/throttle response, not an IP block.")
    except Exception as e:
        print(f"   {type(e).__name__} after {time.monotonic() - start:.2f}s: {e}")


def main() -> None:
    if not HSP_USER or not HSP_PASS:
        sys.exit("Set HSP_USER and HSP_PASS environment variables.")
    print("HSP connectivity diagnostic")
    print("---------------------------")
    tls_handshake_timing()
    single_metrics_call()
    print("Done.")


if __name__ == "__main__":
    main()
