"""
Phase 5 + 17 — Authorization tests.
Verifies that organizers cannot access admin routes directly.
"""
import pytest
from tests.conftest import login, T, T2
from models import db, Resource, Event, ResourceRequest


class TestAdminRoutes:

    def test_organizer_cannot_create_resource(self, client, organizer):
        login(client, 'org', 'org123')
        resp = client.post('/resources/create', data={
            'name': 'Stolen Hall', 'resource_type': 'Auditorium',
            'capacity': '200', 'is_active': 'on',
        }, follow_redirects=False)
        assert resp.status_code in (302, 403)
        if resp.status_code == 302:
            assert 'login' in resp.headers.get('Location', '') or resp.status_code == 302

    def test_organizer_cannot_toggle_resource(self, client, organizer, auditorium):
        login(client, 'org', 'org123')
        resp = client.post(f'/resources/{auditorium.id}/toggle', follow_redirects=False)
        assert resp.status_code in (302, 403)

    def test_organizer_cannot_approve_request(self, client, organizer, event, db):
        login(client, 'org', 'org123')
        req = ResourceRequest(
            event_id=event.id, start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()
        resp = client.post(f'/requests/{req.id}/approve', follow_redirects=False)
        assert resp.status_code in (302, 403)

    def test_organizer_cannot_reject_request(self, client, organizer, event, db):
        login(client, 'org', 'org123')
        req = ResourceRequest(
            event_id=event.id, start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()
        resp = client.post(f'/requests/{req.id}/reject',
            data={'rejection_reason': 'test'}, follow_redirects=False)
        assert resp.status_code in (302, 403)

    def test_organizer_cannot_access_other_request(self, client, organizer, organizer2, event, db):
        """Organizer 2 cannot view Organizer 1's request."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'org2', 'org123')
        resp = client.get(f'/requests/{req.id}', follow_redirects=False)
        assert resp.status_code == 403

    def test_unauthenticated_cannot_access_dashboard(self, client):
        resp = client.get('/', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_admin_can_access_all_requests(self, client, admin, organizer, event, db):
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'admin', 'admin123')
        resp = client.get(f'/requests/{req.id}')
        assert resp.status_code == 200

    def test_invalid_login_rejected(self, client, admin):
        resp = client.post('/login', data={
            'username': 'admin', 'password': 'wrongpassword'
        }, follow_redirects=True)
        assert b'invalid' in resp.data.lower()


class TestStateTransitions:

    def test_reject_already_approved_fails(self, client, admin, event, auditorium, db):
        from tests.conftest import make_allocation
        from services import process_allocation
        req = ResourceRequest(
            event_id=event.id, start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()
        from models import ResourceRequestItem
        db.session.add(ResourceRequestItem(
            request_id=req.id, resource_type='Auditorium',
            quantity=1, preferred_resource_id=auditorium.id
        ))
        db.session.commit()
        process_allocation(req)

        login(client, 'admin', 'admin123')
        resp = client.post(f'/requests/{req.id}/reject',
            data={'rejection_reason': 'too late'}, follow_redirects=True)
        db.session.refresh(req)
        assert req.status == 'Approved'  # must not have changed to Rejected
