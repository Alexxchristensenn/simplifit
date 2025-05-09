from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from backend.models.user import User
from backend.models.weight import WeightEntry
from backend.app import db
from datetime import datetime, timedelta
from sqlalchemy import func

macros_bp = Blueprint('macros', __name__)

# Conversion constants
LB_TO_KG = 0.453592
INCH_TO_CM = 2.54
KG_TO_LB = 2.20462

# Reasonable limits for measurements
# Note: These ranges are based on:
# 1. Safety: Ensuring calculations are medically appropriate
# 2. Accuracy: BMR/TDEE formulas are most accurate within these ranges
# 3. Practicality: Most users will fall within these ranges
# 4. Inclusivity: Accommodating most body types while maintaining accuracy

# Weight ranges (in pounds)
MIN_WEIGHT_LB = 40    # Very low weight, might indicate need for medical supervision
MAX_WEIGHT_LB = 700   # Very high weight, might need medical supervision

# Height ranges (in inches)
MIN_HEIGHT_INCHES = 36  # Accommodates little people (dwarfism)
MAX_HEIGHT_INCHES = 108 # Accommodates very tall individuals (9 feet)

# Age ranges
MIN_AGE = 16  # Minimum age for safe macro tracking. Younger individuals are still growing
              # and should focus on healthy eating habits rather than specific macro targets
MAX_AGE = 100 # Elderly individuals

# Minimum safe calorie thresholds
MIN_CALORIES_MALE = 1500
MIN_CALORIES_FEMALE = 1200

# Weight change targets (in kg per week)
# Based on scientific recommendations for optimal muscle retention during cuts
# and minimal fat gain during bulks
WEIGHT_LOSS_TARGETS = {
    'moderate': (0.45, 0.68),  # Moderate cut: 0.45-0.68 kg/week (1-1.5 lbs/week)
    'aggressive': (0.68, 0.91)  # Aggressive cut: 0.68-0.91 kg/week (1.5-2 lbs/week)
}

WEIGHT_GAIN_TARGETS = {
    'moderate': (0.11, 0.23),  # Moderate bulk: 0.11-0.23 kg/week (0.25-0.5 lbs/week)
    'aggressive': (0.23, 0.34)  # Aggressive bulk: 0.23-0.34 kg/week (0.5-0.75 lbs/week)
}

# Safety caps for calorie adjustments
# Based on research showing that larger deficits can lead to metabolic adaptation
# and muscle loss, while larger surpluses lead to excessive fat gain
MAX_DEFICIT_ADJUSTMENT = 0.15  # Maximum 15% additional deficit
MAX_SURPLUS_ADJUSTMENT = 0.10  # Maximum 10% additional surplus
MIN_DEFICIT = 0.10  # Never go below 10% deficit
MAX_DEFICIT = 0.30  # Never exceed 30% deficit
MIN_SURPLUS = 0.03  # Never go below 3% surplus
MAX_SURPLUS = 0.15  # Never exceed 15% surplus

# Calorie adjustment factors
CALORIE_ADJUSTMENT_FACTOR = 7700  # 7700 calories per kg of weight change

# Additional constants for diet phases and validation
MIN_WEIGHT_ENTRIES_PER_WEEK = 3  # Minimum entries needed for accurate tracking
MAX_WEIGHT_VARIANCE = 2.0  # Maximum allowed variance between consecutive entries (kg)
# This allows for ~4.4 lbs variance to accommodate:
# - Water weight fluctuations (2-5 lbs)
# - Post-carb refeed weight gain
# - Post-workout water retention
# - Digestive system contents
# - Menstrual cycle fluctuations
MAINTENANCE_PHASE_DURATION = 14  # Days to maintain weight before considering goal achieved
DIET_BREAK_DURATION = 14  # Days for diet break
DIET_BREAK_FREQUENCY = 90  # Days between diet breaks
MAX_DEFICIT_DURATION = 90  # Days before requiring diet break
METABOLIC_ADAPTATION_THRESHOLD = 0.15  # 15% reduction in weight loss rate indicates adaptation

# Add new constants at the top with other constants
MIN_PROTEIN_G_PER_KG = 1.7  # Minimum protein requirement for all users
RECOMP_PHASE = 'recomp'  # Add recomp to diet phases

class DietPhase:
    """Enum-like class for tracking diet phases"""
    CUT = 'cut'
    MAINTENANCE = 'maintenance'
    DIET_BREAK = 'diet_break'
    BULK = 'bulk'
    DELOAD = 'deload'
    RECOMP = 'recomp'

# Add validation for activity levels
VALID_ACTIVITY_LEVELS = {
    'sedentary': 'Little or no exercise',
    'light': 'Light exercise 1-3 days/week',
    'moderate': 'Moderate exercise 3-5 days/week',
    'active': 'Hard exercise 6-7 days/week',
    'very_active': 'Very hard exercise & physical job or training twice per day'
}

class ValidationError(Exception):
    """Custom exception for validation errors"""
    pass

