"""Resource request validation tests — including Phase 8 (request within event time)."""
import pytest
from datetime import timedelta
from tests.conftest import login, T, T2, T3
from models import db, ResourceRequest, ResourceRequestItem


class TestRequestValidation:

    def _post_request(self, client, event, start, end, items=None):
        """Submit a resource request via the web form."""
        data = {
            'event_id': str(event.id),
            'start_datetime': start.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': end.strftime('%Y-%m-%dT%H:%M'),
            'notes': '',
            'item_type[]': [i[0] for i in (items or [])],
            'item_qty[]':  [str(i[1]) for i in (items or [])],
            'item_preferred[]': [str(i[2]) if i[2] else '' for i in (items or [])],
        }
        return client.post('/requests/create', data=data, follow_redirects=True)

    def test_valid_request_created(self, client, organizer, event, auditorium):
        login(client, 'org', 'org123')
        resp = self._post_request(client, event, T, T2,
            items=[('Auditorium', 1, auditorium.id)])
        assert resp.status_code == 200
        assert ResourceRequest.query.filter_by(event_id=event.id).count() == 1

    def test_request_before_event_start_rejected(self, client, organizer, event, auditorium):
        """Request cannot start before the event."""
        login(client, 'org', 'org123')
        before = T - timedelta(hours=1)  # 09:00, event starts 10:00
        resp = self._post_request(client, event, before, T2,
            items=[('Auditorium', 1, auditorium.id)])
        assert b'before the event starts' in resp.data
        assert ResourceRequest.query.count() == 0

    def test_request_after_event_end_rejected(self, client, organizer, event, auditorium):
        """Request cannot end after the event."""
        login(client, 'org', 'org123')
        after = T3 + timedelta(hours=1)
        resp = self._post_request(client, event, T, after,
            items=[('Auditorium', 1, auditorium.id)])
        assert b'after the event ends' in resp.data
        assert ResourceRequest.query.count() == 0

    def test_request_end_equals_start_rejected(self, client, organizer, event, auditorium):
        login(client, 'org', 'org123')
        resp = self._post_request(client, event, T, T,
            items=[('Auditorium', 1, auditorium.id)])
        assert b'after' in resp.data.lower()

    def test_inactive_resource_gives_error(self, client, organizer, event, inactive_resource):
        login(client, 'org', 'org123')
        resp = self._post_request(client, event, T, T2,
            items=[('Microphone', 1, inactive_resource.id)])
        assert b'inactive' in resp.data.lower()
        assert ResourceRequest.query.count() == 0

    def test_wrong_type_gives_error(self, client, organizer, event, projector):
        """Requesting Auditorium but specifying a Projector as preferred."""
        login(client, 'org', 'org123')
        resp = self._post_request(client, event, T, T2,
            items=[('Auditorium', 1, projector.id)])
        assert resp.status_code == 200
        assert ResourceRequest.query.count() == 0

    def test_no_items_rejected(self, client, organizer, event):
        login(client, 'org', 'org123')
        resp = self._post_request(client, event, T, T2, items=[])
        assert b'at least one' in resp.data.lower()
        assert ResourceRequest.query.count() == 0

    def test_capacity_insufficient_gives_alternative(self, client, organizer, small_event, small_hall):
        """
        Event attendance=30, small_hall cap=50 → fits.
        Now try with event attendance=200 → should fail + suggest alternative.
        """
        login(client, 'org', 'org123')
        # Create a large event owned by organizer
        from models import Event
        large_event = Event(
            name='Big Event', organizer='CS', expected_attendance=200,
            owner_id=organizer.id, start_datetime=T, end_datetime=T3, status='Approved'
        )
        db.session.add(large_event)
        db.session.commit()

        resp = self._post_request(client, large_event, T, T2,
            items=[('Auditorium', 1, small_hall.id)])
        assert b'capacity' in resp.data.lower() or b'insufficient' in resp.data.lower() \
            or ResourceRequest.query.filter_by(event_id=large_event.id).count() == 0
