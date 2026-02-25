#!/usr/bin/env python3
"""终端直接查看某 trip 的行程摘要（Must-visit、每日景点）。不需启动 Flask、不需填问卷。"""
import sys
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from app import app, _compute_candidates_and_itinerary
from mysql import db, Trip


def _clean_summary_and_add_pace(summary, pace, daily_capacity):
    """
    清理 summary 中的小时标注，并注入 travel pace 行。
    - 删除 "(x.xh)" / "(xh)"
    - 删除 "Total: x.xh" 行
    - 在 "Trip days: N" 后插入 "Travel pace: <pace>(<capacity> POI)"
    """
    lines = (summary or "").splitlines()
    cleaned_lines = []
    inserted_pace = False

    for line in lines:
        if re.search(r"^\s*Total:\s*\d+(\.\d+)?h\s*$", line):
            continue

        # 删除名称后的时长标注，例如 "Trinity College Dublin (2.0h)"
        line = re.sub(r"\s*\(\d+(\.\d+)?h\)", "", line)
        cleaned_lines.append(line)

        if not inserted_pace and line.startswith("Trip days:"):
            pace_label = str(pace or "balanced")
            cleaned_lines.append(f"Travel pace: {pace_label}({daily_capacity} POI)")
            inserted_pace = True

    if not inserted_pace:
        pace_label = str(pace or "balanced")
        cleaned_lines.insert(0, f"Travel pace: {pace_label}({daily_capacity} POI)")

    return "\n".join(cleaned_lines)

if __name__ == "__main__":
    trip_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if not trip_id:
        print("Usage: python scripts/show_trip_itinerary.py <trip_id>")
        print("Example: python scripts/show_trip_itinerary.py 26")
        sys.exit(1)

    with app.app_context():
        trip = db.session.get(Trip, trip_id)
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

        cleaned_summary = _clean_summary_and_add_pace(
            result.get("summary"),
            getattr(pref, "pace", None),
            result.get("daily_capacity"),
        )
        print(cleaned_summary)
