"""Event CRUD and validation tests."""
import pytest
from tests.conftest import login, T, T2, T3
from models import db, Event
from datetime import timedelta


class TestEventValidation:

    def test_create_valid_event(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/events/create', data={
            'name': 'Tech Fest', 'organizer': 'CS Dept',
            'expected_attendance': '150',
            'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': T2.strftime('%Y-%m-%dT%H:%M'),
            'status': 'Draft',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert Event.query.filter_by(name='Tech Fest').first() is not None

    def test_missing_name_rejected(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/events/create', data={
            'name': '', 'organizer': 'CS Dept',
            'expected_attendance': '100',
            'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': T2.strftime('%Y-%m-%dT%H:%M'),
            'status': 'Draft',
        }, follow_redirects=True)
        assert b'required' in resp.data.lower()
        assert Event.query.count() == 0

    def test_negative_attendance_rejected(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/events/create', data={
            'name': 'Bad Event', 'organizer': 'Dept',
            'expected_attendance': '-10',
            'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': T2.strftime('%Y-%m-%dT%H:%M'),
            'status': 'Draft',
        }, follow_redirects=True)
        assert b'positive' in resp.data.lower()

    def test_end_before_start_rejected(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/events/create', data={
            'name': 'Bad Dates', 'organizer': 'Dept',
            'expected_attendance': '50',
            'start_datetime': T2.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': T.strftime('%Y-%m-%dT%H:%M'),
            'status': 'Draft',
        }, follow_redirects=True)
        assert b'after' in resp.data.lower()

    def test_start_equals_end_rejected(self, client, admin):
        login(client, 'admin', 'admin123')
        resp = client.post('/events/create', data={
            'name': 'Zero Duration', 'organizer': 'Dept',
            'expected_attendance': '50',
            'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': T.strftime('%Y-%m-%dT%H:%M'),
            'status': 'Draft',
        }, follow_redirects=True)
        assert b'after' in resp.data.lower()

    def test_invalid_status_transition_blocked(self, client, admin, organizer):
        login(client, 'admin', 'admin123')
        # Create event as admin
        client.post('/events/create', data={
            'name': 'Trans Event', 'organizer': 'CS Dept',
            'expected_attendance': '50',
            'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': T2.strftime('%Y-%m-%dT%H:%M'),
            'status': 'Cancelled',
        }, follow_redirects=True)
        event = Event.query.filter_by(name='Trans Event').first()
        if event:
            # Cancelled → Approved should be blocked
            resp = client.post(f'/events/{event.id}/edit', data={
                'name': 'Trans Event', 'organizer': 'CS Dept',
                'expected_attendance': '50',
                'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
                'end_datetime': T2.strftime('%Y-%m-%dT%H:%M'),
                'status': 'Approved',
            }, follow_redirects=True)
            # Either blocked by "cannot edit cancelled" or transition error
            event_after = Event.query.get(event.id)
            # Status should not have jumped to Approved from Cancelled
            assert event_after.status != 'Approved'


class TestEventAuthorization:

    def test_organizer_cannot_see_others_event(self, client, organizer, organizer2, db):
        login(client, 'org2', 'org123')
        e = Event(name='Org1 Event', organizer='CS', expected_attendance=50,
                  owner_id=organizer.id, start_datetime=T, end_datetime=T2, status='Draft')
        db.session.add(e)
        db.session.commit()
        resp = client.get(f'/events/{e.id}')
        assert resp.status_code == 403

    def test_admin_can_see_all_events(self, client, admin, organizer, db):
        login(client, 'admin', 'admin123')
        e = Event(name='Someone Elses Event', organizer='CS', expected_attendance=50,
                  owner_id=organizer.id, start_datetime=T, end_datetime=T2, status='Draft')
        db.session.add(e)
        db.session.commit()
        resp = client.get(f'/events/{e.id}')
        assert resp.status_code == 200

    def test_unauthenticated_redirected_to_login(self, client):
        resp = client.get('/events', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']
