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
from changePOI import init_change_poi, register_change_poi_routes

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


@app.route("/api/trips", methods=["GET"])
def get_user_trips():
    """返回当前登录用户的 Trip 历史列表（包含最新 itinerary 摘要）。"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Please login first", "trips": []}), 401

    try:
        trips = (
            Trip.query
            .filter_by(user_id=user_id)
            .order_by(Trip.created_at.desc())
            .all()
        )

        out = []
        for trip in trips:
            active_itinerary = (
                Itinerary.query
                .filter_by(trip_id=trip.trip_id, is_active=True)
                .order_by(Itinerary.version.desc())
                .first()
            )
            latest_itinerary = active_itinerary or (
                Itinerary.query
                .filter_by(trip_id=trip.trip_id)
                .order_by(Itinerary.version.desc())
                .first()
            )

            latest_payload = None
            if latest_itinerary:
                content = latest_itinerary.content_json or {}
                day_plans = content.get("day_plans") if isinstance(content, dict) else []
                if not isinstance(day_plans, list):
                    day_plans = []
                summary = (content.get("summary") if isinstance(content, dict) else None) or None
                if not summary and day_plans:
                    trip_days = _get_trip_days(trip.preference) if trip.preference else len(day_plans)
                    summary = format_itinerary_summary(day_plans, trip_days)
                poi_count = sum(len((dp or {}).get("pois") or []) for dp in day_plans)
                latest_payload = {
                    "itinerary_id": latest_itinerary.itinerary_id,
                    "version": latest_itinerary.version,
                    "is_active": bool(latest_itinerary.is_active),
                    "generated_at": latest_itinerary.generated_at.isoformat() if latest_itinerary.generated_at else None,
                    "day_count": len(day_plans),
                    "poi_count": poi_count,
                    "summary": summary,
                }

            out.append({
                "trip_id": trip.trip_id,
                "status": trip.status,
                "is_saved": bool(trip.is_saved),
                "created_at": trip.created_at.isoformat() if trip.created_at else None,
                "saved_at": trip.saved_at.isoformat() if trip.saved_at else None,
                "visit_date": trip.preference.visit_date.isoformat() if (trip.preference and trip.preference.visit_date) else None,
                "visit_date_end": trip.preference.visit_date_end.isoformat() if (trip.preference and trip.preference.visit_date_end) else None,
                "interests": (trip.preference.interests if (trip.preference and isinstance(trip.preference.interests, dict)) else {}),
                "latest_itinerary": latest_payload,
            })

        return jsonify({"success": True, "trips": out}), 200
    except Exception as e:
        return jsonify({"success": False, "message": "Failed to load trips: " + str(e), "trips": []}), 500


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

init_change_poi(
    db=db,
    Trip=Trip,
    Itinerary=Itinerary,
    POI=POI,
    OpenAI=OpenAI,
    optimize_route_greedy_tsp=optimize_route_greedy_tsp,
    get_poi_duration=get_poi_duration,
    format_itinerary_summary=format_itinerary_summary,
    PACE_TO_DAILY_POI_CAP=PACE_TO_DAILY_POI_CAP,
    INTEREST_TO_FILTER_IDS=INTEREST_TO_FILTER_IDS,
    DUBLIN_CENTER=DUBLIN_CENTER,
    haversine_km=_haversine_km,
    is_same_location=_is_same_location,
    compute_candidates_and_itinerary=_compute_candidates_and_itinerary,
    persist_itinerary_if_missing=_persist_itinerary_if_missing,
    get_trip_days=_get_trip_days,
)
register_change_poi_routes(app)

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