def validate_measurements(weight_lb, height_inches, age):
    """
    Validate that measurements are within reasonable ranges.
    Note: These ranges are intentionally wide to be inclusive, but users
    outside these ranges should consult with healthcare professionals.
    """
    if not isinstance(weight_lb, (int, float)) or not isinstance(height_inches, (int, float)) or not isinstance(age, (int, float)):
        raise ValidationError("Weight, height, and age must be numbers")
    
    if weight_lb < MIN_WEIGHT_LB:
        raise ValidationError(
            f"Weight must be at least {MIN_WEIGHT_LB} pounds. "
            "If you are below this weight, please consult with a healthcare professional."
        )
    if weight_lb > MAX_WEIGHT_LB:
        raise ValidationError(
            f"Weight must be at most {MAX_WEIGHT_LB} pounds. "
            "If you are above this weight, please consult with a healthcare professional."
        )
    
    if height_inches < MIN_HEIGHT_INCHES:
        raise ValidationError(
            f"Height must be at least {MIN_HEIGHT_INCHES} inches. "
            "If you are below this height, please consult with a healthcare professional."
        )
    if height_inches > MAX_HEIGHT_INCHES:
        raise ValidationError(
            f"Height must be at most {MAX_HEIGHT_INCHES} inches. "
            "If you are above this height, please consult with a healthcare professional."
        )
    
    if age < MIN_AGE:
        raise ValidationError(
            f"Age must be at least {MIN_AGE} years. "
            "This app is designed for individuals who have completed their primary growth phase. "
            "Younger individuals should focus on healthy eating habits and consult with healthcare professionals."
        )
    if age > MAX_AGE:
        raise ValidationError(
            f"Age must be at most {MAX_AGE} years. "
            "If you are above this age, please consult with a healthcare professional."
        )

def convert_to_metric(weight_lb, height_inches):
    """Convert imperial measurements to metric for calculations"""
    try:
        validate_measurements(weight_lb, height_inches, 0)  # Age validation not needed for conversion
        weight_kg = weight_lb * LB_TO_KG
        height_cm = height_inches * INCH_TO_CM
        return weight_kg, height_cm
    except ValidationError as e:
        raise ValidationError(f"Invalid measurements for conversion: {str(e)}")
    except Exception as e:
        raise ValidationError(f"Error during unit conversion: {str(e)}")

def convert_to_imperial(weight_kg, height_cm):
    """Convert metric measurements back to imperial for display"""
    try:
        # Convert to imperial first to validate against imperial limits
        weight_lb = weight_kg * KG_TO_LB
        height_inches = height_cm / INCH_TO_CM
        validate_measurements(weight_lb, height_inches, 0)  # Age validation not needed for conversion
        return weight_lb, height_inches
    except ValidationError as e:
        raise ValidationError(f"Invalid measurements for conversion: {str(e)}")
    except Exception as e:
        raise ValidationError(f"Error during unit conversion: {str(e)}")

def calculate_theoretical_tdee(user):
    """Calculate theoretical TDEE using Mifflin-St Jeor equation"""
    try:
        # Validate user measurements
        validate_measurements(user.weight, user.height, user.age)
        
        # Convert weight and height to metric units
        weight_kg, height_cm = convert_to_metric(user.weight, user.height)
        
        # BMR calculation using metric units
        if user.gender == 'male':
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * user.age + 5
        else:
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * user.age - 161
        
        # Validate BMR result
        if bmr <= 0:
            raise ValidationError("Calculated BMR is invalid (must be positive)")
        
        # Activity multipliers (Mifflin-St Jeor)
        activity_multipliers = {
            'sedentary': 1.2,      # Little or no exercise
            'light': 1.375,        # Light exercise 1-3 days/week
            'moderate': 1.55,      # Moderate exercise 3-5 days/week
            'active': 1.725,       # Hard exercise 6-7 days/week
            'very_active': 1.9     # Very hard exercise & physical job or training twice per day
        }
        
        if user.activity_level not in activity_multipliers:
            raise ValidationError(f"Invalid activity level: {user.activity_level}")
        
        tdee = bmr * activity_multipliers[user.activity_level]
        
        # Validate TDEE result
        if tdee <= 0:
            raise ValidationError("Calculated TDEE is invalid (must be positive)")
        
        return tdee
    except ValidationError as e:
        raise ValidationError(f"Error calculating TDEE: {str(e)}")
    except Exception as e:
        raise ValidationError(f"Unexpected error in TDEE calculation: {str(e)}")

def calculate_actual_tdee(user, weight_entries):
    """
    Calculate actual TDEE based on weight changes over time.
    Continuously adjusts the theoretical TDEE based on actual weight changes
    to account for individual metabolic differences.
    """
    try:
        if len(weight_entries) < 2:
            return None
            
        # Get the first and last weight entries
        first_entry = weight_entries[0]
        last_entry = weight_entries[-1]
        
        # Validate weight entries
        validate_measurements(first_entry.weight, user.height, 0)
        validate_measurements(last_entry.weight, user.height, 0)
        
        # Calculate time difference in weeks
        weeks = (last_entry.date - first_entry.date).days / 7
        
        if weeks < 2:  # Need at least 2 weeks of data
            return None
            
        # Convert weights to kg for calculation
        first_weight_kg = first_entry.weight * LB_TO_KG
        last_weight_kg = last_entry.weight * LB_TO_KG
        
        # Calculate weight change per week in kg
        weight_change_kg = last_weight_kg - first_weight_kg
        weekly_change_kg = weight_change_kg / weeks
        
        # Calculate theoretical TDEE
        theoretical_tdee = calculate_theoretical_tdee(user)
        
        # Calculate actual TDEE based on weight change and current calorie intake
        # If user is losing weight, their actual TDEE is higher than theoretical
        # If user is gaining weight, their actual TDEE is lower than theoretical
        calorie_adjustment = weekly_change_kg * CALORIE_ADJUSTMENT_FACTOR / 7  # Daily adjustment
        
        # Get user's current calorie intake
        current_calories = theoretical_tdee
        if user.goal == 'lose':
            current_calories *= (1 - 0.15)  # Assuming 15% deficit
        elif user.goal == 'gain':
            current_calories *= (1 + 0.05)  # Assuming 5% surplus
        
        # Calculate actual TDEE
        actual_tdee = current_calories - calorie_adjustment
        
        # Validate TDEE result
        if actual_tdee <= 0:
            raise ValidationError("Calculated actual TDEE is invalid (must be positive)")
        
        # Cap the difference between theoretical and actual TDEE
        # This prevents extreme adjustments while still allowing for individual differences
        max_tdee_difference = theoretical_tdee * 0.3  # Allow up to 30% difference
        if abs(actual_tdee - theoretical_tdee) > max_tdee_difference:
            if actual_tdee > theoretical_tdee:
                actual_tdee = theoretical_tdee + max_tdee_difference
            else:
                actual_tdee = theoretical_tdee - max_tdee_difference
        
        return actual_tdee
    except ValidationError as e:
        raise ValidationError(f"Error calculating actual TDEE: {str(e)}")
    except Exception as e:
        raise ValidationError(f"Unexpected error in actual TDEE calculation: {str(e)}")

