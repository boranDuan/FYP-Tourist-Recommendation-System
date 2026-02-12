#!/usr/bin/env python3
"""
Step0 人口适配缓存回填：对全量 POI 跑一遍 Step0（儿童 / 老人各一次），
将结果写入 POI.suitable_for_children / POI.suitable_for_seniors。
运行后候选池生成将直接按这两列过滤，不再实时调 Google API。

用法（项目根目录，与 Flask 相同环境）：
  python scripts/backfill_step0_poi_cache.py
  python scripts/backfill_step0_poi_cache.py --no-gmaps   # 仅按 filter 规则，不调 API
"""
import argparse
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from flask import Flask
from mysql import get_database_config, db, POI
from rule_based_filtering import step0_hard_filter


def create_app():
    app = Flask(__name__)
    get_database_config(app)
    return app


class _FakePreference:
    def __init__(self, num_children=0, num_seniors=0):
        self.num_children = num_children
        self.num_seniors = num_seniors


def run(use_gmaps=True):
    app = create_app()
    with app.app_context():
        gmaps = None
        if use_gmaps and os.getenv("GOOGLE_MAPS_API_KEY"):
            try:
                import googlemaps
                gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY"))
            except Exception:
                gmaps = None
        if use_gmaps and not gmaps:
            print("Warning: GOOGLE_MAPS_API_KEY not set or import failed, running without API (filter-only).")

        pois = POI.query.all()
        # 与候选池一致：构造带 filter_ids、score 的 dict 列表
        pois_data = []
        for poi in pois:
            pois_data.append({
                "poi_id": poi.poi_id,
                "name": poi.name,
                "score": 1.0,
                "filter_ids": [f.filter_id for f in poi.filters],
            })

        # 1) 适合儿童：num_children=1, num_seniors=0 跑 Step0，通过则 suitable_for_children=True
        pref_kids = _FakePreference(num_children=1, num_seniors=0)
        passed_kids = {p["poi_id"] for p in step0_hard_filter(pois_data, pref_kids, gmaps=gmaps)}

        # 2) 适合老人：num_children=0, num_seniors=1 跑 Step0，通过则 suitable_for_seniors=True
        pref_seniors = _FakePreference(num_children=0, num_seniors=1)
        passed_seniors = {p["poi_id"] for p in step0_hard_filter(pois_data, pref_seniors, gmaps=gmaps)}

        # 3) 写回 POI 表
        for poi in pois:
            poi.suitable_for_children = poi.poi_id in passed_kids
            poi.suitable_for_seniors = poi.poi_id in passed_seniors
        db.session.commit()
        print(f"Backfill done: {len(pois)} POIs updated.")
        print(f"  suitable_for_children=True: {len(passed_kids)}, suitable_for_seniors=True: {len(passed_seniors)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill POI Step0 cache (suitable_for_children / suitable_for_seniors)")
    parser.add_argument("--no-gmaps", action="store_true", help="Do not call Google API, only use filter rules")
    args = parser.parse_args()
    run(use_gmaps=not args.no_gmaps)
