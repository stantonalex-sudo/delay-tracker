#!/usr/bin/env python3
"""
Fetch historical delay data from National Rail's HSP API for the last 28 days.
Route: Preston Park (PRP) <-> London Bridge (LBG), all services, all day.

Credentials come from environment variables HSP_USER and HSP_PASS.
Output: delays.json in the repo root. The script is incremental - it only
fetches dates it doesn't already have, then prunes anything older than 28 days.
"""

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

HSP_USER = os.environ.get("HSP_USER")
HSP_PASS = os.environ.get("HSP_PASS")

METRICS_URL = "https://hsp-prod.rockshore.net/api/v1/serviceMetrics"
DETAILS_URL = "https://hsp-prod.rockshore.net/api/v1/serviceDetails"

# Routes to track. Outbound and return, since Delay Repay applies both ways.
# Delete the second tuple if you only want the morning direction.
ROUTES = [
    ("PRP", "LBG"),
    ("LBG", "PRP"),
]

WINDOW_DAYS = 28   # how far back to keep data
LAG_DAYS = 3       # HSP data lags; don't try to fetch the most recent few days
DELAY_THRESHOLD = 15  # minutes late at destination that qualifies for Delay Repay (GTR)

OUT_FILE = Path(__file__).parent / "delays.json"


def days_value(d: date) -> str:
    if d.weekday() <= 4:
        return "WEEKDAY"
    return "SATURDAY" if d.weekday() == 5 else "SUNDAY"


def hsp_post(url: str, body: dict) -> dict:
    r = requests.post(
        url,
        json=body,
        auth=(HSP_USER, HSP_PASS),
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def to_minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def delay_minutes(scheduled: str, actual: str) -> int:
    diff = to_minutes(actual) - to_minutes(scheduled)
    if diff < -720:   # crossed midnight forwards
        diff += 1440
    elif diff > 720:  # actual before scheduled across midnight
        diff -= 1440
    return diff


def fetch_day(d: date, from_loc: str, to_loc: str) -> list[dict]:
    ds = d.strftime("%Y-%m-%d")
    body = {
        "from_loc": from_loc,
        "to_loc": to_loc,
        "from_time": "0000",
        "to_time": "2359",
        "from_date": ds,
        "to_date": ds,
        "days": days_value(d),
    }
    metrics = hsp_post(METRICS_URL, body)
    services = []

    rids = []
    for svc in metrics.get("Services", []):
        attrs = svc.get("serviceAttributesMetrics", {})
        rids.extend(attrs.get("rids", []))

    for rid in rids:
        try:
            detail = hsp_post(DETAILS_URL, {"rid": rid})
        except requests.HTTPError as e:
            print(f"  detail fetch failed for {rid}: {e}", file=sys.stderr)
            continue
        time.sleep(0.25)  # be polite to the API

        attrs = detail.get("serviceAttributesDetails", {})
        locations = attrs.get("locations", [])
        dep = next((l for l in locations if l.get("location") == from_loc), None)
        arr = next((l for l in locations if l.get("location") == to_loc), None)
        if not dep or not arr:
            continue

        sched_dep = dep.get("gbtt_ptd") or ""
        sched_arr = arr.get("gbtt_pta") or ""
        actual_arr = arr.get("actual_ta") or ""
        cancel_reason = arr.get("late_canc_reason") or ""

        cancelled = not actual_arr
        delay = delay_minutes(sched_arr, actual_arr) if (sched_arr and actual_arr) else None

        services.append({
            "date": ds,
            "rid": rid,
            "from": from_loc,
            "to": to_loc,
            "sched_dep": sched_dep,
            "sched_arr": sched_arr,
            "actual_arr": actual_arr,
            "delay_min": delay,
            "cancelled": cancelled,
            "reason_code": cancel_reason,
            "claimable": cancelled or (delay is not None and delay >= DELAY_THRESHOLD),
        })

    return services


def main():
    if not HSP_USER or not HSP_PASS:
        sys.exit("Set HSP_USER and HSP_PASS environment variables.")

    today = date.today()
    newest = today - timedelta(days=LAG_DAYS)
    oldest = today - timedelta(days=WINDOW_DAYS)

    existing = []
    if OUT_FILE.exists():
        existing = json.loads(OUT_FILE.read_text()).get("services", [])

    # keep only records inside the window
    existing = [s for s in existing if s["date"] >= oldest.strftime("%Y-%m-%d")]
    have_dates = {s["date"] for s in existing}

    wanted = []
    d = oldest
    while d <= newest:
        if d.strftime("%Y-%m-%d") not in have_dates:
            wanted.append(d)
        d += timedelta(days=1)

    print(f"Fetching {len(wanted)} missing date(s)...")
    for d in wanted:
        for from_loc, to_loc in ROUTES:
            print(f"  {d} {from_loc}->{to_loc}")
            try:
                existing.extend(fetch_day(d, from_loc, to_loc))
            except Exception as e:
                print(f"  failed for {d} {from_loc}->{to_loc}: {e}", file=sys.stderr)

    existing.sort(key=lambda s: (s["date"], s["sched_dep"]))

    OUT_FILE.write_text(json.dumps({
        "updated": today.isoformat(),
        "window_days": WINDOW_DAYS,
        "threshold_min": DELAY_THRESHOLD,
        "services": existing,
    }, indent=1))

    claimable = sum(1 for s in existing if s["claimable"])
    print(f"Done. {len(existing)} services stored, {claimable} claimable.")


if __name__ == "__main__":
    main()
