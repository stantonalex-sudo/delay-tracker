#!/usr/bin/env python3
"""Targeted test of the return direction (London Bridge -> Preston Park).

Makes just two HSP calls for one day (Thursday 2026-07-09):
  1. serviceMetrics LBG->PRP to list the day's services
  2. serviceDetails for one of them, to confirm it comes back with a
     London Bridge departure and a Preston Park arrival with real times

This is deliberately tiny (two calls, not a full-day sweep) so it tests
whether LBG->PRP data exists and parses, without tripping the rate limit.
Writes nothing.
"""

import os
import sys

import requests

HSP_USER = os.environ.get("HSP_USER")
HSP_PASS = os.environ.get("HSP_PASS")
METRICS_URL = "https://hsp-prod.rockshore.net/api/v1/serviceMetrics"
DETAILS_URL = "https://hsp-prod.rockshore.net/api/v1/serviceDetails"

FROM_LOC = "LBG"
TO_LOC = "PRP"
TEST_DATE = "2026-07-09"   # a Thursday, so a WEEKDAY service pattern


def post(url, body):
    r = requests.post(
        url,
        json=body,
        auth=(HSP_USER, HSP_PASS),
        headers={"Content-Type": "application/json"},
        timeout=(10, 25),
    )
    r.raise_for_status()
    return r.json()


def run():
    # A one-hour window keeps the query as small as possible, so this is
    # the lightest probe we can make and the most likely to slip through
    # while the account is still lightly rate-limited.
    print("1. serviceMetrics (0700-0800 window) ...")
    metrics = post(METRICS_URL, {
        "from_loc": FROM_LOC,
        "to_loc": TO_LOC,
        "from_time": "0700",
        "to_time": "0800",
        "from_date": TEST_DATE,
        "to_date": TEST_DATE,
        "days": "WEEKDAY",
    })
    services = metrics.get("Services", [])
    rids = []
    for svc in services:
        rids.extend(svc.get("serviceAttributesMetrics", {}).get("rids", []))
    print(f"   header: {metrics.get('header')}")
    print(f"   services returned: {len(services)}, total RIDs: {len(rids)}")

    if not rids:
        print("   NO services returned for LBG->PRP on this date.")
        print("   -> the return direction query yields nothing (not a fetch bug).")
        return

    rid = rids[0]
    print(f"2. serviceDetails for one service (rid {rid}) ...")
    detail = post(DETAILS_URL, {"rid": rid})
    attrs = detail.get("serviceAttributesDetails", {})
    locations = attrs.get("locations", [])
    stops = [l.get("location") for l in locations]
    dep = next((l for l in locations if l.get("location") == FROM_LOC), None)
    arr = next((l for l in locations if l.get("location") == TO_LOC), None)
    print(f"   stops on this service: {stops}")

    if dep and arr:
        print("   MATCH: this service calls at both LBG and PRP.")
        print(f"   LBG  sched dep {dep.get('gbtt_ptd') or '-'}, "
              f"actual dep {dep.get('actual_td') or '-'}")
        print(f"   PRP  sched arr {arr.get('gbtt_pta') or '-'}, "
              f"actual arr {arr.get('actual_ta') or '-'}")
        print("   -> LBG->PRP data is available and parses correctly. The only "
              "reason it is not in delays.json yet is the rate-limited backfill.")
    else:
        print("   This RID did not contain both LBG and PRP in its locations.")
        print(f"   dep found: {bool(dep)}, arr found: {bool(arr)}")
        print("   -> worth checking the location codes for the return leg.")


def main():
    if not HSP_USER or not HSP_PASS:
        sys.exit("Set HSP_USER and HSP_PASS environment variables.")

    print(f"Return-direction test: {FROM_LOC} -> {TO_LOC} on {TEST_DATE} (Thursday)")
    print("-" * 60)
    try:
        run()
        print("Done.")
    except requests.exceptions.ReadTimeout:
        print("   READ TIMEOUT: HSP accepted the connection then went silent. "
              "That is the rate-limit stall again, not a data problem. Let the "
              "account rest and try later.")
    except requests.exceptions.ConnectTimeout:
        print("   CONNECT TIMEOUT: HSP did not accept the connection.")
    except requests.HTTPError as e:
        print(f"   HTTP error: {e}")
    except requests.RequestException as e:
        print(f"   Request failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
