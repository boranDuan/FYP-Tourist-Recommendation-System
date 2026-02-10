"""
Rule-based Filtering：在 Layer2 兴趣得分基础上，按儿童/老人人数做硬性人群过滤。
- 前提：num_children > 0 或 num_seniors > 0
- 极端风险 POI（如 Zip Lining）直接排除
- 非极端风险 POI 可选调用 Google API 用评论 tags 验证后保留/删除
"""

# Step0: 风险 filter_id 列表（仅做标记，不做主观判断）
RISK_FILTER_IDS_KIDS = {63, 68, 87, 55, 77, 78, 42, 54, 59, 58, 93, 96}
RISK_FILTER_IDS_SENIORS = {8, 75, 29, 63, 68, 55}

# 极端硬删：该 filter 若命中儿童/老人风险则直接排除
FILTER_ID_EXTREME_HARD_EXCLUDE = 87  # Zip Lining


def _num(x):
    """问卷人数：None 或非法视为 0"""
    if x is None:
        return 0
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def is_risk_poi(poi, preference):
    """
    标记 POI 是否属于风险类别（Step0）。
    只根据 filter_id 和问卷人数，不做主观判断。
    poi 需为 dict，包含 'filter_ids'（list/set）；会原地写入 risk_kids, risk_seniors, exclude。
    preference 需有 num_children, num_seniors 属性。
    """
    filter_ids = set(poi.get("filter_ids") or [])
    num_children = _num(getattr(preference, "num_children", None))
    num_seniors = _num(getattr(preference, "num_seniors", None))

    poi["risk_kids"] = num_children > 0 and bool(filter_ids & RISK_FILTER_IDS_KIDS)
    poi["risk_seniors"] = num_seniors > 0 and bool(filter_ids & RISK_FILTER_IDS_SENIORS)

    if FILTER_ID_EXTREME_HARD_EXCLUDE in filter_ids:
        poi["exclude"] = poi["risk_kids"] or poi["risk_seniors"]
    else:
        poi["exclude"] = False

    return poi


def verify_poi_google_tags(poi_name, gmaps):
    """
    可选：调用 Google API 精细验证风险 POI。
    - 找不到 place_id 或评论数量为 0 → 返回 False（建议删除）
    - 成功则返回评论中出现的单词集合（小写），供调用方检查 'kids' / 'senior' / 'elderly'
    """
    if not poi_name or not gmaps:
        return False
    try:
        # 搜索 place_id（googlemaps 库：find_place(input, input_type, fields=[])）
        place = gmaps.find_place(poi_name, "textquery", fields=["place_id"])
        if not place.get("candidates"):
            return False

        place_id = place["candidates"][0]["place_id"]

        # 获取详情（含评论）
        details = gmaps.place(place_id, fields=["user_ratings_total", "reviews"])
        result = details.get("result") or {}
        if result.get("user_ratings_total", 0) == 0:
            return False

        reviews = result.get("reviews") or []
        tags = set()
        for review in reviews:
            text = review.get("text") or ""
            tags.update(w.lower() for w in text.split())
        return tags
    except Exception:
        return False


def step0_hard_filter(poi_list, preference, gmaps=None):
    """
    Step0：对全量 Layer2 有效 POI 做硬性人群过滤。
    逻辑：
    1. 对极端 filter_id 硬删（exclude=True）
    2. 对标记为风险的 POI 若提供 gmaps 则调用 Google API 验证评论 tags
    3. 非风险 POI 或通过验证 → 保留
    返回按 score 降序排列的列表。
    """
    cleaned = []
    for poi in poi_list:
        is_risk_poi(poi, preference)

        if poi.get("exclude"):
            continue

        need_api_kids = poi.get("risk_kids", False)
        need_api_seniors = poi.get("risk_seniors", False)

        if (need_api_kids or need_api_seniors) and gmaps:
            tags = verify_poi_google_tags(poi.get("name") or "", gmaps)
            if not tags:
                continue
            kids_ok = not need_api_kids or "kids" in tags
            seniors_ok = not need_api_seniors or ("senior" in tags or "elderly" in tags)
            if kids_ok and seniors_ok:
                cleaned.append(poi)
        else:
            cleaned.append(poi)

    return sorted(cleaned, key=lambda x: float(x.get("score") or 0), reverse=True)
