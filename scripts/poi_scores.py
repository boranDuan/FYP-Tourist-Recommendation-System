#!/usr/bin/env python3
"""
根据 TripPreference.interests 为所有 POI 计算兴趣得分，输出到终端。

用法（在项目根目录，与启动 Flask 相同环境）：
  python scripts/poi_scores.py              # 使用最新 Trip 的 interests
  python scripts/poi_scores.py --trip 1     # 指定 trip_id
  python scripts/poi_scores.py -n 20        # 只显示前 20 条
"""
import argparse
import sys
import json
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from flask import Flask
from mysql import get_database_config, db, POI, TripPreference, Filter
from preference_matching import calculate_poi_score, resolve_specific_places_to_poi_ids_and_names
from rule_based_filtering import step0_hard_filter, apply_avoid_filter


def create_app():
    app = Flask(__name__)
    get_database_config(app)
    return app


def _normalize_interests(raw):
    """解析并规范化 interests，转为纯 Python 类型（MySQL 可能返回 Decimal/str）"""
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            out[str(k)] = 0.0
    return out


def run(trip_id=None, limit=None):
    app = create_app()
    with app.app_context():
        if trip_id is not None:
            pref = TripPreference.query.filter_by(trip_id=trip_id).first()
            if not pref:
                print(f"Trip {trip_id}: TripPreference not found")
                return
        else:
            pref = TripPreference.query.order_by(TripPreference.updated_at.desc()).first()

        if pref:
            interests = _normalize_interests(pref.interests)
            print(f"Trip ID: {pref.trip_id}")
            nc = (pref.num_children or 0) if hasattr(pref, "num_children") else 0
            ns = (pref.num_seniors or 0) if hasattr(pref, "num_seniors") else 0
            print(f"num_children={nc}, num_seniors={ns}")
            must_visit_raw = (getattr(pref, "specific_places", None) or "").strip()
            if must_visit_raw:
                try:
                    import os
                    import googlemaps
                    gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY")) if os.getenv("GOOGLE_MAPS_API_KEY") else None
                except Exception:
                    gmaps = None
                _, display_pairs = resolve_specific_places_to_poi_ids_and_names(
                    must_visit_raw, POI, db, gmaps_client=gmaps
                )
                for raw, resolved in display_pairs:
                    if resolved == "(unresolved)":
                        print(f"Must visit place: {raw} (unresolved)")
                    else:
                        print(f"Must visit place: {resolved}")
            else:
                print("Must visit place: (none)")
        else:
            print("No TripPreference in DB, using default interests for demo")
            interests = {"museum": 0.5, "culture": 0.5, "nature": 0.0, "shopping": 0.0, "nightlife": 0.0}

        print("Interests:", json.dumps(interests, ensure_ascii=False))
        print()

        # Dublin 中心点与半径（km）
        DUBLIN_CENTER_LAT, DUBLIN_CENTER_LON = 53.3498, -6.2603
        DUBLIN_RADIUS_KM = 30.0
        DUBLIN_COUNTIES = {"Dublin", "Dublin City", "Dún Laoghaire-Rathdown", "Fingal", "South Dublin"}

        pois = POI.query.all()
        results = []
        for poi in pois:
            # 先判断是否在 Dublin 县域或 30km 半径内
            in_county = getattr(poi, "county", None) in DUBLIN_COUNTIES
            in_radius = False
            if poi.latitude is not None and poi.longitude is not None:
                try:
                    # Haversine 距离计算
                    lat1, lon1 = float(poi.latitude), float(poi.longitude)
                    lat2, lon2 = DUBLIN_CENTER_LAT, DUBLIN_CENTER_LON
                    r = 6371.0
                    phi1, phi2 = math.radians(lat1), math.radians(lat2)
                    dphi = math.radians(lat2 - lat1)
                    dlambda = math.radians(lon2 - lon1)
                    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
                    c = 2 * math.asin(math.sqrt(a))
                    dist_km = r * c
                    in_radius = dist_km <= DUBLIN_RADIUS_KM
                except (TypeError, ValueError):
                    in_radius = False

            if not in_county and not in_radius:
                continue

            poi_filter_ids = [f.filter_id for f in poi.filters]
            score = calculate_poi_score(poi.poi_id, poi_filter_ids, interests)
            results.append((poi, score))

        results.sort(key=lambda x: x[1], reverse=True)
        total_in_region = len(results)
        count_positive = sum(1 for _, s in results if s > 0)

        # 只对得分>0 的做人群过滤（与 API 一致），转成带 filter_ids 的 dict 列表
        pois_data = []
        for poi, score in results:
            if score <= 0:
                continue
            pois_data.append({
                "poi_id": poi.poi_id,
                "name": poi.name,
                "score": score,
                "filter_ids": [f.filter_id for f in poi.filters],
            })

        # Step0 人群过滤（与 API 一致）
        num_children = (pref.num_children or 0) if pref else 0
        num_seniors = (pref.num_seniors or 0) if pref else 0
        if num_children > 0 or num_seniors > 0:
            try:
                import os
                import googlemaps
                gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY")) if os.getenv("GOOGLE_MAPS_API_KEY") else None
            except Exception:
                gmaps = None
            pois_data = step0_hard_filter(pois_data, pref, gmaps=gmaps)
            print("Applied Rule-based population filter (Step0)")
        else:
            print("Skipped Rule-based population filter (Step0)")
        count_after_pop_filter = len(pois_data)

        # Step1 避免题过滤
        avoid_list = pref.avoid if pref and pref.avoid and isinstance(pref.avoid, list) else []
        if avoid_list:
            all_filters = [{"id": f.filter_id, "name": f.filter_name} for f in Filter.query.all()]
            print("Applied Rule-based avoid filter (Step1)")
            pois_data = apply_avoid_filter(poi_list=pois_data, avoid_list=avoid_list, all_filters=all_filters, debug=True)
        else:
            print("Skipped Rule-based avoid filter (Step1)")
        count_after_avoid_filter = len(pois_data)

        # 只展示前 limit 条，但总结用全量数字
        display_list = pois_data[:limit] if limit is not None else pois_data

        print(f"{'poi_id':<8} {'score':<8} {'name'}")
        print("-" * 60)
        for p in display_list:
            name = (p.get("name") or "")[:45]
            print(f"{p['poi_id']:<8} {p['score']:<8.2f} {name}")
        print("-" * 60)
        n_display = len(display_list)
        summary = (
            f"Total {total_in_region} POIs (Dublin region), {count_positive} with score > 0 ("
            f"{count_after_pop_filter} after region+population filter, "
            f"{count_after_avoid_filter} after avoid filter"
        )
        if limit is not None and n_display < count_after_avoid_filter:
            summary += f", showing top {n_display}"
        summary += ")"
        print(summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="POI 兴趣得分计算脚本")
    parser.add_argument("-t", "--trip", type=int, help="指定 trip_id，否则使用最新 Trip")
    parser.add_argument("-n", "--limit", type=int, help="只显示前 N 条")
    args = parser.parse_args()
    run(trip_id=args.trip, limit=args.limit)
