"""Resource management tests."""
import pytest
from tests.conftest import login, T, T2
from models import db, Resource


class TestResourceManagement:

    def test_admin_can_add_resource(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/resources/create', data={
            'name': 'New Lab', 'resource_type': 'Laboratory',
            'capacity': '40', 'is_active': 'on',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert Resource.query.filter_by(name='New Lab').first() is not None

    def test_invalid_resource_type_rejected(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/resources/create', data={
            'name': 'Mystery Box', 'resource_type': 'Spaceship',
            'capacity': '', 'is_active': 'on',
        }, follow_redirects=True)
        assert Resource.query.filter_by(resource_type='Spaceship').count() == 0

    def test_auditorium_requires_capacity(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/resources/create', data={
            'name': 'No Cap Hall', 'resource_type': 'Auditorium',
            'capacity': '', 'is_active': 'on',
        }, follow_redirects=True)
        assert b'capacity' in resp.data.lower()
        assert Resource.query.filter_by(name='No Cap Hall').count() == 0

    def test_negative_capacity_rejected(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/resources/create', data={
            'name': 'Neg Hall', 'resource_type': 'Auditorium',
            'capacity': '-50', 'is_active': 'on',
        }, follow_redirects=True)
        assert Resource.query.filter_by(name='Neg Hall').count() == 0

    def test_duplicate_name_rejected(self, client, admin, auditorium):
        login(client, 'admin', 'admin123')
        resp = client.post('/resources/create', data={
            'name': auditorium.name, 'resource_type': 'Auditorium',
            'capacity': '100', 'is_active': 'on',
        }, follow_redirects=True)
        assert Resource.query.filter_by(name=auditorium.name).count() == 1

    def test_toggle_activates_resource(self, client, admin, inactive_resource):
        login(client, 'admin', 'admin123')
        assert inactive_resource.is_active is False
        resp = client.post(f'/resources/{inactive_resource.id}/toggle', follow_redirects=True)
        db.session.refresh(inactive_resource)
        assert inactive_resource.is_active is True

    def test_projector_does_not_require_capacity(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/resources/create', data={
            'name': 'New Projector', 'resource_type': 'Projector',
            'capacity': '', 'is_active': 'on',
        }, follow_redirects=True)
        r = Resource.query.filter_by(name='New Projector').first()
        assert r is not None
        assert r.capacity is None