def validate_calories(calories, bmr, gender):
    """
    Validate that calculated calories are within safe ranges.
    Ensures calories are not below minimum safe thresholds or BMR.
    """
    min_calories = MIN_CALORIES_MALE if gender == 'male' else MIN_CALORIES_FEMALE
    min_safe_calories = max(min_calories, bmr)
    
    if calories < min_safe_calories:
        raise ValidationError(
            f"Calculated calories ({round(calories)}) are below the safe minimum of {round(min_safe_calories)}. "
            "This deficit is too aggressive and could be harmful to your health. "
            "Please consider a more moderate deficit or consult with a healthcare professional."
        )

def calculate_weekly_weight_changes(weight_entries):
    """
    Calculate weight changes using a flexible time window approach.
    Focuses on the most recent 2-3 weeks of data to determine current rate of change.
    Returns a list of weekly changes in kg and the current rate of change.
    """
    if len(weight_entries) < 2:
        return [], None
    
    # Sort entries by date
    sorted_entries = sorted(weight_entries, key=lambda x: x.date)
    
    # Calculate daily changes
    daily_changes = []
    for i in range(1, len(sorted_entries)):
        prev_entry = sorted_entries[i-1]
        curr_entry = sorted_entries[i]
        
        # Convert weights to kg
        prev_weight_kg = prev_entry.weight * LB_TO_KG
        curr_weight_kg = curr_entry.weight * LB_TO_KG
        
        # Calculate days between entries
        days = (curr_entry.date - prev_entry.date).days
        if days == 0:  # Skip if same day
            continue
            
        # Calculate daily change
        daily_change = (curr_weight_kg - prev_weight_kg) / days
        daily_changes.append((curr_entry.date, daily_change))
    
    if not daily_changes:
        return [], None
    
    # Group changes into weeks, but be flexible about the window
    weekly_changes = []
    current_week = []
    current_week_start = daily_changes[0][0]
    
    for date, daily_change in daily_changes:
        # If we've moved to a new week (or close to it), calculate the average for the previous period
        if (date - current_week_start).days >= 6:  # More flexible than strict 7 days
            if current_week:
                # Weight the average by the number of days in the period
                period_days = (current_week[-1][0] - current_week[0][0]).days + 1
                weekly_avg = sum(change for _, change in current_week) / len(current_week)
                # Normalize to a weekly rate
                weekly_avg = weekly_avg * (7 / period_days)
                weekly_changes.append(weekly_avg)
            current_week = []
            current_week_start = date
        
        current_week.append((date, daily_change))
    
    # Add the last period if it has data
    if current_week:
        period_days = (current_week[-1][0] - current_week[0][0]).days + 1
        weekly_avg = sum(change for _, change in current_week) / len(current_week)
        weekly_avg = weekly_avg * (7 / period_days)
        weekly_changes.append(weekly_avg)
    
    if not weekly_changes:
        return [], None
    
    # Calculate current rate of change using a gentle weighting system
    # Focus on the most recent 2-3 weeks
    decay_factor = 0.85
    weights = [decay_factor ** i for i in range(len(weekly_changes))]
    weights.reverse()  # Most recent week gets highest weight
    
    # Normalize weights to sum to 1
    total_weight = sum(weights)
    normalized_weights = [w / total_weight for w in weights]
    
    # Calculate weighted average
    current_rate = sum(change * weight for change, weight in zip(weekly_changes, normalized_weights))
    
    # If we have enough data, calculate the trend
    trend = None
    if len(weekly_changes) >= 2:
        # Calculate if the rate is accelerating or decelerating
        recent_changes = weekly_changes[-2:]  # Last two weeks
        trend = recent_changes[1] - recent_changes[0]
    
    return weekly_changes, current_rate, trend

