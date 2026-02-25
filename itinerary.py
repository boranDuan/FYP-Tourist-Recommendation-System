"""
Itinerary 模块：行程相关配置，如 filter 时间分类与时长。
"""

FILTER_TIME_CATEGORY = {
    # ========== HEAVY (2.0h) ==========
    19: 'HEAVY',  # Museums and Attraction
    43: 'HEAVY',  # Castle
    18: 'HEAVY',  # Historic Houses and Castle
    95: 'HEAVY',  # Zoos and Aquarium
    67: 'HEAVY',  # Offshore Island
    68: 'HEAVY',  # Adventure Center
    57: 'HEAVY',  # Visitor Farm

    # ========== MEDIUM (1.5h) ==========
    37: 'MEDIUM',  # Art Gallery
    38: 'MEDIUM',  # Garden
    24: 'MEDIUM',  # Churches
    25: 'MEDIUM',  # Church Abbey
    26: 'MEDIUM',  # Abbeys and Monastery
    23: 'MEDIUM',  # Monastery
    89: 'MEDIUM',  # National Park
    39: 'MEDIUM',  # Natural Landscape
    91: 'MEDIUM',  # Discovery Point
    74: 'MEDIUM',  # Forest Park
    14: 'MEDIUM',  # Learning
    80: 'MEDIUM',  # Literary Ireland
    79: 'MEDIUM',  # Cinema
    84: 'MEDIUM',  # Movies
    94: 'MEDIUM',  # Gaa
    88: 'MEDIUM',  # Race Course
    20: 'MEDIUM',  # Golf
    21: 'MEDIUM',  # Golf Course
    61: 'MEDIUM',  # Sports Venues
    4: 'MEDIUM',   # Activity Operator
    7: 'MEDIUM',   # Tour
    64: 'MEDIUM',  # Day Tour
    56: 'MEDIUM',  # Food Trails and Tour
    65: 'MEDIUM',  # Tracing Your Ancestors
    8: 'MEDIUM',   # Walking
    42: 'MEDIUM',  # Kayaking
    54: 'MEDIUM',  # Sailing
    55: 'MEDIUM',  # Surfing
    77: 'MEDIUM',  # Kitesurfing
    78: 'MEDIUM',  # Windsurfing
    87: 'MEDIUM',  # Zip Lining
    63: 'MEDIUM',  # Climbing
    58: 'MEDIUM',  # Equestrian
    59: 'MEDIUM',  # Horse Riding
    53: 'MEDIUM',  # Cruising
    81: 'MEDIUM',  # Cookery
    82: 'MEDIUM',  # Cooking

    # ========== LIGHT (1.0h) ==========
    22: 'LIGHT',   # Beach
    41: 'LIGHT',   # Ruins
    66: 'LIGHT',   # Public Park
    75: 'LIGHT',   # Park and Forest Walk
    52: 'LIGHT',   # Public Sculpture
    9: 'LIGHT',    # Shopping
    15: 'LIGHT',   # Shopping Centres and Department Store
    17: 'LIGHT',   # Craft
    44: 'LIGHT',   # Artisan
    12: 'LIGHT',   # Food Shops
    85: 'LIGHT',   # Photography
    40: 'LIGHT',   # Music
    16: 'LIGHT',   # Venue
    47: 'LIGHT',   # Bird Watching
    69: 'LIGHT',   # Gardening
    71: 'LIGHT',   # Traditionally Irish
    28: 'LIGHT',   # Local Produce
    92: 'LIGHT',   # Pitch and Putt
    73: 'LIGHT',   # Comedy
    97: 'LIGHT',   # Banquet
    90: 'LIGHT',   # River
    86: 'LIGHT',   # Marina
    11: 'LIGHT',   # Nature and Wildlife
    29: 'LIGHT',   # Cycling
    34: 'LIGHT',   # Angling
    35: 'LIGHT',   # Fishing
    27: 'LIGHT',   # Boat
    76: 'LIGHT',   # Swimming Pool
    45: 'LIGHT',   # Swimming Pools and Water Park
    93: 'LIGHT',   # Falconry
    46: 'LIGHT',   # Bike Rental
    70: 'LIGHT',   # Agriculture
    62: 'RELAX',   # Spa
    49: 'RELAX',   # Spa and Wellness

    # ========== RELAX (0.5h) ==========
    48: 'RELAX',   # Pampering
    50: 'RELAX',   # Health Farm
    51: 'RELAX',   # Specialised Retreat
    72: 'RELAX',   # Fitness and Leisure
}

