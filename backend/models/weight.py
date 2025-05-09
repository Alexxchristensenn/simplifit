from backend.app import db
from datetime import datetime

class WeightEntry(db.Model):
    __tablename__ = 'weight_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    notes = db.Column(db.Text)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('weight_entries', lazy=True))
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'weight': self.weight,
            'date': self.date.strftime('%Y-%m-%d'),
            'notes': self.notes
        } 