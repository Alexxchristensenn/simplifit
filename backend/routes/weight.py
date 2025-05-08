from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.user import User
from models.weight import WeightEntry
from app import db
from datetime import datetime

weight_bp = Blueprint('weight', __name__)

@weight_bp.route('/entries', methods=['POST'])
@jwt_required()
def add_weight_entry():
    user_id = int(get_jwt_identity())
    data = request.get_json()
    
    # Create new weight entry
    entry = WeightEntry(
        user_id=user_id,
        weight=data['weight'],
        date=datetime.strptime(data.get('date', datetime.now().strftime('%Y-%m-%d')), '%Y-%m-%d'),
        notes=data.get('notes', '')
    )
    
    db.session.add(entry)
    db.session.commit()
    
    return jsonify(entry.to_dict()), 201

@weight_bp.route('/entries', methods=['GET'])
@jwt_required()
def get_weight_entries():
    user_id = int(get_jwt_identity())
    
    # Get date range from query parameters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = WeightEntry.query.filter_by(user_id=user_id)
    
    if start_date:
        query = query.filter(WeightEntry.date >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        query = query.filter(WeightEntry.date <= datetime.strptime(end_date, '%Y-%m-%d'))
    
    entries = query.order_by(WeightEntry.date.desc()).all()
    return jsonify([entry.to_dict() for entry in entries])

@weight_bp.route('/entries/<int:entry_id>', methods=['DELETE'])
@jwt_required()
def delete_weight_entry(entry_id):
    user_id = int(get_jwt_identity())
    entry = WeightEntry.query.filter_by(id=entry_id, user_id=user_id).first_or_404()
    
    db.session.delete(entry)
    db.session.commit()
    
    return jsonify({'message': 'Weight entry deleted successfully'})

@weight_bp.route('/stats', methods=['GET'])
@jwt_required()
def get_weight_stats():
    user_id = int(get_jwt_identity())
    
    # Get all weight entries for the user
    entries = WeightEntry.query.filter_by(user_id=user_id).order_by(WeightEntry.date.asc()).all()
    
    if not entries:
        return jsonify({
            'message': 'No weight entries found',
            'stats': {
                'current_weight': None,
                'starting_weight': None,
                'weight_change': None,
                'average_weight': None
            }
        })
    
    # Calculate statistics
    current_weight = entries[-1].weight
    starting_weight = entries[0].weight
    weight_change = current_weight - starting_weight
    average_weight = sum(entry.weight for entry in entries) / len(entries)
    
    return jsonify({
        'stats': {
            'current_weight': current_weight,
            'starting_weight': starting_weight,
            'weight_change': weight_change,
            'average_weight': average_weight,
            'total_entries': len(entries)
        }
    }) 