CATEGORY_DURATION = {
    'HEAVY': 2.0,
    'MEDIUM': 1.5,
    'LIGHT': 1.0,
    'RELAX': 0.5,
}

# Pace → 每日最大游玩时长（h）
MAX_DAY_HOURS = {
    'relaxed': 5.0,
    'balanced': 6.5,
    'intensive': 8.0,
}

# 允许超出的时长（h），使分配更灵活
FLEXIBLE_BUFFER = {
    'relaxed': 0.5,
    'balanced': 1.0,
    'intensive': 1.5,
}


def get_poi_duration(poi):
    """根据 POI 的 filter_ids 取最重类别对应的时长（h）。Google must-visit 若含 duration 则直接返回。"""
    if poi.get("duration") is not None:
        try:
            return float(poi["duration"])
        except (TypeError, ValueError):
            pass
    filter_ids = poi.get('filter_ids') or []
    max_dur = 0.0
    for fid in filter_ids:
        cat = FILTER_TIME_CATEGORY.get(fid)
        dur = CATEGORY_DURATION.get(cat, 1.0) if cat else 1.0
        max_dur = max(max_dur, dur)
    return max_dur if max_dur > 0 else 1.0


def _haversine_km(lat1, lon1, lat2, lon2):
    """Haversine 距离（km）"""
    import math
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return r * c


def optimize_route_greedy_tsp(day_pois):
    """贪心 TSP：从离当日中心最近点开始，每次选最近的下一个未访问点。"""
    if len(day_pois) <= 2:
        return day_pois
    center = _center_of_pois(day_pois)
    if center is None:
        ordered = sorted(day_pois, key=lambda p: (p.get('longitude') or 0))
        start = ordered[0]
    else:
        start = min(day_pois, key=lambda p: _poi_distance_to_point(p, center[0], center[1]))
    path = [start]
    remaining = [p for p in day_pois if p is not start]
    while remaining:
        last = path[-1]
        lat1, lon1 = last.get('latitude') or 0, last.get('longitude') or 0
        best_idx = 0
        best_dist = 1e9
        for i, p in enumerate(remaining):
            lat2, lon2 = p.get('latitude') or 0, p.get('longitude') or 0
            d = _haversine_km(lat1, lon1, lat2, lon2)
            if d < best_dist:
                best_dist = d
                best_idx = i
        path.append(remaining.pop(best_idx))
    return path


def _poi_distance_to_point(poi, lat, lng):
    """POI 到某点的距离（km）。"""
    pla = poi.get("latitude")
    plo = poi.get("longitude")
    if pla is None or plo is None:
        return 1e9
    return _haversine_km(float(pla), float(plo), lat, lng)


def _center_of_pois(pois):
    """POI 列表的地理中心 (lat, lng)。"""
    if not pois:
        return None
    lats = []
    lngs = []
    for p in pois:
        la, lo = p.get("latitude"), p.get("longitude")
        if la is not None and lo is not None:
            lats.append(float(la))
            lngs.append(float(lo))
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


def improved_geographic_clustering(selected_pois, trip_days):
    """
    改进版地理聚类：考虑 Dublin 实际地理区域，将距离接近的景点安排在同一天。
    """
    import numpy as np

    if len(selected_pois) < trip_days:
        return list(range(len(selected_pois)))

    coords = np.array([
        [float(p.get('latitude') or 0), float(p.get('longitude') or 0)]
        for p in selected_pois
    ])

    # 预定义 Dublin 的地理区域中心点
    DUBLIN_REGIONS = [
        (53.3498, -6.2603),   # city_center
        (53.5200, -6.1500),   # north_coast
        (53.1800, -6.1800),   # south_wicklow
        (53.3500, -6.5500),   # west_kildare
    ]
    n_regions = min(trip_days, len(DUBLIN_REGIONS))
    region_centers = DUBLIN_REGIONS[:n_regions]

    try:
        # 若 trip_days <= 4，让每天对应一个区域：POI 分配到最近的区域中心
        if trip_days <= 4 and n_regions >= trip_days:
            poi_regions = []
            for i in range(len(coords)):
                lat, lng = coords[i, 0], coords[i, 1]
                distances = [
                    _haversine_km(lat, lng, rc[0], rc[1])
                    for rc in region_centers
                ]
                poi_regions.append(int(np.argmin(distances)))
            unique_regions = set(poi_regions)
            # 若某些区域无 POI 或区域数不足，用 KMeans 补充
            if len(unique_regions) < trip_days or max(poi_regions) >= trip_days:
                from sklearn.cluster import KMeans
                kmeans = KMeans(n_clusters=trip_days, random_state=42, n_init=10)
                return kmeans.fit_predict(coords).tolist()
            return poi_regions
        else:
            from sklearn.cluster import KMeans
            kmeans = KMeans(n_clusters=trip_days, random_state=42, n_init=10)
            return kmeans.fit_predict(coords).tolist()
    except ImportError:
        return [i % trip_days for i in range(len(selected_pois))]


