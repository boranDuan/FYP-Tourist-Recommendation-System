from flask import Flask, jsonify, redirect, render_template, send_from_directory, request, session
from dotenv import load_dotenv
from mysql import get_database_config, db, User, Favorite, Trip, TripPreference
from datetime import datetime
import os
import webbrowser

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
        user = User.query.get(session['user_id'])
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

# ========== 问卷校验 API ==========
# Q4、Q7、Q12 为可选，1b 为可选，其余必填

def validate_questionnaire_data(data):
    """校验问卷必填项，返回 (is_valid, errors_list)。Q4、Q7、Q12 可选，1b 可选。"""
    errors = []
    group_type = (data.get("group_type") or "").strip()
    num_people = data.get("num_people")
    trip_duration_value = (data.get("trip_duration_value") or "").strip()
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
        errors.append("Q1: Please select how many people are traveling.")
    if group_type in ("family", "friends_group", "other"):
        np = num_people if isinstance(num_people, (int, float)) else (num_people and str(num_people).strip())
        if not np or (isinstance(np, str) and not np) or (isinstance(np, (int, float)) and int(np) < 1):
            errors.append("Q1: Please enter the number of people.")
    if not trip_duration_value or (trip_duration_value.isdigit() and int(trip_duration_value) < 1):
        errors.append("Q2: Please enter trip duration.")
    if budget_unit == "custom":
        if not (budget_value and str(budget_value).strip()):
            errors.append("Q3: Please enter your custom budget amount.")
    elif not budget_unit:
        errors.append("Q3: Please select your travel budget.")
    if not visit_date:
        errors.append("Q5: Please select your visit date.")
    # interests: weight object {museum, culture, ...}, at least one > 0
    if not isinstance(interests, dict):
        errors.append("Q6: Please select at least one type of place or specify in Other.")
    else:
        has_interest = any(v and float(v) > 0 for v in interests.values())
        if not has_interest:
            errors.append("Q6: Please select at least one type of place or specify in Other.")
    if not pace:
        errors.append("Q9: Please select your travel pace.")
    if not start_time_unit:
        errors.append("Q10: Please select your preferred start time.")
    if start_time_unit == "custom" and not (start_time_value and str(start_time_value).strip()):
        errors.append("Q10: Please enter your custom start time.")
    if not food_preference or len(food_preference) == 0:
        errors.append("Q11: Please select at least one food type or specify in Other.")

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

    val = str(data.get("trip_duration_value") or "").strip()
    unit = (data.get("trip_duration_unit") or "day").strip()
    trip_duration = f"{val} {unit}(s)" if val else None

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
            trip_duration=trip_duration,
            budget=budget if budget_unit else None,
            hotel_budget=(data.get("hotel_budget_unit") or "").strip() or None,
            hotel_preferred_area=(data.get("hotel_preferred_area") or "").strip() or None,
            visit_date=visit_date,
            visit_date_end=visit_date_end,
            interests=interests_data,
            specific_places=(data.get("specific_places") or "").strip() or None,
            pace=(data.get("pace") or "").strip() or None,
            start_time=start_time,
            food_preference=food_str or None,
            dietary_needs=dietary_str or None,
            avoid=data.get("avoid") or [],
        )
        db.session.add(pref)
        db.session.commit()
        return jsonify({"success": True, "trip_id": trip.trip_id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


# ========== AI 对话 API（OpenAI GPT-4.1 mini）==========

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
