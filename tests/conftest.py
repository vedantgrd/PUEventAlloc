"""Shared pytest fixtures for PUEventAlloc test suite."""
import pytest
from datetime import datetime, timedelta
from app import create_app
from models import db as _db, User, Resource, Event, ResourceRequest, ResourceRequestItem, ResourceAllocation

T = datetime(2026, 10, 1, 10, 0)   # base test time: 10:00
T2 = T + timedelta(hours=4)        # 14:00
T3 = T + timedelta(hours=8)        # 18:00


@pytest.fixture(scope='session')
def app():
    application = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test-secret',
        'LOGIN_DISABLED': False,
    })
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(scope='function')
def db(app):
    """Fresh DB state for every test — rolls back after each."""
    with app.app_context():
        yield _db
        _db.session.rollback()
        # Clear all tables between tests
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture
def client(app, db):
    return app.test_client()


# ── User fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def admin(db):
    u = User(username='admin', name='Admin', email='admin@test.com', role='Admin')
    u.set_password('admin123')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def organizer(db):
    u = User(username='org', name='Organizer', email='org@test.com', role='Organizer')
    u.set_password('org123')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def organizer2(db):
    u = User(username='org2', name='Organizer2', email='org2@test.com', role='Organizer')
    u.set_password('org123')
    db.session.add(u)
    db.session.commit()
    return u


# ── Login helpers ──────────────────────────────────────────────────────────────

def login(client, username, password):
    return client.post('/login', data={'username': username, 'password': password},
                       follow_redirects=True)


# ── Resource fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def auditorium(db):
    r = Resource(name='Main Hall', resource_type='Auditorium', capacity=300, is_active=True)
    db.session.add(r)
    db.session.commit()
    return r


@pytest.fixture
def small_hall(db):
    r = Resource(name='Small Hall', resource_type='Auditorium', capacity=50, is_active=True)
    db.session.add(r)
    db.session.commit()
    return r


@pytest.fixture
def projector(db):
    r = Resource(name='Proj1', resource_type='Projector', is_active=True)
    db.session.add(r)
    db.session.commit()
    return r


@pytest.fixture
def microphone(db):
    r = Resource(name='Mic1', resource_type='Microphone', is_active=True)
    db.session.add(r)
    db.session.commit()
    return r


@pytest.fixture
def inactive_resource(db):
    r = Resource(name='Broken Mic', resource_type='Microphone', is_active=False)
    db.session.add(r)
    db.session.commit()
    return r


# ── Event fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def event(db, organizer):
    e = Event(
        name='Test Event', organizer='CS Dept',
        expected_attendance=100, owner_id=organizer.id,
        start_datetime=T, end_datetime=T3, status='Approved',
    )
    db.session.add(e)
    db.session.commit()
    return e


@pytest.fixture
def small_event(db, organizer):
    e = Event(
        name='Small Event', organizer='IT Dept',
        expected_attendance=30, owner_id=organizer.id,
        start_datetime=T, end_datetime=T3, status='Approved',
    )
    db.session.add(e)
    db.session.commit()
    return e


# ── Request + allocation helpers ───────────────────────────────────────────────

def make_allocation(db, event, resource, start=None, end=None):
    """Helper: create an approved request + active allocation."""
    start = start or T
    end = end or T2
    req = ResourceRequest(
        event_id=event.id, start_datetime=start, end_datetime=end, status='Approved'
    )
    db.session.add(req)
    db.session.flush()
    db.session.add(ResourceRequestItem(
        request_id=req.id, resource_type=resource.resource_type, quantity=1,
        preferred_resource_id=resource.id,
    ))
    alloc = ResourceAllocation(
        request_id=req.id, resource_id=resource.id,
        start_datetime=start, end_datetime=end, status='Allocated',
    )
    db.session.add(alloc)
    db.session.commit()
    return req, alloc