def allocate_pois_to_days_v3_with_must_visit(pois, must_visit_identifiers, preference, trip_days):
    """
    改进版：分层 + 地理 + Must-visit 中心。
    - 有 Must-visit 的天：在其附近选高分 POI
    - 无 Must-visit 的天：地理聚类
    - 时长允许灵活超标（buffer）
    支持 Google place_id 与本地 poi_id。
    """
    warnings = []
    must_visit_set = set(must_visit_identifiers or [])

    for poi in pois:
        identifier = poi.get("place_id") or poi.get("poi_id")
        poi["is_must_visit"] = identifier is not None and identifier in must_visit_set

    for poi in pois:
        if poi["is_must_visit"]:
            poi["effective_score"] = 999.0
        else:
            poi["effective_score"] = poi.get("score", 0.0) or 0.0

    must_visit_pois = [p for p in pois if p["is_must_visit"]]
    remaining_pois = [p for p in pois if not p["is_must_visit"]]

    pace = getattr(preference, "pace", None) or "balanced"
    daily_hours = MAX_DAY_HOURS.get(pace, 6.5)
    buffer = FLEXIBLE_BUFFER.get(pace, 1.0)
    max_day_hours = daily_hours + buffer

    must_visit_hours = sum(get_poi_duration(p) for p in must_visit_pois)
    base_budget = daily_hours * trip_days
    if must_visit_hours > base_budget:
        adjusted_daily = min(8.0, must_visit_hours / trip_days)
        max_day_hours = min(max_day_hours, adjusted_daily + buffer)
        warnings.append(
            f"Must-visit POIs require {must_visit_hours:.1f}h, adjusted daily pace to {adjusted_daily:.1f}h"
        )

    # Step 1: Must-visit 分配到天（地理聚类，允许相近的 must-visit 同一天）
    n_must_clusters = min(trip_days, max(1, (len(must_visit_pois) + 1) // 2))
    must_visit_clusters = (
        improved_geographic_clustering(must_visit_pois, n_must_clusters)
        if must_visit_pois
        else []
    )
    day_plans = [{"day": i + 1, "must_visit": [], "recommended": []} for i in range(trip_days)]
    for i, mvp in enumerate(must_visit_pois):
        c = must_visit_clusters[i] if i < len(must_visit_clusters) else i % trip_days
        if c < trip_days:
            day_plans[c]["must_visit"].append(mvp)

    used_ids = set()
    for p in must_visit_pois:
        uid = p.get("place_id") or p.get("poi_id")
        if uid is not None:
            used_ids.add(uid)

    # Step 2 + Step 3: 固定 anchor + 最近邻扩展（不漂移、不跨城）
    _nearest_neighbor_fill(day_plans, remaining_pois, used_ids, max_day_hours)

    # Step 3.5: 轻量跨天优化（相邻天 swap）
    _optimize_adjacent_day_swap(day_plans, max_day_hours)

    # Step 4: 每天合并 must_visit + recommended，TSP 优化
    result_plans = []
    for plan in day_plans:
        all_pois = plan["must_visit"] + plan["recommended"]
        if not all_pois:
            continue
        optimized = optimize_route_greedy_tsp(all_pois)
        day_dur = sum(get_poi_duration(p) for p in optimized)
        must_count = sum(1 for p in optimized if p["is_must_visit"])
        result_plans.append({
            "day": plan["day"],
            "pois": optimized,
            "total_hours": round(day_dur, 1),
            "must_visit_count": must_count,
        })

    return result_plans, warnings


# 地理软约束：超过此距离（km）时降低分数，但不排除
MAX_DISTANCE_SOFT_KM = 15.0
DISTANCE_PENALTY = 0.7
MAX_DAY_RADIUS_KM = 10.0
DUBLIN_CENTER = (53.3498, -6.2603)
MAX_CITY_RADIUS_KM = 8.0


def _nearest_neighbor_fill(day_plans, remaining_pois, used_ids, max_day_hours):
    """
    固定 Day Anchor + 最近邻扩展：
    - 有 must-visit 的天：anchor = must-visit 中心
    - 无 must-visit 的天：anchor = Dublin city center
    - 只填充 anchor 半径内的 POI，避免跨城漂移
    """
    for plan in day_plans:
        anchor = _center_of_pois(plan["must_visit"]) if plan["must_visit"] else DUBLIN_CENTER
        if anchor is None:
            continue

        day_dur = sum(get_poi_duration(p) for p in plan["must_visit"])

        candidates = [
            p for p in remaining_pois
            if (p.get("place_id") or p.get("poi_id")) not in used_ids
        ]
        candidates.sort(key=lambda p: _poi_distance_to_point(p, anchor[0], anchor[1]))

        for poi in candidates:
            uid = poi.get("place_id") or poi.get("poi_id")
            if uid in used_ids:
                continue

            # City Bias: 超出都柏林中心半径则跳过
            city_dist = _poi_distance_to_point(poi, DUBLIN_CENTER[0], DUBLIN_CENTER[1])
            if city_dist > MAX_CITY_RADIUS_KM:
                continue

            dist = _poi_distance_to_point(poi, anchor[0], anchor[1])
            if dist > MAX_DAY_RADIUS_KM:
                break

            dur = get_poi_duration(poi)
            if day_dur + dur > max_day_hours:
                continue

            plan["recommended"].append(poi)
            if uid is not None:
                used_ids.add(uid)
            day_dur += dur


def _optimize_adjacent_day_swap(day_plans, max_day_hours):
    """
    轻量跨天优化（仅相邻天）：
    - 只移动 recommended（不移动 must-visit）
    - 若 POI 距离前一天中心更近，且前一天时长允许，则从 Day(i+1) 移到 Day(i)
    """
    if not day_plans or len(day_plans) < 2:
        return

    def _plan_center(plan):
        all_pois = (plan.get("must_visit") or []) + (plan.get("recommended") or [])
        return _center_of_pois(all_pois)

    def _plan_duration(plan):
        all_pois = (plan.get("must_visit") or []) + (plan.get("recommended") or [])
        return sum(get_poi_duration(p) for p in all_pois)

    for i in range(1, len(day_plans)):
        prev_plan = day_plans[i - 1]
        curr_plan = day_plans[i]
        movable = list(curr_plan.get("recommended") or [])

        for poi in movable:
            prev_center = _plan_center(prev_plan)
            curr_center = _plan_center(curr_plan)
            if prev_center is None or curr_center is None:
                continue

            d_prev = _poi_distance_to_point(poi, prev_center[0], prev_center[1])
            d_curr = _poi_distance_to_point(poi, curr_center[0], curr_center[1])
            if d_prev >= d_curr:
                continue

            poi_dur = get_poi_duration(poi)
            if _plan_duration(prev_plan) + poi_dur > max_day_hours:
                continue

            # move_to_Day(i)_if_time_allows
            curr_plan["recommended"].remove(poi)
            prev_plan["recommended"].append(poi)


def allocate_pois_to_days_v4_popularity_first(pois, must_visit_identifiers, preference, trip_days):
    """
    知名度优先 + 地理软约束。
    - Must-visit 分散到不同天（轮流分配）
    - 按综合评分（兴趣+知名度）排序，地理作为软约束（>15km 降分但不排除）
    """
    warnings = []
    must_visit_set = set(must_visit_identifiers or [])

    for poi in pois:
        identifier = poi.get("place_id") or poi.get("poi_id")
        poi["is_must_visit"] = identifier is not None and identifier in must_visit_set

    for poi in pois:
        if poi["is_must_visit"]:
            poi["final_score"] = 999.0
        else:
            poi["final_score"] = poi.get("score", 0.0) or 0.0

    must_visit_pois = [p for p in pois if p["is_must_visit"]]
    remaining_pois = sorted(
        [p for p in pois if not p["is_must_visit"]],
        key=lambda p: p["final_score"],
        reverse=True,
    )

    pace = getattr(preference, "pace", None) or "balanced"
    daily_hours = MAX_DAY_HOURS.get(pace, 6.5)
    buffer = FLEXIBLE_BUFFER.get(pace, 1.0)
    max_day_hours = daily_hours + buffer

    must_visit_hours = sum(get_poi_duration(p) for p in must_visit_pois)
    base_budget = daily_hours * trip_days
    if must_visit_hours > base_budget:
        adjusted_daily = min(8.0, must_visit_hours / trip_days)
        max_day_hours = min(max_day_hours, adjusted_daily + buffer)
        warnings.append(
            f"Must-visit POIs require {must_visit_hours:.1f}h, adjusted daily pace to {adjusted_daily:.1f}h"
        )

    day_plans = [{"day": i + 1, "must_visit": [], "recommended": []} for i in range(trip_days)]

    # Step 1: Must-visit 分散到不同天（轮流分配）
    for i, mvp in enumerate(must_visit_pois):
        target_day = i % trip_days
        day_plans[target_day]["must_visit"].append(mvp)

    used_ids = set()
    for p in must_visit_pois:
        uid = p.get("place_id") or p.get("poi_id")
        if uid:
            used_ids.add(uid)

    # Step 2: 地理软约束填充
    for plan in day_plans:
        day_dur = sum(get_poi_duration(p) for p in plan["must_visit"])
        center = _center_of_pois(plan["must_visit"]) if plan["must_visit"] else None

        candidates = [
            p for p in remaining_pois
            if (p.get("place_id") or p.get("poi_id")) not in used_ids
        ]
        if center:
            candidates = sorted(
                candidates,
                key=lambda p: (
                    -(p["final_score"] * (DISTANCE_PENALTY if _poi_distance_to_point(p, center[0], center[1]) > MAX_DISTANCE_SOFT_KM else 1.0)),
                    _poi_distance_to_point(p, center[0], center[1]),
                ),
            )
        else:
            candidates = sorted(candidates, key=lambda p: -p["final_score"])

        for p in candidates:
            uid = p.get("place_id") or p.get("poi_id")
            if uid in used_ids:
                continue
            # City Bias: 超出都柏林中心半径则跳过
            city_dist = _poi_distance_to_point(p, DUBLIN_CENTER[0], DUBLIN_CENTER[1])
            if city_dist > MAX_CITY_RADIUS_KM:
                continue
            poi_dur = get_poi_duration(p)
            if day_dur + poi_dur > max_day_hours:
                continue
            plan["recommended"].append(p)
            used_ids.add(uid)
            day_dur += poi_dur

    # Step 3: 轻量跨天优化（相邻天 swap）
    _optimize_adjacent_day_swap(day_plans, max_day_hours)

    result_plans = []
    for plan in day_plans:
        all_pois = plan["must_visit"] + plan["recommended"]
        if not all_pois:
            continue
        optimized = optimize_route_greedy_tsp(all_pois)
        day_dur = sum(get_poi_duration(x) for x in optimized)
        must_count = sum(1 for x in optimized if x["is_must_visit"])
        result_plans.append({
            "day": plan["day"],
            "pois": optimized,
            "total_hours": round(day_dur, 1),
            "must_visit_count": must_count,
        })
    return result_plans, warnings


def format_itinerary_summary(day_plans, trip_days):
    """生成可读的行程摘要，区分 Must-visit 与推荐 POI。"""
    lines = [f"Trip days: {trip_days}"]

    # 全局 Must-visit（按 place_id 或 poi_id 去重；Google 用 place_id，本地用 poi_id）
    must_visit_seen = set()
    must_visit_list = []
    for plan in day_plans:
        for p in plan.get('pois', []):
            if not p.get('is_must_visit'):
                continue
            identifier = p.get('place_id') or p.get('poi_id')
            if identifier in must_visit_seen:
                continue
            must_visit_seen.add(identifier)
            name = p.get('name') or f"POI#{identifier}"
            dur = get_poi_duration(p)
            must_visit_list.append(f"{name} ({dur}h)")
    if must_visit_list:
        lines.append("Must-visit: " + ", ".join(must_visit_list))
    else:
        lines.append("Must-visit: (none)")

    # 全部 POI（含推荐）
    all_pois = []
    for plan in day_plans:
        for p in plan.get('pois', []):
            name = p.get('name') or f"POI#{p.get('poi_id')}"
            dur = get_poi_duration(p)
            all_pois.append((name, dur))
    if all_pois:
        lines.append("All POIs: " + ", ".join(f"{n}({d}h)" for n, d in all_pois))

    lines.append("Day plans:")
    for plan in day_plans:
        day_num = plan.get('day', 0)
        pois = plan.get('pois', [])
        must_in_day = [p for p in pois if p.get('is_must_visit')]
        recommended_in_day = [p for p in pois if not p.get('is_must_visit')]
        lines.append(f"  Day {day_num}:")
        if must_in_day:
            names_m = [p.get('name') or f"POI#{p.get('poi_id')}" for p in must_in_day]
            lines.append(f"    Must-visit: " + ", ".join(names_m))
        if recommended_in_day:
            names_r = [p.get('name') or f"POI#{p.get('poi_id')}" for p in recommended_in_day]
            lines.append(f"    Also recommended: " + ", ".join(names_r))
        if not must_in_day and not recommended_in_day:
            lines.append(f"    (no POIs)")
        total_h = plan.get('total_hours', 0)
        lines.append(f"    Total: {total_h}h")
    return "\n".join(lines)

