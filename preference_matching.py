"""
Preference Matching 模块：根据用户 interests 权重为 POI 计算兴趣相关性得分；
以及 specific_places 解析为 must-visit POI（查 must_visit_cache → Google 地图 → contains/fuzzy，并写回缓存）。
支持 Google 知名度评分（rating, user_ratings_total）融入综合得分。
"""
import math
import re

try:
    from rapidfuzz import fuzz, process
except ImportError:
    fuzz = process = None


def _get_must_visit_cache_model():
    """Lazy import 避免与 mysql 循环依赖"""
    from mysql import MustVisitCache
    return MustVisitCache

# Pace → 每日 POI 数（不要写死在逻辑里）
PACE_TO_DAILY_POI = {
    "relaxed": 3,
    "balanced": 4,
    "intensive": 6,
}

# 不推荐的 POI 类型（filter_id）与名称黑名单
EXCLUDED_FILTER_IDS = [
    61,   # Sports Venues - 体育场馆
    88,   # Race Course - 赛马场
    20,   # Golf
    21,   # Golf Course
    92,   # Pitch and Putt
    31,   # Coach - 长途汽车（非景点）
    33,   # Transport - 交通（非景点）
    96,   # Casinos - 赌场
    72,   # Fitness and Leisure - 健身房
    76,   # Swimming Pool - 游泳池
    60,   # General - 太模糊
]
EXCLUDED_POI_NAMES = [
    "Croke Park",
    "Aviva Stadium",
    "RDS Arena",
    "Lansdowne Road",
]


def filter_unwanted_pois(pois):
    """
    过滤不受欢迎的 POI（体育场馆等）。
    - 基于 filter_id
    - 基于名称黑名单
    """
    filtered = []
    for poi in pois:
        poi_filter_ids = poi.get("filter_ids") or []
        poi_name = (poi.get("name") or "").strip()
        if any(fid in EXCLUDED_FILTER_IDS for fid in poi_filter_ids):
            continue
        if any(excluded in poi_name for excluded in EXCLUDED_POI_NAMES):
            continue
        filtered.append(poi)
    return filtered


# interest_key -> filter_id 列表（POI 带任一 filter_id 即属于该 interest 类型）
INTEREST_TO_FILTER_IDS = {
    "museum": [19, 37, 24, 25, 26, 23, 41, 52, 80, 65],
    "nature": [75, 66, 89, 11, 74, 38, 39, 22, 90, 67, 95],
    "culture": [40, 73, 79, 84, 94, 14, 82, 85, 43, 18, 24, 25, 26, 23, 41, 19, 37, 80],
    "shopping": [15, 12, 28, 44, 17],
    "nightlife": [10, 96],
}


def calculate_popularity_score(rating, ratings_total):
    """
    知名度评分（0–1）。
    评价数量比评分更重要：4.6⭐ 3000 评价 > 4.8⭐ 300 评价。
    权重：评价数 80%，评分 20%。
    """
    if not rating or ratings_total is None or ratings_total == 0:
        return 0.0
    try:
        rating = float(rating)
        ratings_total = int(ratings_total)
    except (TypeError, ValueError):
        return 0.0
    if ratings_total <= 0:
        return 0.0
    ratings_log = math.log10(max(1, ratings_total))
    ratings_score = min(1.0, ratings_log / 5.0)
    rating_score = max(0.0, min(1.0, (rating - 4.0) / 1.0))
    return ratings_score * 0.8 + rating_score * 0.2


def calculate_final_score_with_popularity(interest_score, google_rating=None, google_ratings_total=None):
    """
    综合评分 = 兴趣匹配 × (1 + 知名度加成)
    无 Google 数据时，保留 70%。
    """
    if not google_ratings_total or google_ratings_total == 0:
        return interest_score * 0.7
    popularity_score = calculate_popularity_score(google_rating, google_ratings_total)
    final_score = interest_score * (1.0 + popularity_score * 0.8)
    return min(1.0, final_score)


def calculate_poi_score(poi_id, poi_filter_ids, interests):
    """
    饱和模型：score = 1 - Π(1 - weight_i)
    避免多维度匹配导致分数虚高
    """
    if not interests or not isinstance(interests, dict):
        return 0.0
    
    poi_filter_ids = set(poi_filter_ids or [])
    if not poi_filter_ids:
        return 0.0
    
    # 收集所有匹配的兴趣权重
    matched_weights = []
    
    for interest_key, weight in interests.items():
        if not weight or float(weight) <= 0:
            continue
        
        related_filters = INTEREST_TO_FILTER_IDS.get(interest_key)
        if not related_filters:
            continue
        
        # 检查是否有filter匹配
        if any(fid in related_filters for fid in poi_filter_ids):
            matched_weights.append(float(weight))
    
    if not matched_weights:
        return 0.0
    
    # 饱和模型公式: score = 1 - Π(1 - w_i)
    product = 1.0
    for w in matched_weights:
        product *= (1.0 - w)
    
    score = 1.0 - product
    
    return score



def get_daily_poi_capacity(pace, num_children=0, num_seniors=0):
    """
    根据 pace 与人群修正得到每日 POI 容量。
    带小孩或老人 → 节奏下降（capacity -= 1），最低 2。
    """
    base = PACE_TO_DAILY_POI.get(pace) if pace else PACE_TO_DAILY_POI.get("balanced")
    if base is None:
        base = PACE_TO_DAILY_POI["balanced"]
    capacity = base
    if (num_children or 0) > 0 or (num_seniors or 0) > 0:
        capacity -= 1
    return max(2, capacity)


