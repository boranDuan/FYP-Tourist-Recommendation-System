import argparse
import re
from pathlib import Path

from flask import Flask

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from mysql import get_database_config, db, POI, Filter  # noqa: E402


def create_app() -> Flask:
    app = Flask(__name__)
    get_database_config(app)
    return app


STOPWORDS = {"and", "of", "the", "for", "in", "on", "to", "a", "an"}


def format_tag(tag: str) -> str:
    cleaned = re.sub(r'\s+', ' ', (tag or "").strip())
    if not cleaned:
        return ""
    words = cleaned.split(" ")
    normalized = []
    for word in words:
        lower = word.lower()
        if lower in STOPWORDS:
            normalized.append(lower)
        else:
            normalized.append(lower.capitalize())
    return " ".join(normalized)


def parse_tags(raw: str, known_filters: set[str] = None, filter_name_map: dict[str, str] = None) -> list[str]:
    """
    解析标签字符串，智能匹配已知的 filters。
    
    Args:
        raw: 原始标签字符串
        known_filters: 已知 filter 名称的小写集合
        filter_name_map: 小写名称到原始名称的映射（用于保持原始格式）
    
    Returns:
        格式化后的标签列表
    """
    if not raw:
        return []
    if known_filters is None:
        known_filters = set()
    if filter_name_map is None:
        filter_name_map = {}
    
    # 先按逗号分割
    if "," in raw:
        segments = [s.strip() for s in raw.split(",")]
    else:
        segments = [raw.strip()]
    
    all_tags = []
    for segment in segments:
        if not segment:
            continue
        
        # 如果这个片段在已知 filters 中，使用原始格式
        segment_lower = segment.lower()
        if segment_lower in known_filters:
            original_name = filter_name_map.get(segment_lower, segment)
            all_tags.append(original_name)
            continue
        
        # 否则尝试智能拆分：优先匹配已知的复合标签
        words = segment.split()
        if not words:
            continue
        
        i = 0
        while i < len(words):
            matched = False
            # 从最长到最短尝试匹配（最多5个词）
            for length in range(min(len(words) - i, 5), 0, -1):
                candidate = " ".join(words[i:i+length])
                candidate_lower = candidate.lower()
                if candidate_lower in known_filters:
                    # 使用原始格式
                    original_name = filter_name_map.get(candidate_lower, candidate)
                    all_tags.append(original_name)
                    i += length
                    matched = True
                    break
            
            if not matched:
                # 单个词，检查是否是已知的 filter
                word = words[i]
                word_lower = word.lower()
                if word_lower in known_filters:
                    original_name = filter_name_map.get(word_lower, word)
                    all_tags.append(original_name)
                elif word_lower not in STOPWORDS:
                    # 即使不在已知 filters 中，也保留（可能是新标签）
                    all_tags.append(word)
                i += 1
    
    # 去重并格式化
    seen = set()
    normalized = []
    for tag in all_tags:
        formatted = format_tag(tag)
        key = formatted.lower()
        if formatted and key not in seen and key not in STOPWORDS:
            seen.add(key)
            normalized.append(formatted)
    
    return normalized


def populate_filters(reset: bool = False, dry_run: bool = False, no_create: bool = False):
    app = create_app()

    with app.app_context():
        if reset and not dry_run:
            db.session.execute(text("DELETE FROM poi_filter"))
            Filter.query.delete()
            db.session.commit()

        existing_filters = {}
        known_filter_names = set()
        filter_name_map = {}
        for filter_obj in Filter.query.order_by(Filter.filter_id.asc()).all():
            normalized = filter_obj.filter_name.lower() if filter_obj.filter_name else None
            if normalized:
                existing_filters[normalized] = filter_obj
                known_filter_names.add(normalized)
                filter_name_map[normalized] = filter_obj.filter_name
        created_filters = 0
        associations_added = 0

        poi_queryset = POI.query.all()
        for poi in poi_queryset:
            raw_tags = poi.tags or ""
            tags = parse_tags(raw_tags, known_filter_names, filter_name_map)
            if not tags:
                continue

            normalized_string = ", ".join(tags)
            if poi.tags != normalized_string:
                poi.tags = normalized_string

            for tag in tags:
                key = tag.lower()
                filter_obj = existing_filters.get(key)

                if not filter_obj and not no_create:
                    filter_obj = Filter(filter_name=tag)
                    existing_filters[key] = filter_obj
                    if not dry_run:
                        db.session.add(filter_obj)
                    created_filters += 1
                elif not filter_obj and no_create:
                    continue

                if filter_obj not in poi.filters:
                    poi.filters.append(filter_obj)
                    associations_added += 1

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()

        print(
            f"Filters created: {created_filters}, "
            f"associations added: {associations_added}, "
            f"dry_run={dry_run}"
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Populate filters table from POI tags.")
    parser.add_argument("--reset", action="store_true", help="Clear existing filters and associations before populating.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without committing changes.")
    parser.add_argument("--no-create", action="store_true", help="Do not create new filters; only sync existing ones.")
    return parser.parse_args()


def main():
    args = parse_args()
    populate_filters(
        reset=args.reset,
        dry_run=args.dry_run,
        no_create=args.no_create,
    )


if __name__ == "__main__":
    main()

