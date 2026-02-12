from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from dotenv import load_dotenv

load_dotenv()

# 初始化 SQLAlchemy
db = SQLAlchemy()

poi_filter_association = db.Table(
    'poi_filter',
    db.Column('poi_id', db.Integer, db.ForeignKey('poi.poi_id'), primary_key=True),
    db.Column('filter_id', db.Integer, db.ForeignKey('filters.filter_id'), primary_key=True),
    db.UniqueConstraint('poi_id', 'filter_id', name='uq_poi_filter')
)

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


class POI(db.Model):
    __tablename__ = 'poi'

    poi_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    source_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    source = db.Column(db.String(32), nullable=False, default="csv")
    address = db.Column(db.Text, nullable=True)
    telephone = db.Column(db.String(64), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    opening_hours = db.Column(db.JSON, nullable=True)
    price_level = db.Column(db.Integer, nullable=True)
    rating = db.Column(db.Float, nullable=True)
    tags = db.Column(db.Text, nullable=True)
    url = db.Column(db.String(512), nullable=True)
    photos = db.Column(db.Text, nullable=True)
    suitable_for_children = db.Column(db.Boolean, nullable=True, default=None)  # Step0 缓存：None=未计算，True/False=已计算
    suitable_for_seniors = db.Column(db.Boolean, nullable=True, default=None)
    filters = db.relationship('Filter', secondary=poi_filter_association, back_populates='pois', lazy='joined')

    def update_from_dict(self, data: dict):
        for field in [
            'name', 'address', 'telephone', 'latitude', 'longitude', 'opening_hours',
            'price_level', 'rating', 'tags', 'url', 'photos', 'source'
        ]:
            if field in data:
                setattr(self, field, data[field])


class Filter(db.Model):
    __tablename__ = 'filters'

    filter_id = db.Column(db.Integer, primary_key=True)
    filter_name = db.Column(db.String(255), unique=True, nullable=False, index=True)

    pois = db.relationship('POI', secondary=poi_filter_association, back_populates='filters', lazy='dynamic')


class MustVisitCache(db.Model):
    """must-visit 解析缓存：同一 user_input 直接返回已解析的 poi_id / resolved_name，避免重复调 API"""
    __tablename__ = 'must_visit_cache'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    poi_id = db.Column(db.Integer, db.ForeignKey('poi.poi_id', ondelete='CASCADE'), nullable=True)  # 未匹配时为 NULL
    user_input = db.Column(db.String(255), nullable=False, index=True)  # 用户输入的文本，如 "UCD"
    resolved_name = db.Column(db.String(255), nullable=True)  # 匹配到的 POI 名称或 "(unresolved)"
    google_place_id = db.Column(db.String(128), nullable=True)  # 若用过 Google API 则缓存
    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now(), nullable=False)


class Favorite(db.Model):
    """用户收藏表"""
    __tablename__ = 'favorites'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    place_id = db.Column(db.String(255), nullable=False, index=True)  # Google Places API 的 place_id
    place_name = db.Column(db.String(255), nullable=False)
    place_data = db.Column(db.JSON, nullable=True)  # 存储完整的 Google Places 数据
    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    
    # 关系
    user = db.relationship('User', backref='favorites')
    
    # 唯一约束：同一用户不能重复收藏同一地点
    __table_args__ = (
        db.UniqueConstraint('user_id', 'place_id', name='uq_user_place'),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'place_id': self.place_id,
            'place_name': self.place_name,
            'place_data': self.place_data,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Trip(db.Model):
    """行程表：每次用户开始规划即创建一个 Trip"""
    __tablename__ = 'trips'

    trip_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default='active')  # active / archived
    is_saved = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    saved_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref=db.backref('trips', lazy='dynamic'))
    preference = db.relationship('TripPreference', back_populates='trip', uselist=False, cascade='all, delete-orphan')
    itineraries = db.relationship('Itinerary', back_populates='trip', lazy='dynamic', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'trip_id': self.trip_id,
            'user_id': self.user_id,
            'status': self.status,
            'is_saved': self.is_saved,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'saved_at': self.saved_at.isoformat() if self.saved_at else None,
        }