def calculate_adaptive_deficit(user, weight_entries, intensity='moderate'):
    """
    Calculate an adaptive calorie deficit based on actual weight changes.
    Uses evidence-based adjustments to maintain optimal fat loss while preserving muscle.
    """
    if len(weight_entries) < 2:
        return 0.15  # Default to 15% deficit if not enough data
    
    # Calculate weekly weight changes
    weekly_changes, current_rate, trend = calculate_weekly_weight_changes(weight_entries)
    
    if current_rate is None:
        return 0.15
    
    # Get target range based on intensity
    min_target, max_target = WEIGHT_LOSS_TARGETS.get(intensity, (0.45, 0.68))
    
    # Base deficit is 15% (0.15)
    base_deficit = 0.15
    
    # Calculate how far we are from the target range
    if current_rate > -min_target:  # Losing less than minimum target
        # Calculate distance from target as a percentage
        distance_from_target = abs((-min_target - current_rate) / min_target)
        
        # More conservative adjustment for small deviations
        if distance_from_target < 0.2:  # Within 20% of target
            adjustment = distance_from_target * 0.2  # 0.2% per 10% deviation
        else:  # Larger deviations
            adjustment = distance_from_target * 0.3  # 0.3% per 10% deviation
        
        # Cap the adjustment
        adjustment = min(adjustment, MAX_DEFICIT_ADJUSTMENT)
        new_deficit = base_deficit + adjustment
        
        # Ensure we don't exceed maximum safe deficit
        return min(new_deficit, MAX_DEFICIT)
        
    elif current_rate < -max_target:  # Losing more than maximum target
        # Calculate distance from target as a percentage
        distance_from_target = abs((current_rate + max_target) / max_target)
        
        # More aggressive reduction for larger overshoots
        if distance_from_target < 0.2:  # Within 20% of target
            adjustment = distance_from_target * 0.2  # 0.2% per 10% deviation
        else:  # Larger deviations
            adjustment = distance_from_target * 0.3  # 0.3% per 10% deviation
        
        # Cap the adjustment
        adjustment = min(adjustment, MAX_DEFICIT_ADJUSTMENT)
        new_deficit = base_deficit - adjustment
        
        # Ensure we don't go below minimum safe deficit
        return max(new_deficit, MIN_DEFICIT)
        
    else:
        return base_deficit  # Within target range, keep base deficit

def calculate_adaptive_surplus(user, weight_entries, intensity='moderate'):
    """
    Calculate an adaptive calorie surplus based on actual weight changes.
    Uses evidence-based adjustments to optimize muscle gain while minimizing fat gain.
    """
    if len(weight_entries) < 2:
        return 0.05  # Default to 5% surplus if not enough data
    
    # Calculate weekly weight changes
    weekly_changes, current_rate, trend = calculate_weekly_weight_changes(weight_entries)
    
    if current_rate is None:
        return 0.05
    
    # Get target range based on intensity
    min_target, max_target = WEIGHT_GAIN_TARGETS.get(intensity, (0.11, 0.23))
    
    # Base surplus is 5% (0.05)
    base_surplus = 0.05
    
    # Calculate how far we are from the target range
    if current_rate < min_target:  # Gaining less than minimum target
        # Calculate distance from target as a percentage
        distance_from_target = abs((min_target - current_rate) / min_target)
        
        # More conservative adjustment for small deviations
        if distance_from_target < 0.2:  # Within 20% of target
            adjustment = distance_from_target * 0.1  # 0.1% per 10% deviation
        else:  # Larger deviations
            adjustment = distance_from_target * 0.15  # 0.15% per 10% deviation
        
        # Cap the adjustment
        adjustment = min(adjustment, MAX_SURPLUS_ADJUSTMENT)
        new_surplus = base_surplus + adjustment
        
        # Ensure we don't exceed maximum safe surplus
        return min(new_surplus, MAX_SURPLUS)
        
    elif current_rate > max_target:  # Gaining more than maximum target
        # Calculate distance from target as a percentage
        distance_from_target = abs((current_rate - max_target) / max_target)
        
        # More aggressive reduction for larger overshoots
        if distance_from_target < 0.2:  # Within 20% of target
            adjustment = distance_from_target * 0.1  # 0.1% per 10% deviation
        else:  # Larger deviations
            adjustment = distance_from_target * 0.15  # 0.15% per 10% deviation
        
        # Cap the adjustment
        adjustment = min(adjustment, MAX_SURPLUS_ADJUSTMENT)
        new_surplus = base_surplus - adjustment
        
        # Ensure we don't go below minimum safe surplus
        return max(new_surplus, MIN_SURPLUS)
        
    else:
        return base_surplus  # Within target range, keep base surplus

def validate_activity_level(activity_level):
    """Validate that the activity level is valid and properly formatted"""
    if activity_level not in VALID_ACTIVITY_LEVELS:
        raise ValidationError(f"Invalid activity level. Must be one of: {', '.join(VALID_ACTIVITY_LEVELS.keys())}")
    return True

def validate_unit(unit):
    """Validate that the unit is either 'lbs' or 'kg'"""
    if unit not in ['lbs', 'kg']:
        raise ValidationError("Unit must be either 'lbs' or 'kg'")
    return True

