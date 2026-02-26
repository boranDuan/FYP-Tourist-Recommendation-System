from flask import Flask, jsonify, redirect, render_template, send_from_directory, request, session
from dotenv import load_dotenv
from mysql import get_database_config, db, User, Favorite, Trip, TripPreference, POI, Filter, Itinerary
from datetime import datetime, date
import os
import re
import json
import webbrowser
import math
import copy
from preference_matching import (
    calculate_poi_score,
    calculate_final_score_with_popularity,
    filter_unwanted_pois,
    get_daily_poi_capacity,
    INTEREST_TO_FILTER_IDS,
)
from itinerary import (
    allocate_pois_to_days_v3_with_must_visit,
    allocate_pois_to_days_v4_popularity_first,
    format_itinerary_summary,
    optimize_route_greedy_tsp,
    get_poi_duration,
    PACE_TO_DAILY_POI_CAP,
)
from rule_based_filtering import apply_avoid_filter

load_dotenv()

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# 初始化数据库配置
get_database_config(app)

@app.route("/images/<path:filename>")
def images(filename):
    return send_from_directory("images", filename)

@app.route("/config")
def get_config():
    return jsonify({
        "GOOGLE_MAPS_API_KEY": os.getenv("GOOGLE_MAPS_API_KEY"),
        "OPENWEATHER_API_KEY": os.getenv("OPENWEATHER_API_KEY")
    })

@app.route("/")
def home():
    return redirect("/map")

@app.route("/map")
def map_page():
    return render_template("map.html")

@app.route("/api/register", methods=["POST"])
def register():
    """用户注册"""
    data = request.get_json()
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    # 验证输入
    if not username or not email or not password:
        return jsonify({'success': False, 'message': 'All fields are required'}), 400
    
    if len(password) < 6:
        return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
    
    # 检查用户名和邮箱是否已存在
    if User.query.filter_by(username=username).first():
        return jsonify({'success': False, 'message': 'Username already exists'}), 400
    
    if User.query.filter_by(email=email).first():
        return jsonify({'success': False, 'message': 'Email already exists'}), 400
    
    # 创建新用户
    try:
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        # 自动登录
        session['user_id'] = user.id
        session['username'] = user.username
        
        return jsonify({
            'success': True, 
            'message': 'Registration successful',
            'user': user.to_dict()
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Registration failed: ' + str(e)}), 500

@app.route("/api/login", methods=["POST"])
def login():
    """用户登录"""
    data = request.get_json()
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    
    if not email or not password:
        return jsonify({'success': False, 'message': 'Email and password are required'}), 400
    
    # 查找用户
    user = User.query.filter_by(email=email).first()
    
    if user and user.check_password(password):
        # 登录成功
        session['user_id'] = user.id
        session['username'] = user.username
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'user': user.to_dict()
        }), 200
    else:
        return jsonify({'success': False, 'message': 'Invalid email or password'}), 401

@app.route("/api/logout", methods=["POST"])
def logout():
    """用户登出"""
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'}), 200

@app.route("/api/user", methods=["GET"])
def get_user():
    """获取当前登录用户信息"""
    if 'user_id' in session:
        user = db.session.get(User, session['user_id'])
        if user:
            return jsonify({'success': True, 'user': user.to_dict()}), 200
    return jsonify({'success': False, 'user': None}), 200

@app.route("/api/guest", methods=["POST"])
def guest_mode():
    """访客模式"""
    session['guest'] = True
    return jsonify({'success': True, 'message': 'Browsing as guest'}), 200

# ========== 收藏功能 API ==========

@app.route("/api/favorites", methods=["POST"])
def add_favorite():
    """添加收藏"""
    # 检查用户是否登录
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    data = request.get_json()
    place_id = data.get('place_id', '').strip()
    place_name = data.get('place_name', '').strip()
    place_data = data.get('place_data')  # 完整的 Google Places 数据（可选）
    
    if not place_id or not place_name:
        return jsonify({'success': False, 'message': 'place_id and place_name are required'}), 400
    
    user_id = session['user_id']
    
    # 检查是否已收藏
    existing = Favorite.query.filter_by(user_id=user_id, place_id=place_id).first()
    if existing:
        return jsonify({'success': False, 'message': 'Already favorited'}), 400
    
    try:
        favorite = Favorite(
            user_id=user_id,
            place_id=place_id,
            place_name=place_name,
            place_data=place_data,
            created_at=datetime.now()
        )
        db.session.add(favorite)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Favorite added successfully',
            'favorite': favorite.to_dict()
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Failed to add favorite: ' + str(e)}), 500

@app.route("/api/favorites/<place_id>", methods=["DELETE"])
def remove_favorite(place_id):
    """取消收藏"""
    # 检查用户是否登录
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    user_id = session['user_id']
    
    favorite = Favorite.query.filter_by(user_id=user_id, place_id=place_id).first()
    if not favorite:
        return jsonify({'success': False, 'message': 'Favorite not found'}), 404
    
    try:
        db.session.delete(favorite)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Favorite removed successfully'
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Failed to remove favorite: ' + str(e)}), 500

@app.route("/api/favorites", methods=["GET"])
def get_favorites():
    """获取用户收藏列表"""
    # 检查用户是否登录
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please login first', 'favorites': []}), 401
    
    user_id = session['user_id']
    
    try:
        favorites = Favorite.query.filter_by(user_id=user_id).order_by(Favorite.created_at.desc()).all()
        return jsonify({
            'success': True,
            'favorites': [f.to_dict() for f in favorites]
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'message': 'Failed to get favorites: ' + str(e)}), 500

@app.route("/api/favorites/check/<place_id>", methods=["GET"])
def check_favorite(place_id):
    """检查是否已收藏"""
    # 检查用户是否登录
    if 'user_id' not in session:
        return jsonify({'success': True, 'is_favorited': False}), 200
    
    user_id = session['user_id']
    
    try:
        favorite = Favorite.query.filter_by(user_id=user_id, place_id=place_id).first()
        return jsonify({
            'success': True,
            'is_favorited': favorite is not None
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'message': 'Failed to check favorite: ' + str(e)}), 500

