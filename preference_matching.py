"""
Preference Matching 模块：根据用户 interests 权重为 POI 计算兴趣相关性得分。
使用 poi_filter 表 + INTEREST_TO_FILTER_IDS 规则映射表。
"""

# interest_key -> filter_id 列表（POI 带任一 filter_id 即属于该 interest 类型）
INTEREST_TO_FILTER_IDS = {
    "museum": [19, 37, 43, 18, 24, 25, 26, 23, 41, 52, 80, 91, 65, 71],
    "nature": [75, 66, 89, 11, 74, 38, 69, 39, 22, 90, 67, 95],
    "culture": [2, 40, 73, 79, 84, 94, 14, 91, 82, 17, 85, 71],
    "shopping": [15, 12, 28, 44, 17, 71],
    "nightlife": [10, 40, 73, 96],
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
