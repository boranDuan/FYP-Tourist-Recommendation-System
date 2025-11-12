import argparse
import json
import logging
import math
import os
import sys
import time
from typing import Dict, Optional, Sequence, Tuple

import requests
from flask import Flask

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mysql import get_database_config, db, POI  # noqa: E402

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"


def create_app() -> Flask:
    app = Flask(__name__)
    get_database_config(app)
    return app


def normalize_name(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


class GooglePlacesClient:
    def __init__(self, api_key: str, language: str, session: Optional[requests.Session] = None):
        self.api_key = api_key
        self.language = language
        self.session = session or requests.Session()

    def text_search(
        self,
        query: str,
        location: Optional[Sequence[float]] = None,
        radius: int = 5000,
    ) -> Dict:
        params = {
            "query": query,
            "key": self.api_key,
            "language": self.language,
        }
        if location:
            params["location"] = f"{location[0]},{location[1]}"
            params["radius"] = radius
        response = self.session.get(TEXT_SEARCH_URL, params=params, timeout=15)
        response.raise_for_status()
        return response.json()

    def place_details(self, place_id: str) -> Dict:
        params = {
            "place_id": place_id,
            "key": self.api_key,
            "language": self.language,
            "fields": ",".join(
                [
                    "name",
                    "formatted_address",
                    "formatted_phone_number",
                    "international_phone_number",
                    "geometry",
                    "opening_hours/weekday_text",
                    "price_level",
                    "rating",
                    "types",
                    "website",
                    "url",
                    "user_ratings_total",
                    "editorial_summary",
                    "photos",
                ]
            ),
        }
        response = self.session.get(DETAILS_URL, params=params, timeout=15)
        response.raise_for_status()
        return response.json()


def select_match(
    poi: POI,
    candidates: Sequence[Dict],
    distance_threshold_km: float,
) -> Optional[Dict]:
    target_name = normalize_name(poi.name)
    for candidate in candidates:
        candidate_name = normalize_name(candidate.get("name", ""))
        if target_name and candidate_name and target_name == candidate_name:
            return candidate
        poi_lat = getattr(poi, "latitude", None)
        poi_lon = getattr(poi, "longitude", None)
        geometry = candidate.get("geometry", {})
        location = geometry.get("location")
        if poi_lat is not None and poi_lon is not None and location:
            cand_lat = location.get("lat")
            cand_lon = location.get("lng")
            if cand_lat is None or cand_lon is None:
                continue
            distance = haversine_distance(poi_lat, poi_lon, cand_lat, cand_lon)
            if distance <= distance_threshold_km:
                return candidate
    return candidates[0] if candidates else None


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def normalize_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.replace("Tel:", "").replace("tel:", "").strip() or None


def format_opening_hours(opening_hours: Optional[Dict]) -> Optional[Dict[str, str]]:
    if not opening_hours:
        return None
    weekday_text = opening_hours.get("weekday_text")
    if not weekday_text:
        return None
    normalized: Dict[str, str] = {}
    for idx, entry in enumerate(weekday_text):
        if idx >= len(DAY_NAMES):
            break
        parts = entry.split(":", 1)
        if len(parts) != 2:
            continue
        times_part = parts[1].strip()
        if not times_part:
            continue
        times_part = times_part.replace("–", "-").replace("—", "-")
        if times_part.lower() in {"closed", "休息"}:
            simplified = "-"
        else:
            allowed_chars = set("0123456789:-, ")
            simplified = "".join(ch for ch in times_part if ch in allowed_chars)
            simplified = simplified.replace("  ", " ").strip()
        normalized[DAY_NAMES[idx]] = simplified or "-"
    # 填补缺失的日期
    for idx, day in enumerate(DAY_NAMES):
        normalized.setdefault(day, "-")
    return normalized or None


def format_photos(photos: Optional[Sequence[Dict]], limit: int = 3) -> Optional[str]:
    if not photos:
        return None
    references = [photo.get("photo_reference") for photo in photos[:limit] if photo.get("photo_reference")]
    if not references:
        return None
    return json.dumps(references)


def truncate_value(value: Optional[str], max_length: Optional[int]) -> Optional[str]:
    if value is None or max_length is None:
        return value
    return value[:max_length]


def update_poi_from_details(poi: POI, details: Dict, length_limits: Dict[str, Optional[int]]) -> Dict:
    result = details.get("result", {})
    changes = {}

    address = result.get("formatted_address")
    if address and not poi.address:
        changes["address"] = address

    location = (result.get("geometry") or {}).get("location")
    if location:
        lat = location.get("lat")
        lng = location.get("lng")
        if lat is not None and (poi.latitude is None or not math.isfinite(poi.latitude)):
            changes["latitude"] = lat
        if lng is not None and (poi.longitude is None or not math.isfinite(poi.longitude)):
            changes["longitude"] = lng

    opening_hours = format_opening_hours(result.get("opening_hours"))
    if opening_hours and not poi.opening_hours:
        changes["opening_hours"] = opening_hours

    price_level = result.get("price_level")
    if price_level is not None and poi.price_level is None:
        changes["price_level"] = price_level

    rating = result.get("rating")
    if rating is not None and poi.rating is None:
        changes["rating"] = rating

    if not poi.telephone:
        phone = normalize_phone(
            result.get("formatted_phone_number") or result.get("international_phone_number")
        )
        if phone:
            changes["telephone"] = truncate_value(phone, length_limits.get("telephone"))

    types = result.get("types")
    if types and not poi.tags:
        tags_value = ",".join(types)
        changes["tags"] = truncate_value(tags_value, length_limits.get("tags"))

    website = result.get("website")
    url = result.get("url")
    if website and not poi.url:
        changes["url"] = truncate_value(website, length_limits.get("url"))
    elif url and not poi.url:
        changes["url"] = truncate_value(url, length_limits.get("url"))

    photos = format_photos(result.get("photos"))
    if photos and not poi.photos:
        changes["photos"] = photos

    return changes


def build_poi_data_from_details(details: Dict, length_limits: Dict[str, Optional[int]]) -> Dict:
    result = details.get("result", {})
    location = (result.get("geometry") or {}).get("location") or {}
    return {
        "name": result.get("name"),
        "address": result.get("formatted_address"),
        "telephone": truncate_value(
            normalize_phone(
                result.get("formatted_phone_number") or result.get("international_phone_number")
            ),
            length_limits.get("telephone"),
        ),
        "latitude": location.get("lat"),
        "longitude": location.get("lng"),
        "opening_hours": format_opening_hours(result.get("opening_hours")),
        "price_level": result.get("price_level"),
        "rating": result.get("rating"),
        "tags": truncate_value(
            ",".join(result.get("types", [])) if result.get("types") else None,
            length_limits.get("tags"),
        ),
        "url": truncate_value(result.get("website") or result.get("url"), length_limits.get("url")),
        "photos": format_photos(result.get("photos")),
    }


def augment_pois(
    api_key: str,
    language: str,
    distance_threshold_km: float,
    sleep_seconds: float,
    limit: Optional[int],
    create_missing: bool,
    only_county: Optional[str],
    bbox: Optional[Tuple[float, float, float, float]],
):
    app = create_app()
    client = GooglePlacesClient(api_key=api_key, language=language)

    with app.app_context():
        column_length_limits = {}
        for field in ("tags", "url", "telephone"):
            column = POI.__table__.columns.get(field)
            column_length_limits[field] = getattr(column.type, "length", None) if column is not None else None

        pois = POI.query.order_by(POI.poi_id.asc())
        if limit:
            pois = pois.limit(limit)

        processed = 0
        updated = 0
        created = 0
        skipped = 0

        for poi in pois:
            if only_county:
                address_text = (poi.address or "").lower()
                if only_county.lower() not in address_text:
                    continue
            if bbox:
                min_lat, min_lng, max_lat, max_lng = bbox
                if (
                    poi.latitude is not None
                    and poi.longitude is not None
                    and not (min_lat <= poi.latitude <= max_lat and min_lng <= poi.longitude <= max_lng)
                ):
                    continue
            processed += 1
            logging.info("Processing %s (id=%s)", poi.name, poi.poi_id)

            location = None
            if poi.latitude is not None and poi.longitude is not None:
                location = (poi.latitude, poi.longitude)

            try:
                search_response = client.text_search(query=poi.name, location=location)
            except requests.RequestException as exc:
                logging.error("Text search failed for %s: %s", poi.name, exc)
                skipped += 1
                continue

            status = search_response.get("status")
            if status == "ZERO_RESULTS":
                logging.info("No Google match for %s", poi.name)
                skipped += 1
                time.sleep(sleep_seconds)
                continue
            if status != "OK":
                logging.warning("Google returned status %s for %s", status, poi.name)
                skipped += 1
                time.sleep(sleep_seconds)
                continue

            candidates = search_response.get("results", [])
            match = select_match(poi, candidates, distance_threshold_km=distance_threshold_km)
            if not match:
                logging.info("No suitable candidate for %s", poi.name)
                skipped += 1
                time.sleep(sleep_seconds)
                continue

            place_id = match.get("place_id")
            if not place_id:
                logging.info("Candidate for %s lacks place_id", poi.name)
                skipped += 1
                time.sleep(sleep_seconds)
                continue

            try:
                details = client.place_details(place_id=place_id)
            except requests.RequestException as exc:
                logging.error("Details retrieval failed for %s: %s", poi.name, exc)
                skipped += 1
                continue

            status = details.get("status")
            if status != "OK":
                logging.warning("Details status %s for %s", status, poi.name)
                skipped += 1
                time.sleep(sleep_seconds)
                continue

            changes = update_poi_from_details(poi, details, column_length_limits)
            if changes:
                poi.update_from_dict(changes)
                updated += 1
                logging.info("Updated %s with fields: %s", poi.name, ", ".join(changes.keys()))
            else:
                logging.info("No missing fields to update for %s", poi.name)

            if create_missing:
                google_name = details.get("result", {}).get("name")
                if google_name:
                    existing = POI.query.filter((POI.source_id == place_id) | (POI.name == google_name)).first()
                    if not existing:
                        new_poi_data = build_poi_data_from_details(details, column_length_limits)
                        if new_poi_data.get("name"):
                            allow_creation = True
                            if bbox:
                                min_lat, min_lng, max_lat, max_lng = bbox
                                lat = new_poi_data.get("latitude")
                                lng = new_poi_data.get("longitude")
                                if (
                                    lat is None
                                    or lng is None
                                    or not (min_lat <= lat <= max_lat and min_lng <= lng <= max_lng)
                                ):
                                    logging.info(
                                        "Skip creating %s due to outside bounding box", new_poi_data["name"]
                                    )
                                    allow_creation = False
                            if allow_creation:
                                new_poi = POI()
                                new_poi.update_from_dict(new_poi_data)
                                new_poi.source_id = place_id
                                new_poi.source = "google_places"
                                db.session.add(new_poi)
                                created += 1
                                logging.info("Created new POI %s", new_poi_data["name"])

            db.session.commit()
            time.sleep(sleep_seconds)

        logging.info("Done. processed=%s updated=%s created=%s skipped=%s", processed, updated, created, skipped)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Augment POI table with Google Places data.")
    parser.add_argument("--api-key", default=None, help="Google Maps API key (falls back to GOOGLE_MAPS_API_KEY env).")
    parser.add_argument(
        "--language",
        default="en",
        help="Language code for Google Places responses (default: en).",
    )
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=1.0,
        help="Maximum distance in km to treat a Google match as the same POI (default: 1.0).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Delay in seconds between Google API calls (default: 0.2).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of POIs to process (default: no limit).",
    )
    parser.add_argument(
        "--bbox",
        default=None,
        help="Bounding box filter formatted as min_lat,min_lng,max_lat,max_lng.",
    )
    parser.add_argument(
        "--only-county",
        default=None,
        help="Only process POIs whose address contains this keyword (case-insensitive).",
    )
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Create new POI entries when Google returns a place not already in the database.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")

    api_key_value = args.api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key_value:
        logging.error("Google Maps API key is required. Provide --api-key or set GOOGLE_MAPS_API_KEY.")
        sys.exit(1)

    bbox = None
    if args.bbox:
        try:
            parts = [float(part.strip()) for part in args.bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            min_lat, min_lng, max_lat, max_lng = parts
            bbox = (
                min(min_lat, max_lat),
                min(min_lng, max_lng),
                max(min_lat, max_lat),
                max(min_lng, max_lng),
            )
        except ValueError:
            logging.error("Invalid --bbox format. Use min_lat,min_lng,max_lat,max_lng.")
            sys.exit(1)

    augment_pois(
        api_key=api_key_value,
        language=args.language,
        distance_threshold_km=args.distance_threshold,
        sleep_seconds=args.sleep,
        limit=args.limit,
        create_missing=args.create_missing,
        only_county=args.only_county,
        bbox=bbox,
    )


if __name__ == "__main__":
    main()