def determine_diet_phase(user, weight_entries):
    """
    Determine the current diet phase based on user's goal, progress, and history.
    Returns (phase, reason) tuple.
    """
    if len(weight_entries) < 2:
        if user.goal == 'recomp':
            return DietPhase.RECOMP, "Initial recomp phase"
        return DietPhase.CUT if user.goal == 'lose' else DietPhase.BULK, "Initial phase"
    
    # Sort entries by date
    sorted_entries = sorted(weight_entries, key=lambda x: x.date)
    
    # Calculate current rate and trend
    weekly_changes, current_rate, trend = calculate_weekly_weight_changes(weight_entries)
    
    # Enhanced metabolic adaptation detection
    if user.goal in ['lose', 'recomp']:
        days_in_deficit = (datetime.now() - sorted_entries[0].date).days
        if days_in_deficit >= MAX_DEFICIT_DURATION:
            return DietPhase.DIET_BREAK, "Extended deficit period"
        
        # More sophisticated adaptation detection
        if len(weekly_changes) >= 4:
            recent_rate = sum(weekly_changes[-4:]) / 4
            initial_rate = sum(weekly_changes[:4]) / 4
            
            # Check for multiple adaptation indicators
            adaptation_indicators = []
            
            # Rate of weight loss slowing
            if recent_rate / initial_rate < (1 - METABOLIC_ADAPTATION_THRESHOLD):
                adaptation_indicators.append("Weight loss rate decreasing")
            
            # Check for weight loss plateaus
            if len(weekly_changes) >= 2 and all(abs(change) < 0.1 for change in weekly_changes[-2:]):
                adaptation_indicators.append("Weight loss plateau detected")
            
            # Check for increased weight variability
            recent_variance = sum((w - sum(weekly_changes[-4:])/4)**2 for w in weekly_changes[-4:]) / 4
            initial_variance = sum((w - sum(weekly_changes[:4])/4)**2 for w in weekly_changes[:4]) / 4
            if recent_variance > initial_variance * 2:
                adaptation_indicators.append("Increased weight variability")
            
            if adaptation_indicators:
                return DietPhase.DIET_BREAK, f"Metabolic adaptation detected: {', '.join(adaptation_indicators)}"
    
    # Check if we're in maintenance
    if user.goal in ['lose', 'recomp'] and current_rate is not None:
        target_min, target_max = WEIGHT_LOSS_TARGETS.get(user.intensity, (0.45, 0.68))
        if abs(current_rate) < target_min * 0.2:  # Within 20% of maintenance
            # Check if this has been consistent
            recent_entries = [e for e in sorted_entries if (datetime.now() - e.date).days <= MAINTENANCE_PHASE_DURATION]
            if len(recent_entries) >= 3:
                recent_changes = [abs(e.weight - recent_entries[i-1].weight) * LB_TO_KG 
                                for i, e in enumerate(recent_entries[1:], 1)]
                if all(change < target_min * 0.2 for change in recent_changes):
                    return DietPhase.MAINTENANCE, "Weight stable within maintenance range"
    
    # Default to goal-based phase
    if user.goal == 'recomp':
        return DietPhase.RECOMP, "Active recomp phase"
    return (DietPhase.CUT if user.goal == 'lose' else DietPhase.BULK), "Active phase"

def calculate_maintenance_calories(tdee, diet_phase):
    """
    Calculate maintenance calories based on diet phase.
    Includes reverse dieting logic for coming out of deficits.
    """
    if diet_phase == DietPhase.DIET_BREAK:
        # During diet break, increase calories gradually
        return tdee * 1.1  # 10% above maintenance
    elif diet_phase == DietPhase.MAINTENANCE:
        return tdee
    else:
        return tdee  # For active phases, use regular TDEE

