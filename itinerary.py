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
    """贪心 TSP：从最西点开始，每次选最近的下一个未访问点。"""
    if len(day_pois) <= 2:
        return day_pois
    # 从最西点（经度最小）开始
    ordered = sorted(day_pois, key=lambda p: (p.get('longitude') or 0))
    start = ordered[0]
    path = [start]
    remaining = ordered[1:]
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


def allocate_pois_to_days_v3_with_must_visit(pois, must_visit_identifiers, preference, trip_days):
    """
    改进版：Must-visit 优先 + 全局容量 + 地理聚类。
    支持 Google place_id 与本地 poi_id。

    Args:
        pois: POI 列表（可含 place_id 或 poi_id，需 filter_ids, latitude, longitude）
        must_visit_identifiers: place_id 或 poi_id 的列表（硬约束）
        preference: 用户偏好（需有 pace 属性）
        trip_days: 行程天数（int）

    Returns:
        (day_plans, warnings): day_plans 为 [{day, pois, total_hours, must_visit_count}, ...]
    """
    warnings = []
    must_visit_set = set(must_visit_identifiers or [])

    for poi in pois:
        identifier = poi.get("place_id") or poi.get("poi_id")
        poi["is_must_visit"] = identifier is not None and identifier in must_visit_set

    for poi in pois:
        if poi['is_must_visit']:
            poi['effective_score'] = 999.0
        else:
            poi['effective_score'] = poi.get('score', 0.0) or 0.0

    pois_sorted = sorted(pois, key=lambda x: x['effective_score'], reverse=True)

    pace = getattr(preference, 'pace', None) or 'balanced'
    daily_hours = MAX_DAY_HOURS.get(pace, 6.5)
    base_budget = daily_hours * trip_days

    # Step 2: 强制插入 Must-visit
    selected_pois = []
    must_visit_hours = 0.0
    for poi in pois_sorted:
        if poi['is_must_visit']:
            selected_pois.append(poi)
            must_visit_hours += get_poi_duration(poi)

    # Step 3: 动态调整预算
    if must_visit_hours > base_budget:
        adjusted_daily = min(8.0, must_visit_hours / trip_days)
        total_budget = adjusted_daily * trip_days
        warnings.append(
            f"Must-visit POIs require {must_visit_hours:.1f}h, adjusted daily pace to {adjusted_daily:.1f}h"
        )
    else:
        total_budget = base_budget

    # Step 4: 剩余预算填充其他 POI
    remaining_budget = total_budget - must_visit_hours
    accumulated = 0.0
    for poi in pois_sorted:
        if poi['is_must_visit']:
            continue
        dur = get_poi_duration(poi)
        if accumulated + dur <= remaining_budget:
            selected_pois.append(poi)
            accumulated += dur
        else:
            break

    # Step 5: 地理聚类分天
    n_sel = len(selected_pois)
    if n_sel < trip_days:
        clusters = list(range(n_sel))
    else:
        try:
            import numpy as np
            from sklearn.cluster import KMeans
            coords = np.array([
                [float(p.get('latitude') or 0), float(p.get('longitude') or 0)]
                for p in selected_pois
            ])
            kmeans = KMeans(n_clusters=trip_days, random_state=42, n_init=10)
            clusters = kmeans.fit_predict(coords)
        except ImportError:
            clusters = [i % trip_days for i in range(n_sel)]

    # Step 6: 组装每天行程
    day_plans = []
    for day_idx in range(trip_days):
        day_pois = [p for i, p in enumerate(selected_pois) if clusters[i] == day_idx]
        if not day_pois:
            continue
        optimized = optimize_route_greedy_tsp(day_pois)
        day_dur = sum(get_poi_duration(p) for p in optimized)
        must_count = sum(1 for p in optimized if p['is_must_visit'])
        day_plans.append({
            'day': day_idx + 1,
            'pois': optimized,
            'total_hours': round(day_dur, 1),
            'must_visit_count': must_count,
        })

    return day_plans, warnings


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

