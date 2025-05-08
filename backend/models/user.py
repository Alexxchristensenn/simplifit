from app import db
from datetime import datetime
import bcrypt

class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    height = db.Column(db.Float)  # in cm
    weight = db.Column(db.Float)  # in kg
    age = db.Column(db.Integer)
    gender = db.Column(db.String(20))
    activity_level = db.Column(db.String(20))
    goal = db.Column(db.String(20))  # e.g., 'lose_weight', 'gain_muscle', 'maintain'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, email, password, **kwargs):
        self.email = email
        self.set_password(password)
        for key, value in kwargs.items():
            setattr(self, key, value)

    def set_password(self, password):
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'height': self.height,
            'weight': self.weight,
            'age': self.age,
            'gender': self.gender,
            'activity_level': self.activity_level,
            'goal': self.goal,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        } 