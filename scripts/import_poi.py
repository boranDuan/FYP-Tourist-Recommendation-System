import csv
import sys
from pathlib import Path
from typing import Optional

from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mysql import get_database_config, db, POI


def create_app() -> Flask:
    app = Flask(__name__)
    get_database_config(app)
    return app


def parse_float(value: str) -> Optional[float]:
    try:
        value = value.strip()
        if not value:
            return None
        return float(value)
    except (ValueError, AttributeError):
        return None


def parse_telephone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.replace("Tel:", "").replace("tel:", "").strip()
    return cleaned or None


def import_poi(csv_path: Path):
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在：{csv_path}")

    app = create_app()
    with app.app_context():
        with csv_path.open(encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            imported = 0
            updated = 0

            max_tags_length = getattr(POI.__table__.columns.get("tags").type, "length", None)

            for idx, row in enumerate(reader, start=1):
                source_id_raw = row.get("ID") or row.get("Id") or row.get("id")
                if source_id_raw:
                    source_id = source_id_raw.strip()
                else:
                    source_id = str(idx)
                if not source_id:
                    continue

                name = (row.get("Name") or "").strip()
                if not name:
                    continue

                url = (row.get("Url") or "").strip()
                poi = POI.query.filter_by(source_id=source_id).first()
                is_new = poi is None

                if is_new:
                    poi = POI(name=name, source_id=source_id, source="csv")
                    db.session.add(poi)
                else:
                    poi.name = name

                tags_value = (row.get("Tags") or "").strip() or None
                if tags_value and max_tags_length:
                    tags_value = tags_value[:max_tags_length]

                poi.update_from_dict(
                    {
                        "name": name,
                        "address": (row.get("Address") or "").strip() or None,
                        "telephone": parse_telephone(row.get("Telephone")),
                        "latitude": parse_float(row.get("Latitude", "")),
                        "longitude": parse_float(row.get("Longitude", "")),
                        "tags": tags_value,
                        "url": url or None,
                        "photos": (row.get("Photo") or "").strip() or None,
                    }
                )

                imported += 1 if is_new else 0
                updated += 0 if is_new else 1

            db.session.commit()

    print(f"导入完成：新增 {imported} 条，更新 {updated} 条。")


if __name__ == "__main__":
    default_csv = Path("APIs/Attractions.csv")
    csv_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else default_csv
    import_poi(csv_arg.resolve())

