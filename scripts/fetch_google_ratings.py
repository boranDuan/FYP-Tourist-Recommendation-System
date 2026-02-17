#!/usr/bin/env python3
"""
批量获取 POI 的 Google Places 评分（rating, user_ratings_total）。
只为尚未获取的 POI 调用 API，数据写入 poi 表。
建议：先运行 add_google_fields.py，再运行本脚本。
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from app import app
from mysql import db, POI


def fetch_google_ratings(limit=None, sleep_seconds=0.2, dry_run=False):
    """
    为 google_data_fetched_at IS NULL 的 POI 获取 Google 评分。
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print("❌ GOOGLE_MAPS_API_KEY not set in .env")
        return

    try:
        import googlemaps

        gmaps = googlemaps.Client(key=api_key)
    except ImportError:
        print("❌ pip install googlemaps")
        return

    with app.app_context():
        query = POI.query.filter(POI.google_data_fetched_at.is_(None))
        if limit:
            query = query.limit(limit)
        pois = query.all()

        print(f"Found {len(pois)} POIs without Google data")
        if not pois:
            return

        if dry_run:
            print("Dry run, skipping API calls")
            for p in pois[:5]:
                print(f"  Would fetch: {p.name} (id={p.poi_id})")
            if len(pois) > 5:
                print(f"  ... and {len(pois) - 5} more")
            return

        ok, no_match, err = 0, 0, 0
        for poi in pois:
            try:
                search_query = f"{poi.name}, Dublin, Ireland"
                result = gmaps.find_place(
                    search_query,
                    "textquery",
                    fields=["place_id", "name", "geometry"],
                )
                candidates = result.get("candidates") or []
                if not candidates:
                    poi.google_data_fetched_at = datetime.utcnow()
                    db.session.commit()
                    no_match += 1
                    print(f"⚠️  {poi.name}: No Google match")
                    time.sleep(sleep_seconds)
                    continue

                place_id = candidates[0].get("place_id")
                if not place_id:
                    poi.google_data_fetched_at = datetime.utcnow()
                    db.session.commit()
                    no_match += 1
                    print(f"⚠️  {poi.name}: No place_id")
                    time.sleep(sleep_seconds)
                    continue

                # Place Details 获取 rating, user_ratings_total
                details = gmaps.place(
                    place_id,
                    fields=["rating", "user_ratings_total"],
                )
                detail_result = (details.get("result") or {}) if isinstance(details, dict) else {}

                poi.google_place_id = place_id
                poi.google_rating = detail_result.get("rating")
                poi.google_ratings_total = detail_result.get("user_ratings_total")
                poi.google_data_fetched_at = datetime.utcnow()
                db.session.commit()

                rt = poi.google_rating
                tot = poi.google_ratings_total or 0
                ok += 1
                print(f"✅ {poi.name}: {rt}⭐ ({tot} reviews)")
            except Exception as e:
                err += 1
                db.session.rollback()
                print(f"❌ {poi.name}: {e}")

            time.sleep(sleep_seconds)

        print(f"\nDone: {ok} updated, {no_match} no match, {err} errors")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Google Places ratings for POIs without cached data"
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=None,
        help="Max number of POIs to process (default: all)",
    )
    parser.add_argument(
        "-s",
        "--sleep",
        type=float,
        default=0.2,
        help="Seconds to sleep between API calls (default: 0.2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list POIs to process, no API calls",
    )
    args = parser.parse_args()

    fetch_google_ratings(
        limit=args.limit,
        sleep_seconds=args.sleep,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