def calculate_macros(user):
    """Calculate macros based on user profile and historical data"""
    try:
        # Validate user's preferred unit
        preferred_unit = getattr(user, 'preferred_unit', 'lbs')
        validate_unit(preferred_unit)
        
        # Validate activity level
        validate_activity_level(user.activity_level)
        
        # Convert user's weight to kg for all calculations
        weight_kg, height_cm = convert_to_metric(user.weight, user.height)
        
        # Get weight entries from the last 4 weeks
        four_weeks_ago = datetime.now() - timedelta(weeks=4)
        weight_entries = WeightEntry.query.filter(
            WeightEntry.user_id == user.id,
            WeightEntry.date >= four_weeks_ago
        ).order_by(WeightEntry.date.asc()).all()
        
        # Calculate weekly weight changes
        weekly_changes, current_rate, trend = calculate_weekly_weight_changes(weight_entries)
        
        # Calculate TDEE
        actual_tdee = calculate_actual_tdee(user, weight_entries)
        theoretical_tdee = calculate_theoretical_tdee(user)
        
        # Calculate quality score for internal use only
        entry_quality = calculate_weight_entry_quality(weight_entries)
        
        # Use quality score to determine if we should trust actual TDEE
        if actual_tdee and entry_quality < 0.7:
            # If data quality is poor, bias towards theoretical TDEE
            tdee = (theoretical_tdee * 0.7) + (actual_tdee * 0.3)
        else:
            tdee = actual_tdee if actual_tdee else theoretical_tdee
        
        # Calculate BMR for safety checks
        if user.gender == 'male':
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * user.age + 5
        else:
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * user.age - 161
        
        # Get user's intensity preference (default to moderate)
        intensity = getattr(user, 'intensity', 'moderate')
        
        # Determine current diet phase
        diet_phase, phase_reason = determine_diet_phase(user, weight_entries)
        
        # Adjust TDEE based on diet phase
        maintenance_calories = calculate_maintenance_calories(tdee, diet_phase)
        
        # Calculate adaptive adjustments based on goal and progress
        if diet_phase == DietPhase.DIET_BREAK:
            calories = maintenance_calories
        elif diet_phase == DietPhase.MAINTENANCE:
            calories = maintenance_calories
        else:
            if user.goal == 'lose':
                deficit = calculate_adaptive_deficit(user, weight_entries, intensity)
                calories = tdee * (1 - deficit)
            elif user.goal == 'gain':
                surplus = calculate_adaptive_surplus(user, weight_entries, intensity)
                calories = tdee * (1 + surplus)
            else:
                calories = tdee
        
        # Validate calories are within safe ranges
        validate_calories(calories, bmr, user.gender)
        
        # Update protein calculations with minimum threshold
        protein_multipliers = {
            'sedentary': max(1.76, MIN_PROTEIN_G_PER_KG),    # 0.8 g/lb -> 1.76 g/kg
            'light': max(1.98, MIN_PROTEIN_G_PER_KG),        # 0.9 g/lb -> 1.98 g/kg
            'moderate': max(2.20, MIN_PROTEIN_G_PER_KG),     # 1.0 g/lb -> 2.20 g/kg
            'active': max(2.42, MIN_PROTEIN_G_PER_KG),       # 1.1 g/lb -> 2.42 g/kg
            'very_active': max(2.64, MIN_PROTEIN_G_PER_KG)   # 1.2 g/lb -> 2.64 g/kg
        }
        
        # Increase protein during deficit or recomp
        if user.goal in ['lose', 'recomp']:
            protein_multipliers = {
                'sedentary': max(2.20, MIN_PROTEIN_G_PER_KG),     # 1.0 g/lb -> 2.20 g/kg
                'light': max(2.42, MIN_PROTEIN_G_PER_KG),         # 1.1 g/lb -> 2.42 g/kg
                'moderate': max(2.64, MIN_PROTEIN_G_PER_KG),      # 1.2 g/lb -> 2.64 g/kg
                'active': max(2.86, MIN_PROTEIN_G_PER_KG),        # 1.3 g/lb -> 2.86 g/kg
                'very_active': max(2.86, MIN_PROTEIN_G_PER_KG)    # 1.3 g/lb -> 2.86 g/kg (capped)
            }
        
        # Calculate protein in kg
        protein_kg = weight_kg * protein_multipliers.get(user.activity_level, MIN_PROTEIN_G_PER_KG)
        
        # Fat ranges based on goal
        fat_ranges = {
            'lose': (0.20, 0.25),    # 20-25% of calories from fat during deficit
            'gain': (0.25, 0.35),    # 25-35% of calories from fat during surplus
            'maintain': (0.25, 0.35)  # 25-35% of calories from fat during maintenance
        }
        
        # Get the fat range for the user's goal
        min_fat_percent, max_fat_percent = fat_ranges.get(user.goal, (0.25, 0.35))
        
        # Calculate minimum and maximum fat
        min_fat = (calories * min_fat_percent) / 9
        max_fat = (calories * max_fat_percent) / 9
        
        # Calculate minimum and maximum carbs based on fat range
        max_carbs = (calories - (protein_kg * 4) - (min_fat * 9)) / 4
        min_carbs = (calories - (protein_kg * 4) - (max_fat * 9)) / 4
        
        # Convert protein back to pounds for the response
        protein_lb = protein_kg * KG_TO_LB
        
        # Add recomp-specific information to the response
        if user.goal == 'recomp':
            macros = {
                'calories': round(calories),
                'protein': {
                    'grams': round(protein_lb),
                    'per_lb': round(protein_multipliers.get(user.activity_level, MIN_PROTEIN_G_PER_KG) / KG_TO_LB, 2),
                    'per_kg': round(protein_multipliers.get(user.activity_level, MIN_PROTEIN_G_PER_KG), 2),
                    'distribution': {
                        'recommendation': 'Alternate between slight deficit and surplus based on training days'
                    }
                },
                'fat': {
                    'min': round(min_fat),
                    'max': round(max_fat)
                },
                'carbs': {
                    'min': round(min_carbs),
                    'max': round(max_carbs)
                },
                'tdee': round(tdee),
                'bmr': round(bmr),
                'adjustment_percentage': round((calories / tdee - 1) * 100),
                'protein_multiplier': {
                    'per_lb': round(protein_multipliers.get(user.activity_level, MIN_PROTEIN_G_PER_KG) / KG_TO_LB, 2),
                    'per_kg': round(protein_multipliers.get(user.activity_level, MIN_PROTEIN_G_PER_KG), 2)
                },
                'fat_percentage': {
                    'min': round(min_fat_percent * 100),
                    'max': round(max_fat_percent * 100)
                },
                'adjustment_info': {
                    'message': 'Calories adjusted based on weight trends' if len(weight_entries) >= 2 else None,
                    'using_actual_tdee': actual_tdee is not None,
                    'weight_entries_used': len(weight_entries),
                    'tdee_adjustment': {
                        'theoretical': round(theoretical_tdee),
                        'actual': round(actual_tdee) if actual_tdee else None,
                        'difference_percent': round((actual_tdee - theoretical_tdee) / theoretical_tdee * 100, 1) if actual_tdee else None
                    }
                },
                'weekly_weight_changes': {
                    'kg': [round(change, 3) for change in weekly_changes],
                    'lb': [round(change * KG_TO_LB, 2) for change in weekly_changes]
                },
                'current_rate': {
                    'kg': round(current_rate, 3) if current_rate is not None else None,
                    'lb': round(current_rate * KG_TO_LB, 2) if current_rate is not None else None
                },
                'trend': {
                    'kg': round(trend, 3) if trend is not None else None,
                    'lb': round(trend * KG_TO_LB, 2) if trend is not None else None,
                    'description': 'accelerating' if trend and trend > 0 else 'decelerating' if trend and trend < 0 else 'stable' if trend is not None else None
                },
                'target_weekly_change': {
                    'kg': {
                        'min': WEIGHT_LOSS_TARGETS[intensity][0] if user.goal == 'lose' else WEIGHT_GAIN_TARGETS[intensity][0],
                        'max': WEIGHT_LOSS_TARGETS[intensity][1] if user.goal == 'lose' else WEIGHT_GAIN_TARGETS[intensity][1]
                    },
                    'lb': {
                        'min': round(WEIGHT_LOSS_TARGETS[intensity][0] * KG_TO_LB, 2) if user.goal == 'lose' else round(WEIGHT_GAIN_TARGETS[intensity][0] * KG_TO_LB, 2),
                        'max': round(WEIGHT_LOSS_TARGETS[intensity][1] * KG_TO_LB, 2) if user.goal == 'lose' else round(WEIGHT_GAIN_TARGETS[intensity][1] * KG_TO_LB, 2)
                    }
                },
                'diet_phase': {
                    'current': diet_phase,
                    'reason': phase_reason,
                    'recommendations': {
                        'diet_break': 'Consider a diet break to reset metabolism' if diet_phase == DietPhase.CUT else None,
                        'maintenance': 'Maintain current calories to establish new baseline' if diet_phase == DietPhase.MAINTENANCE else None
                    }
                },
                'recomp_info': {
                    'calorie_range': {
                        'min': round(tdee * 0.9),  # 10% deficit
                        'max': round(tdee * 1.1)   # 10% surplus
                    },
                    'recommendation': 'Alternate between slight deficit and surplus based on training days'
                }
            }
        else:
            macros = {
                'calories': round(calories),
                'protein': {
                    'grams': round(protein_lb),
                    'per_lb': round(protein_multipliers.get(user.activity_level, MIN_PROTEIN_G_PER_KG) / KG_TO_LB, 2),
                    'per_kg': round(protein_multipliers.get(user.activity_level, MIN_PROTEIN_G_PER_KG), 2),
                    'distribution': {
                        'recommendation': 'Distribute protein across 3-6 meals, with 20-40g per meal.'
                    }
                },
                'fat': {
                    'min': round(min_fat),
                    'max': round(max_fat)
                },
                'carbs': {
                    'min': round(min_carbs),
                    'max': round(max_carbs)
                },
                'tdee': round(tdee),
                'bmr': round(bmr),
                'adjustment_percentage': round((calories / tdee - 1) * 100),
                'protein_multiplier': {
                    'per_lb': round(protein_multipliers.get(user.activity_level, MIN_PROTEIN_G_PER_KG) / KG_TO_LB, 2),
                    'per_kg': round(protein_multipliers.get(user.activity_level, MIN_PROTEIN_G_PER_KG), 2)
                },
                'fat_percentage': {
                    'min': round(min_fat_percent * 100),
                    'max': round(max_fat_percent * 100)
                },
                'adjustment_info': {
                    'message': 'Calories adjusted based on weight trends' if len(weight_entries) >= 2 else None,
                    'using_actual_tdee': actual_tdee is not None,
                    'weight_entries_used': len(weight_entries),
                    'tdee_adjustment': {
                        'theoretical': round(theoretical_tdee),
                        'actual': round(actual_tdee) if actual_tdee else None,
                        'difference_percent': round((actual_tdee - theoretical_tdee) / theoretical_tdee * 100, 1) if actual_tdee else None
                    }
                },
                'weekly_weight_changes': {
                    'kg': [round(change, 3) for change in weekly_changes],
                    'lb': [round(change * KG_TO_LB, 2) for change in weekly_changes]
                },
                'current_rate': {
                    'kg': round(current_rate, 3) if current_rate is not None else None,
                    'lb': round(current_rate * KG_TO_LB, 2) if current_rate is not None else None
                },
                'trend': {
                    'kg': round(trend, 3) if trend is not None else None,
                    'lb': round(trend * KG_TO_LB, 2) if trend is not None else None,
                    'description': 'accelerating' if trend and trend > 0 else 'decelerating' if trend and trend < 0 else 'stable' if trend is not None else None
                },
                'target_weekly_change': {
                    'kg': {
                        'min': WEIGHT_LOSS_TARGETS[intensity][0] if user.goal == 'lose' else WEIGHT_GAIN_TARGETS[intensity][0],
                        'max': WEIGHT_LOSS_TARGETS[intensity][1] if user.goal == 'lose' else WEIGHT_GAIN_TARGETS[intensity][1]
                    },
                    'lb': {
                        'min': round(WEIGHT_LOSS_TARGETS[intensity][0] * KG_TO_LB, 2) if user.goal == 'lose' else round(WEIGHT_GAIN_TARGETS[intensity][0] * KG_TO_LB, 2),
                        'max': round(WEIGHT_LOSS_TARGETS[intensity][1] * KG_TO_LB, 2) if user.goal == 'lose' else round(WEIGHT_GAIN_TARGETS[intensity][1] * KG_TO_LB, 2)
                    }
                },
                'diet_phase': {
                    'current': diet_phase,
                    'reason': phase_reason,
                    'recommendations': {
                        'diet_break': 'Consider a diet break to reset metabolism' if diet_phase == DietPhase.CUT else None,
                        'maintenance': 'Maintain current calories to establish new baseline' if diet_phase == DietPhase.MAINTENANCE else None
                    }
                }
            }
        
        return macros
    except ValidationError as e:
        raise ValidationError(f"Error calculating macros: {str(e)}")
    except Exception as e:
        raise ValidationError(f"Unexpected error in macro calculation: {str(e)}")

