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
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask
from mysql import get_database_config, db, POI, TripPreference
from preference_matching import calculate_poi_score


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
                print(f"Trip {trip_id} 没有找到 TripPreference")
                return
        else:
            pref = TripPreference.query.order_by(TripPreference.updated_at.desc()).first()

        if pref:
            interests = _normalize_interests(pref.interests)
            print(f"Trip ID: {pref.trip_id}")
        else:
            print("数据库中没有 TripPreference，使用默认 interests 演示")
            interests = {"museum": 0.5, "culture": 0.5, "nature": 0.0, "shopping": 0.0, "nightlife": 0.0}

        print("Interests:", json.dumps(interests, ensure_ascii=False))
        print()

        pois = POI.query.all()
        results = []
        for poi in pois:
            poi_filter_ids = [f.filter_id for f in poi.filters]
            score = calculate_poi_score(poi.poi_id, poi_filter_ids, interests)
            results.append((poi, score))

        results.sort(key=lambda x: x[1], reverse=True)
        count_positive = sum(1 for _, s in results if s > 0)
        if limit is not None:
            results = results[:limit]

        print(f"{'poi_id':<8} {'score':<8} {'name'}")
        print("-" * 60)
        for poi, score in results:
            name = (poi.name or "")[:45]
            print(f"{poi.poi_id:<8} {score:<8.2f} {name}")
        print("-" * 60)
        print(f"共 {len(pois)} 个 POI，得分 > 0 的有 {count_positive} 个")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="POI 兴趣得分计算脚本")
    parser.add_argument("-t", "--trip", type=int, help="指定 trip_id，否则使用最新 Trip")
    parser.add_argument("-n", "--limit", type=int, help="只显示前 N 条")
    args = parser.parse_args()
    run(trip_id=args.trip, limit=args.limit)