class TripPreference(db.Model):
    """用户偏好表：问卷 + AI 对话修改后的偏好，每个 Trip 至少有一条最新记录"""
    __tablename__ = 'trip_preferences'

    trip_id = db.Column(db.Integer, db.ForeignKey('trips.trip_id', ondelete='CASCADE'), primary_key=True)
    group_type = db.Column(db.String(32), nullable=True)  # Single / Couple / Family / Friends Group / Other
    num_people = db.Column(db.Integer, nullable=True)
    num_children = db.Column(db.Integer, nullable=True)  # <12 years
    num_seniors = db.Column(db.Integer, nullable=True)   # 65+ years
    trip_duration = db.Column(db.String(64), nullable=True)  # e.g. "1 day", "3 days", "1 week"
    budget = db.Column(db.String(64), nullable=True)  # Low(0-50) / Medium(50-100) / High(100-150) 或自定义 €
    hotel_budget = db.Column(db.String(64), nullable=True)  # 50-100 / 100-200 / 200-400 或自定义 €（可选）
    hotel_preferred_area = db.Column(db.String(64), nullable=True)  # city_centre / near_main_attractions / near_public_transport / quiet_residential / no_preference
    visit_date = db.Column(db.Date, nullable=True)
    visit_date_end = db.Column(db.Date, nullable=True)
    interests = db.Column(db.JSON, nullable=True)
    interests_other = db.Column(db.Text, nullable=True)  # 用户自定义兴趣，不进打分函数
    specific_places = db.Column(db.Text, nullable=True)
    pace = db.Column(db.String(32), nullable=True)
    start_time = db.Column(db.String(32), nullable=True)
    food_preference = db.Column(db.String(255), nullable=True)
    dietary_needs = db.Column(db.String(255), nullable=True)
    avoid = db.Column(db.JSON, nullable=True)
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now(), nullable=False)

    trip = db.relationship('Trip', back_populates='preference')

    def to_dict(self):
        return {
            'trip_id': self.trip_id,
            'group_type': self.group_type,
            'num_people': self.num_people,
            'num_children': self.num_children,
            'num_seniors': self.num_seniors,
            'trip_duration': self.trip_duration,
            'budget': self.budget,
            'hotel_budget': self.hotel_budget,
            'hotel_preferred_area': self.hotel_preferred_area,
            'visit_date': self.visit_date.isoformat() if self.visit_date else None,
            'visit_date_end': self.visit_date_end.isoformat() if self.visit_date_end else None,
            'interests': self.interests,
            'interests_other': self.interests_other,
            'specific_places': self.specific_places,
            'pace': self.pace,
            'start_time': self.start_time,
            'food_preference': self.food_preference,
            'dietary_needs': self.dietary_needs,
            'avoid': self.avoid,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Itinerary(db.Model):
    """AI 输出路线表：根据 TripPreference 生成的行程，支持多版本"""
    __tablename__ = 'itineraries'

    itinerary_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    trip_id = db.Column(db.Integer, db.ForeignKey('trips.trip_id', ondelete='CASCADE'), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    content_json = db.Column(db.JSON, nullable=True)  
    generated_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True) 

    trip = db.relationship('Trip', back_populates='itineraries')

    __table_args__ = (
        db.UniqueConstraint('trip_id', 'version', name='uq_trip_version'),
    )

    def to_dict(self):
        return {
            'itinerary_id': self.itinerary_id,
            'trip_id': self.trip_id,
            'version': self.version,
            'content_json': self.content_json,
            'generated_at': self.generated_at.isoformat() if self.generated_at else None,
            'is_active': self.is_active,
        }