import pytest
from backend.app import create_app, db
from backend.models.user import User

@pytest.fixture(scope='session')
def app():
    """Create application for the tests."""
    app = create_app('testing')
    
    # Create tables
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

@pytest.fixture(scope='function')
def client(app):
    """Create a test client for the app."""
    return app.test_client()

@pytest.fixture(scope='function')
def test_user(app):
    """Create a test user."""
    with app.app_context():
        user = User(
            email='test@example.com',
            password='testpassword123',
            first_name='Test',
            last_name='User'
        )
        db.session.add(user)
        db.session.commit()
        return user 