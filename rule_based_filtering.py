"""
Rule-based Filtering：基于内容的过滤模块。
1. 人群过滤：按儿童/老人人数做硬性过滤（num_children > 0 或 num_seniors > 0）
2. Avoid 过滤（Q7）：
   - 固定 checkbox：too_much_walking→[8], stairs_hills→[41,8] 硬删；very_crowded 不处理（itinerary 避高峰）
   - 自定义输入 other:xxx：re + rapidfuzz 语义匹配 filter，命中则删
"""
import re

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

# ----- 人群过滤 -----
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


# ----- Avoid 过滤（Q7）-----
# 固定 key 硬删：too_much_walking→[8], stairs_hills→[41,8]。very_crowded 不处理（itinerary 避高峰）
# 自定义 other:xxx 用语义匹配
OVERBROAD_FILTER_IDS = {1, 2, 5, 60}
FUZZY_THRESHOLD = 75
STOPWORDS = {
    "i", "me", "my", "you", "we", "the", "a", "an", "and", "or", "but",
    "no", "not", "like", "dislike", "hate", "love", "want", "avoid",
}


def _build_avoid_other_text(avoid_list):
    """从 avoid_list 中提取 other:xxx 的自定义文本，拼接成匹配用字符串"""
    if not avoid_list or not isinstance(avoid_list, list):
        return ""
    parts = [str(v)[6:].strip() for v in avoid_list if v and str(v).strip().startswith("other:")]
    return " ".join(parts)


def extract_keywords(text):
    """清洗：长词 + 非停用，≥4 字才考虑"""
    if not text:
        return []
    text = re.sub(r"[^a-z\s]", " ", text.lower())
    words = text.split()
    return [w for w in words if w not in STOPWORDS and len(w) >= 4]


def match_filters_two_stage(avoid_text, all_filters, debug=False):
    """两阶段匹配：强匹配 + 条件 fuzzy。返回要删除的 filter_id 集合"""
    keywords = extract_keywords(avoid_text)
    matched_ids = set()
    if not keywords:
        return matched_ids
    for kw in keywords:
        for f in all_filters:
            fid = f.get("id") or f.get("filter_id")
            fname = (f.get("name") or f.get("filter_name") or "").lower()
            if fid is None:
                continue
            if kw in fname and fid not in OVERBROAD_FILTER_IDS:
                matched_ids.add(fid)
                if debug:
                    print(f"  Strong match → {fid} ({f.get('name') or f.get('filter_name')})")
        if fuzz is not None and len(kw) >= 5:
            for f in all_filters:
                fid = f.get("id") or f.get("filter_id")
                fname = (f.get("name") or f.get("filter_name") or "")
                if fid is None or fid in OVERBROAD_FILTER_IDS or len(fname) < 5:
                    continue
                score = fuzz.partial_ratio(kw, fname.lower())
                if score >= FUZZY_THRESHOLD:
                    matched_ids.add(fid)
                    if debug:
                        print(f"  Fuzzy → {fid} ({fname}) [{score}]")
    return matched_ids


def _filter_id_to_name(fid, all_filters):
    """根据 filter_id 查 filter 名称"""
    if not all_filters:
        return str(fid)
    for f in all_filters:
        if (f.get("id") or f.get("filter_id")) == fid:
            return f.get("name") or f.get("filter_name") or str(fid)
    return str(fid)


def apply_avoid_filter(poi_list, avoid_list, all_filters, debug=False):
    """
    在人群过滤后的 POI 列表上应用 Avoid 过滤。
    - 固定 checkbox（too_much_walking, stairs_hills）→ 硬删对应 filter
    - 自定义 other:xxx → 语义匹配 filter，命中则删
    - very_crowded 不处理（itinerary 避高峰）
    """
    if not avoid_list or not isinstance(avoid_list, list):
        return poi_list

    exclude_filter_ids = set()
    avoids = {str(v).strip() for v in avoid_list if v}
    fixed_selected = []
    from_other = []

    # 1. 固定 key 硬删
    if "too_much_walking" in avoids:
        exclude_filter_ids.update([8])
        fixed_selected.append("too_much_walking")
    if "stairs_hills" in avoids:
        exclude_filter_ids.update([41, 8])
        fixed_selected.append("stairs_hills")

    # 2. 自定义输入 other:xxx → 语义匹配
    avoid_other_text = _build_avoid_other_text(avoid_list)
    if avoid_other_text.strip() and all_filters:
        keywords = extract_keywords(avoid_other_text)
        matched = match_filters_two_stage(avoid_other_text, all_filters, debug=debug)
        exclude_filter_ids.update(matched)
        from_other = (avoid_other_text, keywords, matched)

    if debug:
        if fixed_selected:
            print(f"  Fixed options selected: {', '.join(fixed_selected)}")
        if from_other:
            raw, kws, matched_ids = from_other[0], from_other[1], from_other[2]
            print(f"  User custom input: \"{raw}\"")
            if kws:
                print(f"  Keywords after cleaning: {', '.join(kws)}")
            if matched_ids:
                for fid in sorted(matched_ids):
                    name = _filter_id_to_name(fid, all_filters)
                    print(f"  Exclude filter: \"{name}\", filter id = {fid}")
            else:
                print("  No matching filters found in filter table, skip")
        if fixed_selected and all_filters:
            for fid in sorted(exclude_filter_ids):
                if not from_other or fid not in from_other[2]:
                    name = _filter_id_to_name(fid, all_filters)
                    print(f"  Exclude filter: \"{name}\", filter id = {fid}")

    if not exclude_filter_ids:
        return poi_list

    filtered = []
    for p in poi_list:
        poi_filter_ids = set(p.get("filter_ids") or [])
        if poi_filter_ids & exclude_filter_ids:
            continue
        filtered.append(p)
    return filtered
