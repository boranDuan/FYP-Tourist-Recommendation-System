"""
Preference Matching 模块：根据用户 interests 权重为 POI 计算兴趣相关性得分；
以及 specific_places 解析为 must-visit POI（查 must_visit_cache → Google 地图 → contains/fuzzy，并写回缓存）。
"""
import re

try:
    from rapidfuzz import fuzz, process
except ImportError:
    fuzz = process = None


def _get_must_visit_cache_model():
    """Lazy import 避免与 mysql 循环依赖"""
    from mysql import MustVisitCache
    return MustVisitCache

# interest_key -> filter_id 列表（POI 带任一 filter_id 即属于该 interest 类型）
INTEREST_TO_FILTER_IDS = {
    "museum": [19, 37, 43, 18, 24, 25, 26, 23, 41, 52, 80, 65],
    "nature": [75, 66, 89, 11, 74, 38, 39, 22, 90, 67, 95],
    "culture": [40, 73, 79, 84, 94, 14, 82, 17, 85],
    "shopping": [15, 12, 28, 44, 17],
    "nightlife": [10, 96],
}


def calculate_poi_score(poi_id, poi_filter_ids, interests):
    """
    根据 POI 的 filter_id 与用户 interests 权重，计算兴趣相关性得分。

    Args:
        poi_id: POI ID（当前未使用，预留）
        poi_filter_ids: 该 POI 关联的 filter_id 列表
        interests: 用户 interests 权重对象，如 {"museum": 0.5, "culture": 0.5, "nature": 0.0, ...}

    Returns:
        float: 兴趣得分
    """
    score = 0.0

    if not interests or not isinstance(interests, dict):
        return score

    poi_filter_ids = set(poi_filter_ids or [])

    for interest_key, weight in interests.items():
        if not weight or float(weight) <= 0:
            continue
        related_filters = INTEREST_TO_FILTER_IDS.get(interest_key)
        if not related_filters:
            continue  # 如 "other" 无映射，跳过
        if any(fid in related_filters for fid in poi_filter_ids):
            score += float(weight)

    return score


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
        row = db_session.query(MustVisitCache).filter_by(user_input=user_input).first()
        if row:
            row.poi_id = poi_id
            row.resolved_name = resolved_name
            row.google_place_id = google_place_id
        else:
            db_session.add(MustVisitCache(
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
        cached = db_session.query(MustVisitCache).filter_by(user_input=token).first()
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
        db_session.commit()
    except Exception:
        db_session.rollback()

    return result_ids, display_pairs
