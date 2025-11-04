from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from dotenv import load_dotenv

load_dotenv()

# 初始化 SQLAlchemy
db = SQLAlchemy()

# 数据库配置：使用 MySQL
def get_database_config(app):
    """配置数据库连接"""
    database_name = os.getenv('DATABASE_NAME', 'tourist_recommend')
    database_uri = os.getenv(
        'DATABASE_URL', 
        f'mysql+pymysql://root:123456@localhost/{database_name}?charset=utf8mb4'
    )
    
    app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # 初始化数据库
    db.init_app(app)
    
    # 创建数据库表
    with app.app_context():
        db.create_all()
    
    return db


class User(db.Model):
    """用户表"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    
    def set_password(self, password):
        """设置密码（加密）"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """验证密码"""
        return check_password_hash(self.password_hash, password)
    
    def to_dict(self):
        """转换为字典（用于 JSON 响应）"""
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email
        }

