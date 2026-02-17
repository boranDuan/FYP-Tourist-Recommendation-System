#!/usr/bin/env python3
"""终端直接查看某 trip 的行程摘要（Must-visit、每日景点）。不需启动 Flask、不需填问卷。"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from app import app, _compute_candidates_and_itinerary
from mysql import Trip

if __name__ == "__main__":
    trip_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if not trip_id:
        print("Usage: python scripts/show_trip_itinerary.py <trip_id>")
        print("Example: python scripts/show_trip_itinerary.py 26")
        sys.exit(1)

    with app.app_context():
        trip = Trip.query.get(trip_id)
        if not trip:
            print(f"Trip {trip_id} not found.")
            sys.exit(1)
        pref = trip.preference
        if not pref:
            print(f"Trip {trip_id} has no preference.")
            sys.exit(1)

        result = _compute_candidates_and_itinerary(pref)
        if not result:
            print("No itinerary (e.g. no positive interests).")
            sys.exit(0)

        # ----- 调试：Must-visit（Google 数据） -----
        google_must_visits = result.get("google_must_visits", [])
        print("=" * 50)
        print("Must-visit（Google Places 直接返回，不匹配本地 DB）")
        print("=" * 50)
        if google_must_visits:
            for g in google_must_visits:
                print(f"  place_id = {g.get('place_id')!r}")
                print(f"  name     = {g.get('name')!r}")
                print(f"  lat/lng  = {g.get('latitude')}, {g.get('longitude')}")
        else:
            print("  (无，或未配置 Google API)")
        print("=" * 50)
        print()

        print(result["summary"])