@macros_bp.route('/calculate', methods=['GET'])
@jwt_required()
def get_macros():
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get_or_404(user_id)
        
        # Validate user data
        validate_measurements(user.weight, user.height, user.age)
        
        macros = calculate_macros(user)
        return jsonify(macros)
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f"Unexpected error: {str(e)}"}), 500

@macros_bp.route('/update', methods=['POST'])
@jwt_required()
def update_macros():
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get_or_404(user_id)
        data = request.get_json()
        
        # Validate input data
        if 'goal' in data:
            if data['goal'] not in ['lose', 'maintain', 'gain', 'recomp']:
                raise ValidationError("Invalid goal. Must be 'lose', 'maintain', 'gain', or 'recomp'")
            user.goal = data['goal']
            
        if 'activity_level' in data:
            validate_activity_level(data['activity_level'])
            user.activity_level = data['activity_level']
            
        if 'preferred_unit' in data:
            validate_unit(data['preferred_unit'])
            user.preferred_unit = data['preferred_unit']
        
        db.session.commit()
        
        # Recalculate macros with new values
        macros = calculate_macros(user)
        return jsonify(macros)
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f"Unexpected error: {str(e)}"}), 500

def validate_weight_entry(new_weight, previous_entries, user):
    """
    Validate a new weight entry for accuracy and consistency.
    Returns (is_valid, warning_message) tuple.
    """
    if not previous_entries:
        return True, None
    
    # Convert weights to kg for comparison
    new_weight_kg = new_weight * LB_TO_KG
    last_weight_kg = previous_entries[-1].weight * LB_TO_KG
    
    # Check for significant changes
    weight_change_kg = abs(new_weight_kg - last_weight_kg)
    if weight_change_kg > MAX_WEIGHT_VARIANCE:
        warning_message = (
            f"Significant weight change detected ({round(weight_change_kg * KG_TO_LB, 1)} lbs). "
            "Please verify this entry is correct. This could be due to:"
            "\n- Water weight fluctuations"
            "\n- Post-carb refeed weight gain"
            "\n- Post-workout water retention"
            "\n- Digestive system contents"
            "\n- Menstrual cycle fluctuations"
            "\n- Scale calibration"
            "\n- Unit conversion error"
        )
        return True, warning_message
    
    return True, None