def _normalize_interests(raw):
    """将 TripPreference.interests 规范化为 {str: float}."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        # 兼容字符串存储的 JSON（当前应为 JSON 类型，这里预防一下）
        try:
            import json
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            out[str(k)] = 0.0
    return out


def _build_itinerary_content_json(result):
    """将 _compute_candidates_and_itinerary 的结果打包为可持久化 JSON。"""
    return {
        "pois": result.get("out", []),
        "must_visit_ids": result.get("must_visit_ids", []),
        "google_must_visits": result.get("google_must_visits", []),
        "daily_poi_capacity": result.get("daily_capacity"),
        "day_plans": result.get("day_plans", []),
        "warnings": result.get("warnings", []),
        "summary": result.get("summary", ""),
    }


def _persist_itinerary_if_missing(trip_id, result):
    """
    仅在该 trip 还没有 active itinerary 时写入 version=1（或 next version）。
    不覆盖已有 active 版本，避免影响当前行为。
    """
    if not result:
        return None
    active = (
        Itinerary.query
        .filter_by(trip_id=trip_id, is_active=True)
        .order_by(Itinerary.version.desc())
        .first()
    )
    if active:
        return active

    latest = (
        Itinerary.query
        .filter_by(trip_id=trip_id)
        .order_by(Itinerary.version.desc())
        .first()
    )
    next_version = (latest.version + 1) if latest else 1

    row = Itinerary(
        trip_id=trip_id,
        version=next_version,
        content_json=_build_itinerary_content_json(result),
        is_active=True,
    )
    db.session.add(row)
    db.session.commit()
    return row


# Haversine 距离（单位：km）
def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return r * c


# ========== 问卷校验 API ==========
# Q4、Q7、Q12 为可选，1b 为可选，其余必填

def validate_questionnaire_data(data):
    """校验问卷必填项，返回 (is_valid, errors_list)。Q4、Q7、Q12 可选，1b 可选。"""
    errors = []
    group_type = (data.get("group_type") or "").strip()
    num_people = data.get("num_people")
    budget_unit = (data.get("budget_unit") or "").strip()
    budget_value = (data.get("budget_value") or "").strip()
    visit_date = (data.get("visit_date") or "").strip()
    interests = data.get("interests") or []
    avoid = data.get("avoid") or []
    pace = (data.get("pace") or "").strip()
    start_time_unit = (data.get("start_time_unit") or "").strip()
    start_time_value = (data.get("start_time_value") or "").strip()
    food_preference = data.get("food_preference") or []

    if not group_type:
        errors.append("Q1: Please select how you will be traveling.")
    if budget_unit == "custom":
        if not (budget_value and str(budget_value).strip()):
            errors.append("Q3: Please enter your custom budget amount.")
    elif not budget_unit:
        errors.append("Q3: Please select your travel budget.")
    if not visit_date:
        errors.append("Q2: Please select your visit start date.")
    else:
        today = date.today().isoformat()
        if visit_date < today:
            errors.append("Q2: Start date cannot be in the past.")
        visit_date_end = (data.get("visit_date_end") or "").strip()
        if visit_date_end and visit_date_end < visit_date:
            errors.append("Q2: End date cannot be earlier than start date.")
    # interests: weight object {museum, culture, ...}, at least one > 0
    if not isinstance(interests, dict):
        errors.append("Q5: Please select at least one type of place or specify in Other.")
    else:
        has_interest = any(v and float(v) > 0 for v in interests.values())
        if not has_interest:
            errors.append("Q5: Please select at least one type of place or specify in Other.")
    if not pace:
        errors.append("Q8: Please select your travel pace.")
    if not start_time_unit:
        errors.append("Q9: Please select your preferred start time.")
    if start_time_unit == "custom" and not (start_time_value and str(start_time_value).strip()):
        errors.append("Q9: Please enter your custom start time.")
    if not food_preference or len(food_preference) == 0:
        errors.append("Q10: Please select at least one food type or specify in Other.")

    return (len(errors) == 0, errors)


@app.route("/api/questionnaire/validate", methods=["POST"])
def questionnaire_validate():
    """校验问卷数据，返回是否通过及错误列表"""
    data = request.get_json() or {}
    is_valid, errors = validate_questionnaire_data(data)
    if is_valid:
        return jsonify({"success": True}), 200
    return jsonify({"success": False, "errors": errors}), 400


@app.route("/api/trip/create", methods=["POST"])
def trip_create():
    """根据问卷数据创建 Trip + TripPreference，需登录"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Please log in to save your trip."}), 401

    data = request.get_json() or {}
    is_valid, errors = validate_questionnaire_data(data)
    if not is_valid:
        return jsonify({"success": False, "errors": errors}), 400

    budget_unit = (data.get("budget_unit") or "").strip()
    budget_value = str(data.get("budget_value") or "").strip()
    budget = None
    if budget_unit == "custom" and budget_value:
        budget = f"custom:{budget_value}€"
    elif budget_unit:
        budget = budget_unit

    def parse_date(s):
        if not s or not isinstance(s, str):
            return None
        s = s.strip()
        if not s:
            return None
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    visit_date = parse_date(data.get("visit_date"))
    visit_date_end = parse_date(data.get("visit_date_end"))

    st_unit = (data.get("start_time_unit") or "").strip()
    st_val = str(data.get("start_time_value") or "").strip()
    start_time = f"custom:{st_val}" if st_unit == "custom" and st_val else (st_unit if st_unit else None)

    def to_int(x):
        if x is None:
            return None
        try:
            return int(float(x))
        except (ValueError, TypeError):
            return None

    food_pref = data.get("food_preference") or []
    dietary = data.get("dietary_needs") or []
    food_str = ",".join(food_pref)[:255] if isinstance(food_pref, list) else str(food_pref)[:255]
    dietary_str = ",".join(dietary)[:255] if isinstance(dietary, list) else str(dietary)[:255]

    interests = data.get("interests")
    interests_data = interests if isinstance(interests, dict) else {}

    try:
        trip = Trip(user_id=user_id, status="active", is_saved=False)
        db.session.add(trip)
        db.session.flush()

        pref = TripPreference(
            trip_id=trip.trip_id,
            group_type=(data.get("group_type") or "").strip() or None,
            num_people=to_int(data.get("num_people")),
            num_children=to_int(data.get("num_children")),
            num_seniors=to_int(data.get("num_seniors")),
            trip_duration=None,
            budget=budget if budget_unit else None,
            hotel_budget=(data.get("hotel_budget_unit") or "").strip() or None,
            hotel_preferred_area=(data.get("hotel_preferred_area") or "").strip() or None,
            visit_date=visit_date,
            visit_date_end=visit_date_end,
            interests=interests_data,
            interests_other=(data.get("interests_other") or "").strip() or None,
            specific_places=(data.get("specific_places") or "").strip() or None,
            pace=(data.get("pace") or "").strip() or None,
            start_time=start_time,
            food_preference=food_str or None,
            dietary_needs=dietary_str or None,
            avoid=data.get("avoid") or [],
        )
        db.session.add(pref)
        db.session.commit()
        # 问卷提交后生成首版 itinerary，并持久化到 itineraries（version=1）
        try:
            generated = _compute_candidates_and_itinerary(pref)
            if generated:
                _persist_itinerary_if_missing(trip.trip_id, generated)
        except Exception:
            # 生成失败不阻断 trip 创建
            pass
        return jsonify({"success": True, "trip_id": trip.trip_id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


# ========== Preference Matching：按兴趣为 Trip 返回候选 POI ==========

def _get_trip_days(preference):
    """根据 visit_date 与 visit_date_end 计算行程天数。"""
    start = getattr(preference, 'visit_date', None)
    end = getattr(preference, 'visit_date_end', None)
    if not start or not end:
        return 3
    try:
        days = (end - start).days + 1
        return max(1, days)
    except (TypeError, AttributeError):
        return 3


DUBLIN_CENTER = (53.3498, -6.2603)


def apply_distance_decay(score, distance_km, center_type):
    """
    软距离衰减：
    - decay = exp(-distance_km / D)
    - floor: decay >= 0.1
    - must_visit 中心：3km 内不衰减
    - D: trip_center=6.0, dublin_fallback=8.0
    """
    try:
        base_score = float(score or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if base_score <= 0:
        return 0.0

    try:
        d = float(distance_km)
    except (TypeError, ValueError):
        return base_score
    if d < 0:
        return base_score

    if center_type == "must_visit" and d < 3.0:
        decay = 1.0
    else:
        d_const = 8.0 if center_type == "dublin_fallback" else 6.0
        decay = math.exp(-d / d_const)
        decay = max(0.1, decay)

    return base_score * decay


def _avg_coords(items):
    lats = []
    lngs = []
    for item in items or []:
        la = item.get("latitude")
        lo = item.get("longitude")
        if la is None or lo is None:
            continue
        try:
            lats.append(float(la))
            lngs.append(float(lo))
        except (TypeError, ValueError):
            continue
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


def _popularity_score_from_poi(poi):
    rating = getattr(poi, "google_rating", None)
    ratings_total = getattr(poi, "google_ratings_total", None)
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


def _passes_low_popularity_gate(poi, pop_score, base_score):
    """
    低知名度硬门槛（仅用于本地推荐候选）：
    - 默认要求 popularity_score >= 0.30 且 google_ratings_total >= 80
    - 若综合分非常高（base_score >= 0.75），允许放行，避免误伤强兴趣点
    阈值可通过环境变量调整：
    - MIN_POPULARITY_SCORE
    - MIN_GOOGLE_RATINGS_TOTAL
    - HIGH_BASE_SCORE_BYPASS
    """
    try:
        min_pop_score = float(os.getenv("MIN_POPULARITY_SCORE", "0.30"))
    except (TypeError, ValueError):
        min_pop_score = 0.30
    try:
        min_ratings_total = int(os.getenv("MIN_GOOGLE_RATINGS_TOTAL", "80"))
    except (TypeError, ValueError):
        min_ratings_total = 80
    try:
        high_score_bypass = float(os.getenv("HIGH_BASE_SCORE_BYPASS", "0.75"))
    except (TypeError, ValueError):
        high_score_bypass = 0.75

    if float(base_score or 0.0) >= high_score_bypass:
        return True

    raw_total = getattr(poi, "google_ratings_total", None)
    try:
        ratings_total = int(raw_total) if raw_total is not None else 0
    except (TypeError, ValueError):
        ratings_total = 0

    return float(pop_score or 0.0) >= min_pop_score and ratings_total >= min_ratings_total


def _compute_trip_center(google_must_visits, pois):
    must_center = _avg_coords(google_must_visits)
    if must_center is not None:
        return must_center, "must_visit"

    pop_ranked = []
    for poi in pois:
        if getattr(poi, "latitude", None) is None or getattr(poi, "longitude", None) is None:
            continue
        pop = _popularity_score_from_poi(poi)
        if pop > 0:
            pop_ranked.append((poi, pop))
    pop_ranked.sort(key=lambda x: x[1], reverse=True)

    top5 = [{
        "latitude": getattr(p, "latitude", None),
        "longitude": getattr(p, "longitude", None),
    } for p, _ in pop_ranked[:5]]
    pop_center = _avg_coords(top5)
    if pop_center is not None:
        return pop_center, "trip_center"

    return DUBLIN_CENTER, "dublin_fallback"


def _compute_candidates_and_itinerary(pref, limit=200):
    """
    根据 TripPreference 计算候选 POI 与分天行程。
    若无正权重兴趣返回 None，否则返回 dict(out, must_visit_ids, daily_capacity, day_plans, warnings, summary)。
    """
    interests = _normalize_interests(pref.interests)
    if not interests or not any(v and float(v) > 0 for v in interests.values()):
        return None

    try:
        import googlemaps
        gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY")) if os.getenv("GOOGLE_MAPS_API_KEY") else None
    except Exception:
        gmaps = None

    # Must-visit：直接用 Google Places 数据，不匹配本地 DB
    from preference_matching import resolve_specific_places_to_google_data
    google_must_visits = resolve_specific_places_to_google_data(
        getattr(pref, "specific_places", None) or "", gmaps_client=gmaps
    )
    for g in google_must_visits:
        g.setdefault("filter_ids", [])
        g.setdefault("score", None)
        g["source"] = "google"

    pois = POI.query.all()
    trip_center, center_type = _compute_trip_center(google_must_visits, pois)
    scored = []
    for poi in pois:
        poi_filter_ids = [f.filter_id for f in poi.filters]
        interest_score = calculate_poi_score(poi.poi_id, poi_filter_ids, interests)
        if interest_score > 0:
            base_score = calculate_final_score_with_popularity(
                interest_score,
                getattr(poi, "google_rating", None),
                getattr(poi, "google_ratings_total", None),
                interests,
            )
            pop_score = _popularity_score_from_poi(poi)
            if not _passes_low_popularity_gate(poi, pop_score, base_score):
                continue
            if poi.latitude is not None and poi.longitude is not None:
                try:
                    dist_to_trip_center = _haversine_km(
                        float(poi.latitude),
                        float(poi.longitude),
                        float(trip_center[0]),
                        float(trip_center[1]),
                    )
                except (TypeError, ValueError):
                    dist_to_trip_center = None
            else:
                dist_to_trip_center = None

            score = apply_distance_decay(base_score, dist_to_trip_center, center_type)
            # 低知名度软降权（不硬过滤）：减少“名气很弱”的点混入 day plan
            if pop_score < 0.25:
                score *= 0.85
            scored.append((poi, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    pois_data = []
    for poi, score in scored:
        pois_data.append({
            "poi_id": poi.poi_id,
            "place_id": getattr(poi, "google_place_id", None),
            "name": poi.name,
            "score": score,
            "filter_ids": [f.filter_id for f in poi.filters],
            "latitude": poi.latitude,
            "longitude": poi.longitude,
            "tags": poi.tags,
            "rating": poi.rating,
            "price_level": poi.price_level,
            "suitable_for_children": getattr(poi, "suitable_for_children", None),
            "suitable_for_seniors": getattr(poi, "suitable_for_seniors", None),
        })

    num_children = (pref.num_children or 0) if pref else 0
    num_seniors = (pref.num_seniors or 0) if pref else 0
    if num_children > 0 or num_seniors > 0:
        pois_data = [
            p for p in pois_data
            if (num_children == 0 or p.get("suitable_for_children") is True or p.get("suitable_for_children") is None)
            and (num_seniors == 0 or p.get("suitable_for_seniors") is True or p.get("suitable_for_seniors") is None)
        ]

    avoid_list = pref.avoid if pref and pref.avoid and isinstance(pref.avoid, list) else []
    if avoid_list:
        all_filters = [{"id": f.filter_id, "name": f.filter_name} for f in Filter.query.all()]
        pois_data = apply_avoid_filter(poi_list=pois_data, avoid_list=avoid_list, all_filters=all_filters)

    pois_data = filter_unwanted_pois(pois_data)

    # 邻域去重半径（km）：用于避免 must-visit 与推荐点、推荐点彼此重复
    try:
        must_visit_dedup_radius_km = float(os.getenv("MUST_VISIT_DEDUP_RADIUS_KM", "0.35"))
    except (TypeError, ValueError):
        must_visit_dedup_radius_km = 0.35

    # 推荐全局邻域去重半径（默认沿用 must-visit 半径）
    try:
        recommend_dedup_radius_km = float(
            os.getenv("RECOMMEND_DEDUP_RADIUS_KM", str(must_visit_dedup_radius_km))
        )
    except (TypeError, ValueError):
        recommend_dedup_radius_km = must_visit_dedup_radius_km

    def _nearby_keep_key(poi):
        """
        邻域冲突时的保留优先级（高 -> 低）：
        1) Google 点优先（避免误删 must-visit）
        2) 地标主点关键词优先（定向修复 Trinity 被子点挤掉）
        3) score 更高优先
        """
        name = _canonical_poi_name(poi.get("name"))
        landmark_hit = 1 if "trinity college" in name else 0
        is_google = 1 if poi.get("source") == "google" else 0
        score = poi.get("score")
        try:
            score = float(score) if score is not None else -1.0
        except (TypeError, ValueError):
            score = -1.0
        return (is_google, landmark_hit, score)

    final_pois = list(google_must_visits)
    for p in pois_data:
        p["source"] = "local"
        # 邻域冲突改为“挑代表点”，不再先到先得。
        conflict_indices = [
            i for i, kept in enumerate(final_pois)
            if _is_same_location(p, kept, threshold_km=recommend_dedup_radius_km)
        ]
        if not conflict_indices:
            final_pois.append(p)
            continue

        conflict_pois = [final_pois[i] for i in conflict_indices] + [p]
        best = max(conflict_pois, key=_nearby_keep_key)
        if best is p:
            # 新点更适合作为代表点：替换掉冲突旧点
            for i in sorted(conflict_indices, reverse=True):
                removed = final_pois.pop(i)
                if _dedup_debug_enabled():
                    _dedup_debug(
                        f"replace nearby keep='{p.get('name')}' drop='{removed.get('name')}' radius_km={recommend_dedup_radius_km}"
                    )
            final_pois.append(p)
        else:
            if _dedup_debug_enabled():
                _dedup_debug(
                    f"skip nearby-to-selected local='{p.get('name')}' keep='{best.get('name')}' radius_km={recommend_dedup_radius_km}"
                )
    # 同名族分组 + 空间聚类去重
    final_pois = _dedupe_pois_identity_geo(final_pois)
    final_pois = final_pois[:limit]

    google_place_ids = [g["place_id"] for g in google_must_visits]

    out = []
    for p in final_pois:
        out.append({
            "poi_id": p.get("poi_id"),
            "place_id": p.get("place_id"),
            "name": p.get("name"),
            "score": p.get("score"),
            "latitude": p.get("latitude"),
            "longitude": p.get("longitude"),
            "tags": p.get("tags"),
            "rating": p.get("rating"),
            "price_level": p.get("price_level"),
            "source": p.get("source"),
        })

    daily_capacity = get_daily_poi_capacity(pref.pace, pref.num_children or 0, pref.num_seniors or 0)
    trip_days = _get_trip_days(pref)
    use_v4 = os.getenv("USE_V4_ALLOCATION", "true").lower() in ("1", "true", "yes")
    allocator = allocate_pois_to_days_v4_popularity_first if use_v4 else allocate_pois_to_days_v3_with_must_visit
    day_plans, warnings = allocator(final_pois, google_place_ids, pref, trip_days)
    summary = format_itinerary_summary(day_plans, trip_days)

    return {
        "out": out,
        "must_visit_ids": google_place_ids,
        "google_must_visits": google_must_visits,
        "daily_capacity": daily_capacity,
        "day_plans": day_plans,
        "warnings": warnings,
        "summary": summary,
    }


def _is_same_location(poi1, poi2, threshold_km=0.1):
    """判断两个 POI 是否同一地点（基于距离）。"""
    lat1, lng1 = poi1.get("latitude"), poi1.get("longitude")
    lat2, lng2 = poi2.get("latitude"), poi2.get("longitude")
    if None in (lat1, lng1, lat2, lng2):
        return False
    try:
        dist = _haversine_km(float(lat1), float(lng1), float(lat2), float(lng2))
        return dist < threshold_km
    except (TypeError, ValueError):
        return False


def _canonical_poi_name(name):
    """
    规范化 POI 名称用于去重：
    - 小写
    - 取逗号前主名称（例如 "National Museum of Ireland, Kildare Street" -> "national museum of ireland"）
    """
    if not name:
        return ""
    base = str(name).split(",", 1)[0].strip().lower()
    base = re.sub(r"\s+", " ", base)
    return base


def _dedup_debug_enabled():
    return os.getenv("DEBUG_DEDUP", "").lower() in ("1", "true", "yes", "on")


def _dedup_debug(msg):
    if _dedup_debug_enabled():
        app.logger.info(f"[dedup] {msg}")


def _dedupe_pois_identity_geo(pois):
    """
    同名族分组 + 空间聚类（核心）：
    1) 先按身份硬去重（place_id / poi_id）
    2) 对本地 POI 按“主名称（逗号前）”分组
    3) 每个名称族内按距离聚类；同簇仅保留一个代表点
       - 不同簇保留（例如同品牌不同馆）
       - 代表点优先 score 更高
    """
    CLUSTER_RADIUS_KM = 0.8
    # 全局去重半径（保守）：仅用于“跨名称/跨 source”的近重复清理
    GLOBAL_NEAR_DUP_KM = 0.12

    # Step 1: 身份硬去重（保序）
    seen_place_ids = set()
    seen_poi_ids = set()
    unique = []
    dropped_by_id = 0
    for poi in pois:
        place_id = poi.get("place_id")
        poi_id = poi.get("poi_id")

        if place_id:
            if place_id in seen_place_ids:
                dropped_by_id += 1
                continue
            seen_place_ids.add(place_id)
            unique.append(poi)
            continue

        if poi_id is not None:
            if poi_id in seen_poi_ids:
                dropped_by_id += 1
                continue
            seen_poi_ids.add(poi_id)
            unique.append(poi)
            continue

        unique.append(poi)

    # Step 2: 同名族分组 + 空间聚类（仅处理 local，避免误并 must-visit Google 点）
    grouped = {}
    for idx, poi in enumerate(unique):
        if poi.get("source") == "google":
            continue
        family = _canonical_poi_name(poi.get("name"))
        if not family:
            continue
        grouped.setdefault(family, []).append((idx, poi))

    drop_indices = set()
    merge_logs = []
    for members in grouped.values():
        if len(members) <= 1:
            continue

        clusters = []  # [{"indices":[...], "pois":[...]}]
        for idx, poi in members:
            lat, lng = poi.get("latitude"), poi.get("longitude")
            if lat is None or lng is None:
                clusters.append({"indices": [idx], "pois": [poi]})
                continue

            placed = False
            for c in clusters:
                # 单链路：与簇内任一点足够近就入簇
                if any(_is_same_location(poi, cp, threshold_km=CLUSTER_RADIUS_KM) for cp in c["pois"]):
                    c["indices"].append(idx)
                    c["pois"].append(poi)
                    placed = True
                    break

            if not placed:
                clusters.append({"indices": [idx], "pois": [poi]})

        # 每个簇只留一个代表点：优先 score 高；同分保留先出现
        for c in clusters:
            if len(c["indices"]) <= 1:
                continue

            best_idx = c["indices"][0]
            best_score = unique[best_idx].get("score")
            best_score = float(best_score) if best_score is not None else -1.0

            for idx in c["indices"][1:]:
                cur_score = unique[idx].get("score")
                cur_score = float(cur_score) if cur_score is not None else -1.0
                if cur_score > best_score:
                    best_idx = idx
                    best_score = cur_score

            for idx in c["indices"]:
                if idx != best_idx:
                    drop_indices.add(idx)
                    if _dedup_debug_enabled() and len(merge_logs) < 30:
                        merge_logs.append(
                            f"merge family='{_canonical_poi_name(unique[idx].get('name'))}' "
                            f"keep='{unique[best_idx].get('name')}' drop='{unique[idx].get('name')}'"
                        )
    result = [poi for i, poi in enumerate(unique) if i not in drop_indices]

    # Step 3: 全局空间去重（跨 source、跨名称）
    # 仅在非常近的情况下合并，避免把城市中心相邻但独立的景点误并。
    global_drop_indices = set()
    clusters = []  # [{"indices":[...], "pois":[...]}]
    for idx, poi in enumerate(result):
        lat, lng = poi.get("latitude"), poi.get("longitude")
        if lat is None or lng is None:
            continue

        placed = False
        for c in clusters:
            if any(_is_same_location(poi, cp, threshold_km=GLOBAL_NEAR_DUP_KM) for cp in c["pois"]):
                c["indices"].append(idx)
                c["pois"].append(poi)
                placed = True
                break
        if not placed:
            clusters.append({"indices": [idx], "pois": [poi]})

    def _pick_representative_idx(indices):
        # 优先保留 google（通常是 must-visit 或用户指定点），其次 score 高，最后保留先出现
        best_idx = indices[0]
        best = result[best_idx]
        best_key = (
            1 if best.get("source") == "google" else 0,
            float(best.get("score")) if best.get("score") is not None else -1.0,
            -best_idx,
        )
        for idx in indices[1:]:
            cur = result[idx]
            cur_key = (
                1 if cur.get("source") == "google" else 0,
                float(cur.get("score")) if cur.get("score") is not None else -1.0,
                -idx,
            )
            if cur_key > best_key:
                best_idx = idx
                best_key = cur_key
        return best_idx

    for c in clusters:
        if len(c["indices"]) <= 1:
            continue
        keep_idx = _pick_representative_idx(c["indices"])
        for idx in c["indices"]:
            if idx != keep_idx:
                global_drop_indices.add(idx)
                if _dedup_debug_enabled() and len(merge_logs) < 30:
                    merge_logs.append(
                        f"merge global keep='{result[keep_idx].get('name')}' drop='{result[idx].get('name')}'"
                    )

    final_result = [poi for i, poi in enumerate(result) if i not in global_drop_indices]
    if _dedup_debug_enabled():
        _dedup_debug(
            f"input={len(pois)} after_id={len(unique)} dropped_by_id={dropped_by_id} "
            f"dropped_by_cluster={len(drop_indices)} dropped_by_global={len(global_drop_indices)} "
            f"output={len(final_result)} family_radius_km={CLUSTER_RADIUS_KM} global_radius_km={GLOBAL_NEAR_DUP_KM}"
        )
        for line in merge_logs:
            _dedup_debug(line)
    return final_result


@app.route("/api/trips/<int:trip_id>/candidates", methods=["GET"])
def get_interest_ranked_pois_for_trip(trip_id):
    """根据 TripPreference.interests 为指定 Trip 返回按兴趣排序的候选 POI 列表。"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Please log in first."}), 401

    trip = db.session.get(Trip, trip_id)
    if not trip:
        return jsonify({"success": False, "message": "Trip not found."}), 404
    if trip.user_id != user_id:
        return jsonify({"success": False, "message": "You do not have access to this trip."}), 403

    pref = trip.preference
    if not pref:
        return jsonify({"success": True, "trip_id": trip_id, "pois": []}), 200

    limit = request.args.get("limit", default=200, type=int)
    if not limit or limit <= 0:
        limit = 200

    try:
        result = _compute_candidates_and_itinerary(pref, limit=limit)
        if not result:
            return jsonify({"success": True, "trip_id": trip_id, "pois": []}), 200

        # 兜底：若历史 trip 还没有首版 itinerary，这里补写一条 active 版本
        try:
            _persist_itinerary_if_missing(trip_id, result)
        except Exception:
            # 不影响主流程返回
            pass

        return jsonify({
            "success": True,
            "trip_id": trip_id,
            "pois": result["out"],
            "must_visit_ids": result["must_visit_ids"],
            "google_must_visits": result.get("google_must_visits", []),
            "daily_poi_capacity": result["daily_capacity"],
            "day_plans": result["day_plans"],
            "warnings": result["warnings"],
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ========== AI 对话 API（OpenAI GPT-4.1 mini）==========

def _extract_json_object(text):
    """从 LLM 输出中提取 JSON 对象（兼容 ```json ... ``` 包裹）。"""
    s = (text or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _llm_parse_itinerary_edit(user_text):
    """
    将用户自然语言修改请求解析为结构化指令（仅解析，不执行）。
    返回 dict:
      intent, day, poi_name, target_day, constraints, confidence, needs_clarification, clarification_question
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, "AI parser is not configured (missing OPENAI_API_KEY)"
    if OpenAI is None:
        return None, "OpenAI client not installed"

    parser_system = """You are an itinerary edit intent parser.
Convert user request into ONE strict JSON object only (no prose).

Allowed intents:
- remove_poi
- add_poi
- replace_poi
- move_poi
- swap_poi
- reroute_day
- unknown

Output schema (always include all keys):
{
  "intent": "remove_poi|add_poi|replace_poi|move_poi|swap_poi|reroute_day|unknown",
  "day": <number|null>,
  "poi_name": <string|null>,
  "target_day": <number|null>,
  "constraints": {
    "nearby": <boolean>,
    "same_type": <string|null>,
    "avoid_type": <string|null>
  },
  "confidence": <number 0.0-1.0>,
  "needs_clarification": <boolean>,
  "clarification_question": <string|null>
}

Rules:
- If ambiguous, set needs_clarification=true and ask one short question.
- Do not invent POI names.
- Use null when value is not provided.
"""
    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": parser_system},
                {"role": "user", "content": user_text or ""},
            ],
            max_tokens=320,
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = _extract_json_object(raw)
        if not parsed:
            return None, "Failed to parse JSON from LLM response"

        # 轻度兜底，保证关键字段存在
        parsed.setdefault("intent", "unknown")
        parsed.setdefault("day", None)
        parsed.setdefault("poi_name", None)
        parsed.setdefault("target_day", None)
        parsed.setdefault("constraints", {})
        parsed.setdefault("confidence", 0.0)
        parsed.setdefault("needs_clarification", parsed.get("intent") == "unknown")
        parsed.setdefault("clarification_question", None)
        parsed.setdefault("clarification_type", None)
        parsed.setdefault("clarification_options", None)

        # UX 策略：用户只说“不要某点”时，默认追问“只删除还是删除并替换”
        intent = (parsed.get("intent") or "").strip()
        txt = (user_text or "").lower()
        explicit_replace = bool(re.search(r"\b(replace|instead|swap|substitute)\b|换成|替换|改成|换一个", txt))
        if intent == "remove_poi" and not explicit_replace and not parsed.get("needs_clarification"):
            parsed["needs_clarification"] = True
            parsed["clarification_type"] = "remove_or_replace"
            parsed["clarification_options"] = ["remove_only", "replace_nearby_same_type"]
            parsed["clarification_question"] = (
                "Do you want to remove it only, or remove and replace it with a similar nearby POI?"
            )
        return parsed, None
    except Exception as e:
        return None, str(e)


def _norm_poi_name(name):
    s = str(name or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _find_day_plan(day_plans, day_num):
    for plan in (day_plans or []):
        if Number := plan.get("day"):
            try:
                if int(Number) == int(day_num):
                    return plan
            except Exception:
                continue
    return None


def _pick_poi_index_by_name(pois, target_name):
    target = _norm_poi_name(target_name)
    if not target:
        return -1
    best_idx, best_score = _pick_poi_match(pois, target)
    return best_idx if best_score > 0 else -1


def _pick_poi_match(pois, target_name):
    target = _norm_poi_name(target_name)
    if not target:
        return -1, -1
    target_tokens = [t for t in target.split(" ") if t]
    best_idx = -1
    best_score = -1
    for i, p in enumerate(pois or []):
        cur = _norm_poi_name((p or {}).get("name"))
        if not cur:
            continue
        cur_tokens = [t for t in cur.split(" ") if t]
        score = 0
        if cur == target:
            score = 3
        elif target in cur or cur in target:
            # 单词级短词（如 "dublin"）命中太宽松，降为弱匹配，避免误判“多天同一 POI”
            if len(target_tokens) <= 1 or len(target) < 6:
                score = 1
            else:
                score = 2
        elif any(tok and tok in cur for tok in target.split(" ")):
            score = 1
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx, best_score


def _remove_poi_from_plan(plan, poi_name):
    pois = list(plan.get("pois") or [])
    idx = _pick_poi_index_by_name(pois, poi_name)
    if idx < 0:
        return None
    removed = pois.pop(idx)
    if len(pois) >= 2:
        pois = optimize_route_greedy_tsp(pois)
    plan["pois"] = pois
    plan["must_visit_count"] = sum(1 for p in pois if (p or {}).get("is_must_visit"))
    plan["total_hours"] = round(sum(get_poi_duration(p or {}) for p in pois), 1)
    return removed


def _pop_poi_from_plan(plan, poi_name):
    """从指定 day plan 移除并返回该 poi（不做补位，仅重排本天）。"""
    pois = list(plan.get("pois") or [])
    idx = _pick_poi_index_by_name(pois, poi_name)
    if idx < 0:
        return None
    popped = pois.pop(idx)
    plan["pois"] = pois
    _recompute_plan_meta(plan)
    return popped


def _append_poi_to_plan_with_constraints(target_plan, poi, pref):
    """
    将 poi 加入目标天：
    - 不超过 daily cap
    - 简单时长约束（沿用现有软上限估计）
    """
    if not isinstance(target_plan, dict) or not isinstance(poi, dict):
        return False, "invalid input"
    pois = list(target_plan.get("pois") or [])
    cap = _daily_poi_cap_from_pref(pref)
    if len(pois) >= cap:
        return False, f"target day already reached cap ({cap})"

    pace = ((getattr(pref, "pace", None) or "balanced") if pref else "balanced").lower()
    daily_hours_map = {"relaxed": 5.0, "balanced": 6.5, "intensive": 8.0}
    daily_hours = float(daily_hours_map.get(pace, 6.5))
    soft_day_hour_cap = max(daily_hours, cap * 2.5)
    day_hours = sum(get_poi_duration(p or {}) for p in pois)
    add_hours = get_poi_duration(poi or {})
    if day_hours + add_hours > soft_day_hour_cap:
        return False, "target day soft hour limit exceeded"

    pois.append(poi)
    target_plan["pois"] = pois
    _recompute_plan_meta(target_plan)
    return True, None


def _save_new_itinerary_version(trip_id, content_json):
    active = (
        Itinerary.query
        .filter_by(trip_id=trip_id, is_active=True)
        .order_by(Itinerary.version.desc())
        .first()
    )
    latest = (
        Itinerary.query
        .filter_by(trip_id=trip_id)
        .order_by(Itinerary.version.desc())
        .first()
    )
    next_version = (latest.version + 1) if latest else 1
    if active:
        active.is_active = False

    row = Itinerary(
        trip_id=trip_id,
        version=next_version,
        content_json=content_json,
        is_active=True,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _poi_uid(poi):
    if not isinstance(poi, dict):
        return None
    return poi.get("place_id") or poi.get("poi_id")


def _plan_center_from_pois(pois):
    lats = []
    lngs = []
    for p in pois or []:
        la = (p or {}).get("latitude")
        lo = (p or {}).get("longitude")
        if la is None or lo is None:
            continue
        try:
            lats.append(float(la))
            lngs.append(float(lo))
        except (TypeError, ValueError):
            continue
    if not lats:
        return DUBLIN_CENTER
    return (sum(lats) / len(lats), sum(lngs) / len(lngs))


def _minimum_daily_target(pref):
    pace = ((getattr(pref, "pace", None) or "balanced") if pref else "balanced").lower()
    if pace == "balanced":
        return 3
    return 0


def _daily_poi_cap_from_pref(pref):
    pace = ((getattr(pref, "pace", None) or "balanced") if pref else "balanced").lower()
    return int(PACE_TO_DAILY_POI_CAP.get(pace, 4))


def _recompute_plan_meta(plan):
    pois = list((plan or {}).get("pois") or [])
    if len(pois) >= 2:
        pois = optimize_route_greedy_tsp(pois)
    plan["pois"] = pois
    plan["must_visit_count"] = sum(1 for p in pois if (p or {}).get("is_must_visit"))
    plan["total_hours"] = round(sum(get_poi_duration(p or {}) for p in pois), 1)


def _candidate_rank_for_refill(candidate, center):
    # 分数优先，距离次优（0~1 归一化）
    score = _safe_float((candidate or {}).get("score"), 0.0)
    la = _safe_float((candidate or {}).get("latitude"), None)
    lo = _safe_float((candidate or {}).get("longitude"), None)
    if la is None or lo is None:
        return score * 0.7
    dist_km = _haversine_km(float(la), float(lo), float(center[0]), float(center[1]))
    dist_fit = max(0.0, 1.0 - min(10.0, dist_km) / 10.0)
    return score * 0.7 + dist_fit * 0.3


def _refill_day_plan_from_pool(plan, day_plans, pool, pref, banned_uids=None, dedup_km=0.35):
    """
    删除后补位（局部）：
    - 仅补到最低目标（balanced>=3）
    - 候选来自 pool
    - 满足：不重复、不在 banned、同城/同日半径、不近重复
    """
    banned_uids = set(banned_uids or [])
    min_target = _minimum_daily_target(pref)
    if min_target <= 0:
        return []

    day_pois = list(plan.get("pois") or [])
    if len(day_pois) >= min_target:
        return []

    center = _plan_center_from_pois(day_pois)
    used_uids = set()
    all_existing = []
    for dp in day_plans or []:
        for p in (dp.get("pois") or []):
            all_existing.append(p)
            uid = _poi_uid(p)
            if uid:
                used_uids.add(uid)

    added = []
    while len(day_pois) < min_target:
        candidates = []
        for c in pool or []:
            uid = _poi_uid(c)
            if uid and (uid in used_uids or uid in banned_uids):
                continue
            la = (c or {}).get("latitude")
            lo = (c or {}).get("longitude")
            if la is None or lo is None:
                continue
            try:
                city_dist = _haversine_km(float(la), float(lo), float(DUBLIN_CENTER[0]), float(DUBLIN_CENTER[1]))
                day_dist = _haversine_km(float(la), float(lo), float(center[0]), float(center[1]))
            except (TypeError, ValueError):
                continue
            if city_dist > 8.0:
                continue
            if day_dist > 10.0:
                continue
            # 近重复排除（与当前行程任一点过近则跳过）
            if any(_is_same_location(c, ep, threshold_km=dedup_km) for ep in all_existing):
                continue
            candidates.append(c)

        if not candidates:
            break
        best = max(candidates, key=lambda x: _candidate_rank_for_refill(x, center))
        day_pois.append(best)
        added.append(best)
        uid = _poi_uid(best)
        if uid:
            used_uids.add(uid)
        all_existing.append(best)
        center = _plan_center_from_pois(day_pois)

    if len(day_pois) >= 2:
        day_pois = optimize_route_greedy_tsp(day_pois)
    plan["pois"] = day_pois
    plan["must_visit_count"] = sum(1 for p in day_pois if (p or {}).get("is_must_visit"))
    plan["total_hours"] = round(sum(get_poi_duration(p or {}) for p in day_pois), 1)
    return added


def _same_type_filter_ids_from_constraints(constraints):
    """将 replace 约束的 same_type 转成 filter_ids 集合（为空则不限制类型）。"""
    if not isinstance(constraints, dict):
        return set()
    t = str(constraints.get("same_type") or "").strip().lower()
    if not t:
        return set()
    mapping = {
        "museum": set(INTEREST_TO_FILTER_IDS.get("museum", [])),
        "nature": set(INTEREST_TO_FILTER_IDS.get("nature", [])),
        "culture": set(INTEREST_TO_FILTER_IDS.get("culture", [])),
        "shopping": set(INTEREST_TO_FILTER_IDS.get("shopping", [])),
    }
    return mapping.get(t, set())


def _related_type_filter_ids(same_type):
    """
    replace 的相近类型放宽：
    museum <-> culture，nature -> culture，shopping -> culture
    """
    t = str(same_type or "").strip().lower()
    if not t:
        return set()
    if t == "museum":
        keys = ["museum", "culture"]
    elif t == "culture":
        keys = ["culture", "museum"]
    elif t == "nature":
        keys = ["nature", "culture"]
    elif t == "shopping":
        keys = ["shopping", "culture"]
    else:
        keys = [t]
    out = set()
    for k in keys:
        out |= set(INTEREST_TO_FILTER_IDS.get(k, []))
    return out


def _replace_one_poi_in_plan(plan, day_plans, pool, pref, removed_poi, constraints=None, dedup_km=0.35):
    """
    replace_poi 核心：删除后尽量补回 1 个。
    - nearby=true: 优先 day anchor 附近（已体现在 rank + 半径约束）
    - same_type: 若提供则候选需同大类
    """
    constraints = constraints or {}
    same_type = str((constraints or {}).get("same_type") or "").strip().lower()
    same_type_fids = _same_type_filter_ids_from_constraints(constraints)
    related_type_fids = _related_type_filter_ids(same_type)

    day_pois = list(plan.get("pois") or [])
    center = _plan_center_from_pois(day_pois)

    used_uids = set()
    all_existing = []
    for dp in day_plans or []:
        for p in (dp.get("pois") or []):
            all_existing.append(p)
            uid = _poi_uid(p)
            if uid:
                used_uids.add(uid)

    banned = {_poi_uid(removed_poi)} if isinstance(removed_poi, dict) else set()

    def _collect_candidates(max_city_km, max_day_km, type_fids=None):
        candidates = []
        for c in pool or []:
            uid = _poi_uid(c)
            if uid and (uid in used_uids or uid in banned):
                continue
            la = (c or {}).get("latitude")
            lo = (c or {}).get("longitude")
            if la is None or lo is None:
                continue
            try:
                city_dist = _haversine_km(float(la), float(lo), float(DUBLIN_CENTER[0]), float(DUBLIN_CENTER[1]))
                day_dist = _haversine_km(float(la), float(lo), float(center[0]), float(center[1]))
            except (TypeError, ValueError):
                continue
            if city_dist > max_city_km or day_dist > max_day_km:
                continue
            if any(_is_same_location(c, ep, threshold_km=dedup_km) for ep in all_existing):
                continue
            if type_fids:
                c_fids = set((c or {}).get("filter_ids") or [])
                if not (c_fids & type_fids):
                    continue
            candidates.append(c)
        return candidates

    # 分层放宽，尽量“必替换成功”
    fallback_levels = []
    if same_type_fids:
        fallback_levels.append((8.0, 10.0, same_type_fids))      # 严格：同类型 + 近邻
        fallback_levels.append((10.0, 14.0, same_type_fids))     # 放宽半径仍同类型
    if related_type_fids and related_type_fids != same_type_fids:
        fallback_levels.append((10.0, 14.0, related_type_fids))  # 放宽为相近类型
    fallback_levels.append((12.0, 18.0, set()))                  # 不限类型，仍同城近邻
    fallback_levels.append((20.0, 30.0, set()))                  # 最终兜底：城市范围放宽

    best = None
    for max_city_km, max_day_km, type_fids in fallback_levels:
        candidates = _collect_candidates(max_city_km, max_day_km, type_fids)
        if not candidates:
            continue
        best = max(candidates, key=lambda x: _candidate_rank_for_refill(x, center))
        break
    if not best:
        return None

    day_pois.append(best)
    if len(day_pois) >= 2:
        day_pois = optimize_route_greedy_tsp(day_pois)
    plan["pois"] = day_pois
    plan["must_visit_count"] = sum(1 for p in day_pois if (p or {}).get("is_must_visit"))
    plan["total_hours"] = round(sum(get_poi_duration(p or {}) for p in day_pois), 1)
    return best


@app.route("/api/trips/<int:trip_id>/itinerary/parse-edit", methods=["POST"])
def parse_itinerary_edit(trip_id):
    """
    MVP: 仅做“先调 LLM 解析”。
    输入: { "user_text": "Day3 I don't want GPO Museum" }
    输出: 结构化 intent JSON（不执行修改）。
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Please log in first."}), 401

    trip = db.session.get(Trip, trip_id)
    if not trip:
        return jsonify({"success": False, "message": "Trip not found."}), 404
    if trip.user_id != user_id:
        return jsonify({"success": False, "message": "You do not have access to this trip."}), 403

    data = request.get_json() or {}
    user_text = (data.get("user_text") or "").strip()
    if not user_text:
        return jsonify({"success": False, "message": "user_text is required"}), 400

    parsed, err = _llm_parse_itinerary_edit(user_text)
    if err:
        return jsonify({"success": False, "message": err}), 500

    return jsonify({
        "success": True,
        "trip_id": trip_id,
        "parsed": parsed,
        "message": "Parsed only. No itinerary change applied.",
    }), 200


@app.route("/api/trips/<int:trip_id>/itinerary/apply-edit", methods=["POST"])
def apply_itinerary_edit(trip_id):
    """
    第二阶段 MVP：执行局部修改（当前先支持 remove_poi）。
    入参支持二选一：
    1) { "parsed": {...} }
    2) { "user_text": "..." }  # 后端先解析再执行
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Please log in first."}), 401

    trip = db.session.get(Trip, trip_id)
    if not trip:
        return jsonify({"success": False, "message": "Trip not found."}), 404
    if trip.user_id != user_id:
        return jsonify({"success": False, "message": "You do not have access to this trip."}), 403

    data = request.get_json() or {}
    parsed = data.get("parsed") if isinstance(data.get("parsed"), dict) else None
    if not parsed:
        user_text = (data.get("user_text") or "").strip()
        if not user_text:
            return jsonify({"success": False, "message": "parsed or user_text is required"}), 400
        parsed, err = _llm_parse_itinerary_edit(user_text)
        if err:
            return jsonify({"success": False, "message": err}), 500

    if parsed.get("needs_clarification"):
        return jsonify({
            "success": True,
            "applied": False,
            "trip_id": trip_id,
            "needs_clarification": True,
            "clarification_question": parsed.get("clarification_question") or "Could you clarify your request?",
            "clarification_type": parsed.get("clarification_type"),
            "clarification_options": parsed.get("clarification_options"),
            "parsed": parsed,
        }), 200

    intent = (parsed.get("intent") or "").strip()
    if intent not in ("remove_poi", "replace_poi", "move_poi"):
        return jsonify({
            "success": False,
            "message": f"Unsupported intent for MVP apply-edit: {intent or 'unknown'} (currently supports remove_poi/replace_poi/move_poi)",
            "parsed": parsed,
        }), 400

    day = parsed.get("day")
    poi_name = (parsed.get("poi_name") or "").strip()
    if not poi_name:
        return jsonify({"success": False, "message": "poi_name is required for remove_poi/replace_poi/move_poi", "parsed": parsed}), 400

    # 保证有 active itinerary 可编辑
    active = (
        Itinerary.query
        .filter_by(trip_id=trip_id, is_active=True)
        .order_by(Itinerary.version.desc())
        .first()
    )
    if not active:
        pref = trip.preference
        if not pref:
            return jsonify({"success": False, "message": "Trip has no preference"}), 400
        generated = _compute_candidates_and_itinerary(pref)
        if not generated:
            return jsonify({"success": False, "message": "No itinerary generated for this trip"}), 400
        _persist_itinerary_if_missing(trip_id, generated)
        active = (
            Itinerary.query
            .filter_by(trip_id=trip_id, is_active=True)
            .order_by(Itinerary.version.desc())
            .first()
        )
        if not active:
            return jsonify({"success": False, "message": "Failed to initialize active itinerary"}), 500

    content = copy.deepcopy(active.content_json or {})
    day_plans = content.get("day_plans")
    if not isinstance(day_plans, list):
        return jsonify({"success": False, "message": "Current itinerary content is invalid (missing day_plans)"}), 500

    removed = None
    applied_day = None
    added = []
    move_to_day = parsed.get("target_day")
    if move_to_day is not None:
        try:
            move_to_day = int(move_to_day)
        except (TypeError, ValueError):
            move_to_day = None

    # 先定位 source day/plan
    source_plan = None
    if day is not None:
        source_plan = _find_day_plan(day_plans, day)
        if not source_plan:
            return jsonify({"success": False, "message": f"Day {day} not found in current itinerary"}), 400
    else:
        strong_matches = []
        weak_matches = []
        for dp in day_plans:
            idx, score = _pick_poi_match(dp.get("pois") or [], poi_name)
            if idx >= 0:
                if score >= 2:
                    strong_matches.append(dp)
                else:
                    weak_matches.append(dp)
        if len(strong_matches) == 0 and len(weak_matches) == 0:
            return jsonify({"success": False, "message": f"POI not found: {poi_name}"}), 404
        if len(strong_matches) > 1:
            days = [m.get("day") for m in strong_matches]
            return jsonify({
                "success": True,
                "applied": False,
                "needs_clarification": True,
                "clarification_question": f"'{poi_name}' appears in multiple days {days}. Which day do you want to edit?",
                "parsed": parsed,
            }), 200
        if len(strong_matches) == 1:
            source_plan = strong_matches[0]
        elif len(weak_matches) == 1:
            source_plan = weak_matches[0]
        else:
            days = [m.get("day") for m in weak_matches]
            return jsonify({
                "success": True,
                "applied": False,
                "needs_clarification": True,
                "clarification_question": (
                    f"I found multiple possible POIs matching '{poi_name}' in days {days}. "
                    "Please tell me the day number or full POI name."
                ),
                "parsed": parsed,
            }), 200

    # move_poi: 先从 source pop，再 append 到 target
    if intent == "move_poi":
        if move_to_day is None:
            return jsonify({
                "success": True,
                "applied": False,
                "needs_clarification": True,
                "clarification_question": "Which target day do you want to move it to?",
                "parsed": parsed,
            }), 200
        target_plan = _find_day_plan(day_plans, move_to_day)
        if not target_plan:
            return jsonify({"success": False, "message": f"Target day {move_to_day} not found"}), 400
        if int(target_plan.get("day", 0)) == int(source_plan.get("day", -1)):
            return jsonify({"success": False, "message": "Source day and target day are the same"}), 400

        moved = _pop_poi_from_plan(source_plan, poi_name)
        if not moved:
            return jsonify({"success": False, "message": f"POI not found in source day: {poi_name}"}), 404
        ok, reason = _append_poi_to_plan_with_constraints(target_plan, moved, trip.preference)
        if not ok:
            # 回滚：放回 source
            source_pois = list(source_plan.get("pois") or [])
            source_pois.append(moved)
            source_plan["pois"] = source_pois
            _recompute_plan_meta(source_plan)
            return jsonify({"success": False, "message": f"Failed to move POI: {reason}"}), 400

        removed = moved
        applied_day = source_plan.get("day")
        added = [moved]
    else:
        # remove/replace 的删除动作
        removed = _remove_poi_from_plan(source_plan, poi_name)
        applied_day = source_plan.get("day")

    if not removed:
        return jsonify({"success": False, "message": f"POI not found in target day: {poi_name}"}), 404

    # remove/replace 的后处理：
    # - remove_poi: 补到最低目标（balanced>=3）
    # - replace_poi: 尽量补回 1 个（可带 same_type/nearby 约束）
    if intent in ("remove_poi", "replace_poi"):
        banned_uids = {_poi_uid(removed)} if isinstance(removed, dict) else set()
        pool = list(content.get("pois") or [])
        if not pool:
            pref = trip.preference
            generated = _compute_candidates_and_itinerary(pref, limit=300) if pref else None
            pool = list((generated or {}).get("out") or [])
        if intent == "replace_poi":
            rep = _replace_one_poi_in_plan(
                plan=source_plan,
                day_plans=day_plans,
                pool=pool,
                pref=trip.preference,
                removed_poi=removed,
                constraints=parsed.get("constraints") or {},
            )
            if rep:
                added = [rep]
        else:
            added = _refill_day_plan_from_pool(
                plan=source_plan,
                day_plans=day_plans,
                pool=pool,
                pref=trip.preference,
                banned_uids=banned_uids,
            )

    # 更新 summary（可读性）
    trip_days = _get_trip_days(trip.preference) if trip.preference else len(day_plans)
    content["day_plans"] = day_plans
    content["summary"] = format_itinerary_summary(day_plans, trip_days)

    new_row = _save_new_itinerary_version(trip_id, content)

    return jsonify({
        "success": True,
        "applied": True,
        "trip_id": trip_id,
        "new_version": new_row.version,
        "applied_action": {
            "intent": intent,
            "day": applied_day,
            "removed_poi": removed.get("name") if isinstance(removed, dict) else poi_name,
            "target_day": move_to_day if intent == "move_poi" else None,
            "added_pois": [x.get("name") for x in added if isinstance(x, dict)],
        },
        "day_plans": day_plans,
        "summary": content.get("summary"),
        "parsed": parsed,
    }), 200

@app.route("/api/ai-chat", methods=["POST"])
def ai_chat():
    """AI 对话：接收用户消息，调用 OpenAI GPT-4.1 mini，返回助手回复"""
    data = request.get_json() or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []  # [{ "role": "user"|"assistant", "content": "..." }]

    if not message:
        return jsonify({"success": False, "message": "Message is required"}), 400

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return jsonify({"success": False, "message": "AI chat is not configured (missing OPENAI_API_KEY)"}), 503

    if OpenAI is None:
        return jsonify({"success": False, "message": "OpenAI client not installed"}), 503

    system_prompt = """You are a travel planning assistant for Dublin.

Your role is:
1. Guide the user through creating a personalized Dublin trip.
2. Explain clearly that a short questionnaire is required to generate recommendations.
3. Do NOT ask all questionnaire questions directly.
4. When the user clicks the questionnaire and submits answers, you will receive a structured summary and convert it into JSON.

Be concise, friendly, and professional."""

    try:
        client = OpenAI(api_key=api_key)
        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-20:]:  # 最多保留最近 20 轮
            role = h.get("role")
            content = (h.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            max_tokens=1024,
        )
        reply = (response.choices[0].message.content or "").strip()
        return jsonify({"success": True, "reply": reply}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/favorites/sync", methods=["POST"])
def sync_favorites():
    """批量同步收藏（从 localStorage 到数据库）"""
    # 检查用户是否登录
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    data = request.get_json()
    favorites = data.get('favorites', [])  # localStorage 中的收藏数组
    
    if not favorites or not isinstance(favorites, list):
        return jsonify({'success': True, 'message': 'No favorites to sync', 'synced': 0}), 200
    
    user_id = session['user_id']
    synced_count = 0
    skipped_count = 0
    
    try:
        for fav in favorites:
            place_id = fav.get('place_id', '').strip()
            place_name = fav.get('place_name', '').strip()
            place_data = fav.get('place_data')
            
            if not place_id or not place_name:
                continue
            
            # 检查是否已存在
            existing = Favorite.query.filter_by(user_id=user_id, place_id=place_id).first()
            if existing:
                skipped_count += 1
                continue
            
            # 创建新收藏
            favorite = Favorite(
                user_id=user_id,
                place_id=place_id,
                place_name=place_name,
                place_data=place_data,
                created_at=datetime.now()
            )
            db.session.add(favorite)
            synced_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Synced {synced_count} favorites',
            'synced': synced_count,
            'skipped': skipped_count
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Failed to sync favorites: ' + str(e)}), 500

if __name__ == "__main__":
    url = "http://127.0.0.1:5000/map"
    print(f"Server running at {url}")
    # 只在主进程中打开浏览器，避免 debug 模式下重载器导致打开两个窗口
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        webbrowser.open(url)
    app.run(debug=True)
