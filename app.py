from flask import Flask, jsonify, redirect, render_template, send_from_directory, request, session
from dotenv import load_dotenv
from mysql import get_database_config, db, User
import os
import webbrowser

load_dotenv()

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

if __name__ == "__main__":
    url = "http://127.0.0.1:5000/map"
    print(f"Server running at {url}")
    webbrowser.open(url)
    app.run(debug=True)