def calculate_weight_entry_quality(weight_entries):
    """
    Calculate the quality score of weight entries (0-1).
    Considers frequency and consistency of entries.
    """
    if not weight_entries:
        return 0
    
    # Sort entries by date
    sorted_entries = sorted(weight_entries, key=lambda x: x.date)
    
    # Calculate entry frequency score
    total_days = (sorted_entries[-1].date - sorted_entries[0].date).days + 1
    entries_per_week = len(weight_entries) / (total_days / 7)
    frequency_score = min(entries_per_week / MIN_WEIGHT_ENTRIES_PER_WEEK, 1)
    
    # Calculate consistency score
    weights_kg = [entry.weight * LB_TO_KG for entry in sorted_entries]
    variance = sum((w - sum(weights_kg)/len(weights_kg))**2 for w in weights_kg) / len(weights_kg)
    consistency_score = 1 - min(variance / MAX_WEIGHT_VARIANCE, 1)
    
    # Weighted average of scores (removed distribution score)
    return 0.6 * frequency_score + 0.4 * consistency_score

@macros_bp.route('/weight', methods=['POST'])
@jwt_required()
def add_weight_entry():
    try:
        user_id = int(get_jwt_identity())
        user = User.query.get_or_404(user_id)
        data = request.get_json()
        
        if 'weight' not in data:
            raise ValidationError("Weight is required")
        
        # Get user's preferred unit (default to pounds if not set)
        preferred_unit = getattr(user, 'preferred_unit', 'lbs')
        
        # Convert input weight to kg for internal calculations
        if data.get('unit', preferred_unit) == 'lbs':
            weight_kg = data['weight'] * LB_TO_KG
        else:
            weight_kg = data['weight']
            data['weight'] = weight_kg * KG_TO_LB  # Convert to lbs for storage
        
        # Get recent weight entries
        recent_entries = WeightEntry.query.filter(
            WeightEntry.user_id == user_id,
            WeightEntry.date >= datetime.now() - timedelta(days=7)
        ).order_by(WeightEntry.date.desc()).all()
        
        # Convert weights to kg for comparison
        last_weight_kg = recent_entries[-1].weight * LB_TO_KG if recent_entries else weight_kg
        
        # Check for significant changes
        weight_change_kg = abs(weight_kg - last_weight_kg)
        if weight_change_kg > MAX_WEIGHT_VARIANCE:
            # If this is a confirmation request, check the confirmation flag
            if not data.get('confirmed', False):
                # Format the weight in the user's preferred unit
                display_weight = data['weight'] if preferred_unit == 'lbs' else weight_kg
                display_unit = preferred_unit
                
                return jsonify({
                    'requires_confirmation': True,
                    'current_weight': display_weight,
                    'previous_weight': recent_entries[-1].weight if recent_entries else None,
                    'weight_change': round(weight_change_kg * KG_TO_LB, 1) if preferred_unit == 'lbs' else round(weight_change_kg, 1),
                    'unit': display_unit,
                    'warning_message': f"Please verify: Is {round(display_weight, 1)} {display_unit} correct?"
                }), 200
            
        # Create new weight entry
        new_entry = WeightEntry(
            user_id=user_id,
            weight=data['weight'],  # Store in pounds
            date=datetime.now()
        )
        
        db.session.add(new_entry)
        db.session.commit()
        
        # Recalculate macros with new weight entry
        macros = calculate_macros(user)
        return jsonify(macros)
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f"Unexpected error: {str(e)}"}), 500 