def resolve_specific_places_to_google_data(specific_places_text, gmaps_client=None):
    """
    将用户输入的 specific_places 用 Google Places API 解析，直接返回 Google 数据，不匹配本地 DB。
    返回: [{"place_id": "...", "name": "...", "latitude": float, "longitude": float}, ...]
    """
    if not specific_places_text or not isinstance(specific_places_text, str) or not gmaps_client:
        return []

    tokens = re.split(r"[,;\n]+", specific_places_text)
    result = []
    seen_place_ids = set()

    for t in tokens:
        token = t.strip()
        if not token or len(token) < 3:
            continue
        try:
            query = f"{token} Dublin"
            place = gmaps_client.find_place(
                query, "textquery",
                fields=["name", "place_id", "geometry"]
            )
            candidates = place.get("candidates") or []
            if not candidates:
                continue
            c0 = candidates[0]
            place_id = c0.get("place_id")
            name = c0.get("name")
            if not place_id:
                continue
            if place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)

            lat, lng = None, None
            geom = c0.get("geometry")
            if geom and isinstance(geom.get("location"), dict):
                loc = geom["location"]
                lat = loc.get("lat")
                lng = loc.get("lng")

            result.append({
                "place_id": place_id,
                "name": name or token,
                "latitude": float(lat) if lat is not None else None,
                "longitude": float(lng) if lng is not None else None,
                "filter_ids": [],
                "duration": 2.0,
                "user_input": token,
            })
        except Exception:
            continue

    return result


def resolve_specific_places_to_poi_ids_and_names(specific_places_text, poi_model, db_session, gmaps_client=None):
    """
    将 specific_places 文本解析为 (poi_ids, [(raw_token, resolved_name), ...])。
    先查 must_visit_cache；未命中则 Google 地图 → 本地 contains/fuzzy，并写回缓存。
    """
    if not specific_places_text or not isinstance(specific_places_text, str):
        return [], []
    POI = poi_model
    db = db_session
    MustVisitCache = _get_must_visit_cache_model()
    tokens = re.split(r"[,;\n]+", specific_places_text)
    seen = set()
    result_ids = []
    display_pairs = []

    def match_poi_by_name(name):
        if not name:
            return None
        n = (name or "").strip().lower()
        if not n:
            return None
        return POI.query.filter(db.func.lower(POI.name).contains(n)).first()

    def fallback_resolve(token):
        """本地 contains + fuzzy 兜底"""
        token_lower = token.lower()
        poi = POI.query.filter(db.func.lower(POI.name).contains(token_lower)).first()
        if poi:
            return poi.poi_id, poi.name
        if len(token) >= 4 and process is not None and fuzz is not None:
            rows = POI.query.with_entities(POI.name, POI.poi_id).limit(500).all()
            names = [(r[0].lower() if r[0] else "") for r in rows]
            best = process.extractOne(token_lower, names, scorer=fuzz.WRatio, score_cutoff=82)
            if best:
                _, score, idx = best
                return rows[idx][1], rows[idx][0]
        return None, None

    def upsert_cache(user_input, poi_id, resolved_name, google_place_id=None):
        row = MustVisitCache.query.filter_by(user_input=user_input).first()
        if row:
            row.poi_id = poi_id
            row.resolved_name = resolved_name
            row.google_place_id = google_place_id
        else:
            db.session.add(MustVisitCache(
                user_input=user_input,
                poi_id=poi_id,
                resolved_name=resolved_name,
                google_place_id=google_place_id,
            ))

    for t in tokens:
        token = t.strip()
        if not token or len(token) < 3:
            continue
        resolved_name = None
        poi_id = None
        google_place_id = None

        # 1) 先查缓存
        cached = MustVisitCache.query.filter_by(user_input=token).first()
        if cached is not None:
            if cached.poi_id is not None and cached.poi_id not in seen:
                seen.add(cached.poi_id)
                result_ids.append(cached.poi_id)
            display_pairs.append((token, "(unresolved)" if cached.poi_id is None else (cached.resolved_name or token)))
            continue

        # 2) 未命中：Google 或 fallback
        if gmaps_client:
            try:
                query = f"{token} Dublin"
                place = gmaps_client.find_place(query, "textquery", fields=["name", "place_id"])
                candidates = place.get("candidates") or []
                if candidates:
                    c0 = candidates[0]
                    google_name = c0.get("name")
                    if c0.get("place_id"):
                        google_place_id = c0["place_id"]
                    if google_name:
                        poi = match_poi_by_name(google_name)
                        if poi and poi.poi_id not in seen:
                            poi_id = poi.poi_id
                            resolved_name = poi.name
            except Exception:
                pass

        if poi_id is None:
            poi_id, resolved_name = fallback_resolve(token)

        if poi_id is not None and poi_id not in seen:
            seen.add(poi_id)
            result_ids.append(poi_id)
            display_pairs.append((token, resolved_name or token))
        else:
            display_pairs.append((token, "(unresolved)"))

        # 3) 写回缓存
        upsert_cache(token, poi_id, resolved_name or "(unresolved)", google_place_id)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    return result_ids, display_pairs
