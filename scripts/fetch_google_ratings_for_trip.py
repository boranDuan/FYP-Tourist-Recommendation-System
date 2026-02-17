#!/usr/bin/env python3
"""
针对特定 Trip 的 POI List 获取 Google 评分并保存到数据库。

执行顺序：
  1. 先运行 poi_scores.py 生成/预览 POI List
  2. 本脚本：只对该 Trip 的 POI（score >= threshold）拉取 Google 评分
  3. 再运行 poi_scores / show_trip_itinerary 会使用已缓存的 Google 评分

用法：
  python scripts/fetch_google_ratings_for_trip.py --trip_id 21 --score_threshold 0.6 --dry-run
  python scripts/fetch_google_ratings_for_trip.py --trip_id 21 --score_threshold 0.6
"""
import argparse
import json
import math
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
from mysql import db, POI, Trip, TripPreference, Filter
from preference_matching import calculate_poi_score
from rule_based_filtering import step0_hard_filter, apply_avoid_filter


def _normalize_interests(raw):
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): float(v) if v is not None else 0.0 for k, v in raw.items()}


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return r * c


def get_poi_candidates_for_trip(trip_id):
    """
    获取指定 Trip 的 POI 候选列表（与 _compute_candidates_and_itinerary 一致）。
    返回 [(poi_obj, score), ...]，按 score 降序。
    """
    trip = Trip.query.get(trip_id)
    if not trip:
        return None, None
    pref = trip.preference
    if not pref:
        return None, pref

    interests = _normalize_interests(pref.interests)
    if not interests or not any(v and float(v) > 0 for v in interests.values()):
        return [], pref

    DUBLIN_CENTER_LAT, DUBLIN_CENTER_LON = 53.3498, -6.2603
    DUBLIN_RADIUS_KM = 30.0
    DUBLIN_COUNTIES = {"Dublin", "Dublin City", "Dún Laoghaire-Rathdown", "Fingal", "South Dublin"}

    pois = POI.query.all()
    scored = []
    for poi in pois:
        in_county = getattr(poi, "county", None) in DUBLIN_COUNTIES
        in_radius = False
        if poi.latitude is not None and poi.longitude is not None:
            try:
                dist = _haversine_km(
                    float(poi.latitude), float(poi.longitude),
                    DUBLIN_CENTER_LAT, DUBLIN_CENTER_LON,
                )
                in_radius = dist <= DUBLIN_RADIUS_KM
            except (TypeError, ValueError):
                pass
        if not in_county and not in_radius:
            continue
        poi_filter_ids = [f.filter_id for f in poi.filters]
        score = calculate_poi_score(poi.poi_id, poi_filter_ids, interests)
        if score > 0:
            scored.append((poi, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    pois_data = []
    for poi, score in scored:
        pois_data.append({
            "poi_id": poi.poi_id,
            "poi_obj": poi,
            "name": poi.name,
            "score": score,
            "filter_ids": [f.filter_id for f in poi.filters],
        })

    num_children = (pref.num_children or 0) if pref else 0
    num_seniors = (pref.num_seniors or 0) if pref else 0
    if num_children > 0 or num_seniors > 0:
        try:
            import googlemaps
            gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY")) if os.getenv("GOOGLE_MAPS_API_KEY") else None
        except Exception:
            gmaps = None
        pois_data = step0_hard_filter(pois_data, pref, gmaps=gmaps)

    avoid_list = pref.avoid if pref and pref.avoid and isinstance(pref.avoid, list) else []
    if avoid_list:
        all_filters = [{"id": f.filter_id, "name": f.filter_name} for f in Filter.query.all()]
        pois_data = apply_avoid_filter(poi_list=pois_data, avoid_list=avoid_list, all_filters=all_filters)

    return [(p["poi_obj"], p["score"]) for p in pois_data], pref


def fetch_google_ratings_for_trip(trip_id, score_threshold=0.6, dry_run=False, sleep_seconds=0.2):
    with app.app_context():
        candidates, pref = get_poi_candidates_for_trip(trip_id)
        if candidates is None:
            print(f"❌ Trip {trip_id} not found or has no preference")
            return
        if pref:
            print(f"Trip ID: {trip_id}")
            print(f"Interests: {pref.interests}")
            print(f"Avoid: {pref.avoid}")
        print()

        above_threshold = [(poi, score) for poi, score in candidates if score >= score_threshold]
        pois_to_fetch = [
            (poi, score) for poi, score in above_threshold
            if getattr(poi, "google_data_fetched_at", None) is None
        ]

        print(f"📊 Found {len(candidates)} total POI candidates")
        print(f"📊 {len(above_threshold)} POIs with score >= {score_threshold}")
        print(f"📊 {len(pois_to_fetch)} POIs need Google data\n")

        if dry_run:
            print("🔍 DRY RUN - Would fetch Google data for:")
            for poi, score in pois_to_fetch[:20]:
                print(f"  - {poi.name} (score: {score:.2f})")
            if len(pois_to_fetch) > 20:
                print(f"  ... and {len(pois_to_fetch) - 20} more")
            return

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

        success_count = 0
        no_match_count = 0
        err_count = 0

        for i, (poi, score) in enumerate(pois_to_fetch, 1):
            try:
                query = f"{poi.name}, Dublin, Ireland"
                find_result = gmaps.find_place(
                    input=query,
                    input_type="textquery",
                    fields=["place_id", "name"],
                )
                candidates_list = find_result.get("candidates") or []
                if not candidates_list:
                    poi.google_data_fetched_at = datetime.utcnow()
                    db.session.commit()
                    print(f"[{i}/{len(pois_to_fetch)}] ⚠️  {poi.name}: No Google match")
                    no_match_count += 1
                    time.sleep(sleep_seconds)
                    continue

                place_id = candidates_list[0].get("place_id")
                if not place_id:
                    poi.google_data_fetched_at = datetime.utcnow()
                    db.session.commit()
                    no_match_count += 1
                    time.sleep(sleep_seconds)
                    continue

                details = gmaps.place(
                    place_id,
                    fields=["rating", "user_ratings_total"],
                )
                result = (details.get("result") or {}) if isinstance(details, dict) else {}
                rating = result.get("rating")
                ratings_total = result.get("user_ratings_total")

                poi.google_place_id = place_id
                poi.google_rating = rating
                poi.google_ratings_total = ratings_total
                poi.google_data_fetched_at = datetime.utcnow()
                db.session.commit()

                rt = rating if rating is not None else "?"
                tot = ratings_total if ratings_total is not None else 0
                print(f"[{i}/{len(pois_to_fetch)}] ✅ {poi.name}: {rt}⭐ ({tot} reviews) [score: {score:.2f}]")
                success_count += 1
            except Exception as e:
                db.session.rollback()
                print(f"[{i}/{len(pois_to_fetch)}] ❌ {poi.name}: {e}")
                err_count += 1

            time.sleep(sleep_seconds)

        print(f"\n🎉 Complete!")
        print(f"✅ Success: {success_count}")
        print(f"⚠️  No match: {no_match_count}")
        print(f"❌ Failed: {err_count}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Google ratings for a specific trip's POI list",
    )
    parser.add_argument("--trip_id", type=int, required=True, help="Trip ID")
    parser.add_argument(
        "--score_threshold",
        type=float,
        default=0.6,
        help="Minimum interest score (default: 0.6)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without fetching")
    parser.add_argument("-s", "--sleep", type=float, default=0.2, help="Sleep seconds between requests")
    args = parser.parse_args()

    fetch_google_ratings_for_trip(
        trip_id=args.trip_id,
        score_threshold=args.score_threshold,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep,
    )


if __name__ == "__main__":
    main()
