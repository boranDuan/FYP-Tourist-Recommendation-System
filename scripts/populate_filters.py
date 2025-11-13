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


def parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    if "," in raw:
        parts = [part.strip() for part in raw.split(",")]
    else:
        parts = re.split(r"\s+", raw.strip())
    seen = set()
    normalized = []
    for part in parts:
        formatted = format_tag(part)
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
        for filter_obj in Filter.query.order_by(Filter.filter_id.asc()).all():
            normalized = filter_obj.filter_name.lower() if filter_obj.filter_name else None
            if normalized:
                existing_filters[normalized] = filter_obj
        created_filters = 0
        associations_added = 0

        poi_queryset = POI.query.all()
        for poi in poi_queryset:
            raw_tags = poi.tags or ""
            tags = parse_tags(raw_tags)
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

