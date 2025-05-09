import pytest
from backend.app import create_app
from backend.models.user import User
from backend.app import db

@pytest.fixture
def app():
    app = create_app('testing')
    
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()

def test_register_success(client):
    """Test successful user registration"""
    response = client.post('/api/auth/register', json={
        'email': 'test@example.com',
        'password': 'testpassword123',
        'first_name': 'Test',
        'last_name': 'User',
        'height': 180,
        'weight': 75,
        'age': 25,
        'gender': 'male',
        'activity_level': 'moderate',
        'goal': 'muscle_gain'
    })
    
    assert response.status_code == 201
    data = response.get_json()
    assert 'access_token' in data
    assert data['user']['email'] == 'test@example.com'
    assert 'password' not in data['user']

def test_register_duplicate_email(client):
    """Test registration with duplicate email"""
    # First registration
    client.post('/api/auth/register', json={
        'email': 'test@example.com',
        'password': 'testpassword123'
    })
    
    # Second registration with same email
    response = client.post('/api/auth/register', json={
        'email': 'test@example.com',
        'password': 'testpassword123'
    })
    
    assert response.status_code == 400
    assert response.get_json()['error'] == 'Email already registered'

def test_login_success(client):
    """Test successful login"""
    # First register a user
    client.post('/api/auth/register', json={
        'email': 'test@example.com',
        'password': 'testpassword123'
    })
    
    # Then try to login
    response = client.post('/api/auth/login', json={
        'email': 'test@example.com',
        'password': 'testpassword123'
    })
    
    assert response.status_code == 200
    data = response.get_json()
    assert 'access_token' in data
    assert data['user']['email'] == 'test@example.com'

def test_login_invalid_credentials(client):
    """Test login with invalid credentials"""
    response = client.post('/api/auth/login', json={
        'email': 'test@example.com',
        'password': 'wrongpassword'
    })
    
    assert response.status_code == 401
    assert response.get_json()['error'] == 'Invalid email or password' 