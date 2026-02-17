#!/usr/bin/env python3
"""为 poi 表添加 Google 知名度字段。已有字段则跳过。"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from app import app
from mysql import db

COLUMNS = [
    ("google_place_id", "VARCHAR(255) DEFAULT NULL"),
    ("google_rating", "DECIMAL(3,1) DEFAULT NULL"),
    ("google_ratings_total", "INT DEFAULT NULL"),
    ("google_data_fetched_at", "DATETIME DEFAULT NULL"),
]


def main():
    with app.app_context():
        # 使用 SQLAlchemy 的 text() 执行原始 SQL（MySQL）
        from sqlalchemy import text

        for col_name, col_def in COLUMNS:
            try:
                db.session.execute(
                    text(f"ALTER TABLE poi ADD COLUMN {col_name} {col_def}")
                )
                db.session.commit()
                print(f"✅ Added column: {col_name}")
            except Exception as e:
                db.session.rollback()
                err_msg = str(e).lower()
                if "duplicate column" in err_msg or "1060" in str(e):
                    print(f"⏭️  Column {col_name} already exists, skip")
                else:
                    print(f"❌ Error adding {col_name}: {e}")

        # 可选：添加索引
        try:
            db.session.execute(
                text("CREATE INDEX idx_google_popularity ON poi(google_ratings_total DESC, google_rating DESC)")
            )
            db.session.commit()
            print("✅ Created index idx_google_popularity")
        except Exception as e:
            db.session.rollback()
            if "1061" in str(e) or "duplicate" in str(e).lower():
                print("⏭️  Index idx_google_popularity already exists")
            else:
                print(f"⚠️  Index creation skipped: {e}")


if __name__ == "__main__":
    main()
