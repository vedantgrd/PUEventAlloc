"""
Role-based workflow tests — the canonical enforcement suite.

Proves:
  1. Organizer can create events and submit requests.
  2. Organizer CANNOT approve / reject / allocate via any endpoint.
  3. Admin CAN approve and reject.
  4. Approval creates allocations atomically.
  5. Failed approval (partial) creates zero allocations.
  6. Pending requests are visible to admin, scoped for organizer.
  7. Complete end-to-end workflow: Org submits → Admin approves → Org sees result.
"""
import pytest
from datetime import timedelta
from tests.conftest import T, T2, T3, login, make_allocation
from models import (
    db, Event, Resource, ResourceRequest,
    ResourceRequestItem, ResourceAllocation
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _create_event(client, name='Test Event', attendance=100):
    """Organizer creates an event via the web form."""
    return client.post('/events/create', data={
        'name': name,
        'organizer': 'CS Dept',
        'expected_attendance': str(attendance),
        'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
        'end_datetime': T3.strftime('%Y-%m-%dT%H:%M'),
        'status': 'Approved',
    }, follow_redirects=True)


def _submit_request(client, event_id, resource_type, resource_id=None):
    """Organizer submits a resource request via the web form."""
    return client.post('/requests/create', data={
        'event_id': str(event_id),
        'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
        'end_datetime': T2.strftime('%Y-%m-%dT%H:%M'),
        'notes': 'test request',
        'item_type[]': [resource_type],
        'item_qty[]': ['1'],
        'item_preferred[]': [str(resource_id) if resource_id else ''],
    }, follow_redirects=True)


# ─── Organizer capability tests ───────────────────────────────────────────────

class TestOrganizerCapabilities:

    def test_organizer_can_create_event(self, client, organizer):
        login(client, 'org', 'org123')
        _create_event(client, 'Org Event')
        assert Event.query.filter_by(name='Org Event').count() == 1

    def test_organizer_can_submit_request(self, client, organizer, event, auditorium):
        login(client, 'org', 'org123')
        _submit_request(client, event.id, 'Auditorium', auditorium.id)
        req = ResourceRequest.query.filter_by(event_id=event.id).first()
        assert req is not None

    def test_submitted_request_is_pending(self, client, organizer, event, auditorium):
        login(client, 'org', 'org123')
        _submit_request(client, event.id, 'Auditorium', auditorium.id)
        req = ResourceRequest.query.filter_by(event_id=event.id).first()
        assert req.status == 'Pending'

    def test_no_allocation_after_submission(self, client, organizer, event, auditorium):
        """Submitting a request must NOT create any allocation."""
        login(client, 'org', 'org123')
        _submit_request(client, event.id, 'Auditorium', auditorium.id)
        assert ResourceAllocation.query.count() == 0

    def test_organizer_can_view_own_request(self, client, organizer, event, db):
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()
        login(client, 'org', 'org123')
        resp = client.get(f'/requests/{req.id}')
        assert resp.status_code == 200

    def test_organizer_can_view_approved_allocation(self, client, organizer, event, auditorium, db):
        """After admin approval, organizer can see the allocated resources."""
        req, alloc = make_allocation(db, event, auditorium)
        req.requester_id = organizer.id
        db.session.commit()
        login(client, 'org', 'org123')
        resp = client.get(f'/requests/{req.id}')
        assert resp.status_code == 200
        assert b'Allocated' in resp.data or b'Approved' in resp.data

    def test_organizer_sees_no_approve_button(self, client, organizer, event, db):
        """The Approve & Allocate button must not appear for organizers."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()
        login(client, 'org', 'org123')
        resp = client.get(f'/requests/{req.id}')
        assert b'Approve &amp; Allocate' not in resp.data
        assert b'Approve & Allocate' not in resp.data

    def test_organizer_sees_no_reject_button(self, client, organizer, event, db):
        """The Reject button must not appear for organizers."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()
        login(client, 'org', 'org123')
        resp = client.get(f'/requests/{req.id}')
        assert b'Confirm Rejection' not in resp.data

    def test_organizer_sees_awaiting_admin_message(self, client, organizer, event, db):
        """Organizer should see a clear 'awaiting approval' status message."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()
        login(client, 'org', 'org123')
        resp = client.get(f'/requests/{req.id}')
        assert b'Awaiting' in resp.data or b'awaiting' in resp.data or b'admin' in resp.data.lower()


# ─── Organizer CANNOT tests — backend enforcement ─────────────────────────────

class TestOrganizerCannotAllocate:

    def test_organizer_cannot_approve_returns_403(self, client, organizer, event, db):
        """
        CRITICAL: POST to approve endpoint by organizer must return 403.
        Backend must reject this even if organizer constructs the request manually.
        """
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'org', 'org123')
        resp = client.post(f'/requests/{req.id}/approve', follow_redirects=False)
        assert resp.status_code == 403

    def test_organizer_approve_attempt_leaves_pending(self, client, organizer, event, db):
        """After a 403'd approve attempt, request must still be Pending."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'org', 'org123')
        client.post(f'/requests/{req.id}/approve', follow_redirects=False)

        db.session.refresh(req)
        assert req.status == 'Pending'

    def test_organizer_approve_attempt_creates_zero_allocations(
        self, client, organizer, event, auditorium, db
    ):
        """After a 403'd approve attempt, zero allocations must exist."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.flush()
        db.session.add(ResourceRequestItem(
            request_id=req.id, resource_type='Auditorium',
            quantity=1, preferred_resource_id=auditorium.id
        ))
        db.session.commit()

        login(client, 'org', 'org123')
        client.post(f'/requests/{req.id}/approve', follow_redirects=False)

        assert ResourceAllocation.query.count() == 0

    def test_organizer_cannot_reject_returns_403(self, client, organizer, event, db):
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'org', 'org123')
        resp = client.post(
            f'/requests/{req.id}/reject',
            data={'rejection_reason': 'self reject'},
            follow_redirects=False
        )
        assert resp.status_code == 403

    def test_organizer_reject_attempt_leaves_pending(self, client, organizer, event, db):
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'org', 'org123')
        client.post(f'/requests/{req.id}/reject',
                    data={'rejection_reason': 'hack'}, follow_redirects=False)

        db.session.refresh(req)
        assert req.status == 'Pending'

    def test_organizer_cannot_access_another_organizers_request(
        self, client, organizer, organizer2, event, db
    ):
        """IDOR prevention: organizer2 cannot view organizer1's request."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'org2', 'org123')
        resp = client.get(f'/requests/{req.id}', follow_redirects=False)
        assert resp.status_code == 403

    def test_organizer_cannot_create_resource(self, client, organizer):
        login(client, 'org', 'org123')
        resp = client.post('/resources/create', data={
            'name': 'Stolen Hall', 'resource_type': 'Auditorium',
            'capacity': '100', 'is_active': 'on',
        }, follow_redirects=False)
        assert resp.status_code == 403

    def test_organizer_cannot_toggle_resource(self, client, organizer, auditorium):
        login(client, 'org', 'org123')
        resp = client.post(f'/resources/{auditorium.id}/toggle', follow_redirects=False)
        assert resp.status_code == 403

    def test_organizer_cannot_access_other_event(self, client, organizer, organizer2, db):
        """IDOR: org2 cannot edit org1's event."""
        e = Event(
            name='Org1 Event', organizer='CS', expected_attendance=50,
            owner_id=organizer.id, start_datetime=T, end_datetime=T2, status='Draft'
        )
        db.session.add(e)
        db.session.commit()

        login(client, 'org2', 'org123')
        resp = client.get(f'/events/{e.id}', follow_redirects=False)
        assert resp.status_code == 403


# ─── Admin capability tests ───────────────────────────────────────────────────

class TestAdminCapabilities:

    def test_admin_can_see_all_pending_requests(self, client, admin, organizer, event, db):
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'admin', 'admin123')
        resp = client.get('/requests?status=Pending')
        assert resp.status_code == 200
        assert f'#{req.id}'.encode() in resp.data

    def test_admin_can_view_any_request(self, client, admin, organizer, event, db):
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'admin', 'admin123')
        resp = client.get(f'/requests/{req.id}')
        assert resp.status_code == 200

    def test_admin_sees_approve_button(self, client, admin, organizer, event, db):
        """Admin must see the Approve & Allocate button on pending requests."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'admin', 'admin123')
        resp = client.get(f'/requests/{req.id}')
        assert b'Approve' in resp.data

    def test_admin_approve_creates_allocation(self, client, admin, organizer, event, auditorium, db):
        """Admin approval must create a ResourceAllocation record."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.flush()
        db.session.add(ResourceRequestItem(
            request_id=req.id, resource_type='Auditorium',
            quantity=1, preferred_resource_id=auditorium.id
        ))
        db.session.commit()

        login(client, 'admin', 'admin123')
        resp = client.post(f'/requests/{req.id}/approve', follow_redirects=True)
        assert resp.status_code == 200

        db.session.refresh(req)
        assert req.status == 'Approved'
        assert ResourceAllocation.query.filter_by(
            request_id=req.id, status='Allocated'
        ).count() == 1

    def test_admin_reject_changes_status(self, client, admin, organizer, event, db):
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        login(client, 'admin', 'admin123')
        resp = client.post(f'/requests/{req.id}/reject',
            data={'rejection_reason': 'Hall unavailable.'}, follow_redirects=True)
        assert resp.status_code == 200

        db.session.refresh(req)
        assert req.status == 'Rejected'
        assert req.rejection_reason == 'Hall unavailable.'

    def test_admin_reject_creates_no_allocation(self, client, admin, organizer, event, auditorium, db):
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.flush()
        db.session.add(ResourceRequestItem(
            request_id=req.id, resource_type='Auditorium',
            quantity=1, preferred_resource_id=auditorium.id
        ))
        db.session.commit()

        login(client, 'admin', 'admin123')
        client.post(f'/requests/{req.id}/reject',
            data={'rejection_reason': 'no'}, follow_redirects=True)

        assert ResourceAllocation.query.filter_by(request_id=req.id).count() == 0

    def test_admin_dashboard_shows_pending_count(self, client, admin, organizer, event, db):
        """Admin dashboard must prominently display pending request count."""
        for i in range(3):
            db.session.add(ResourceRequest(
                event_id=event.id, requester_id=organizer.id,
                start_datetime=T, end_datetime=T2, status='Pending'
            ))
        db.session.commit()

        login(client, 'admin', 'admin123')
        resp = client.get('/')
        assert resp.status_code == 200
        # The prominent alert should mention the count
        assert b'3' in resp.data
        assert b'Awaiting' in resp.data or b'awaiting' in resp.data or b'Approval' in resp.data


# ─── Complete End-to-End Workflow Test ───────────────────────────────────────

class TestCompleteWorkflow:

    def test_full_org_submit_admin_approve_org_view(
        self, client, organizer, admin, event, auditorium, projector, db
    ):
        """
        End-to-end canonical workflow:
          1. Organizer submits request → PENDING, 0 allocations
          2. Admin approves → APPROVED, allocations created
          3. Organizer views result → sees allocated resources, no approve button
        """
        # Step 1: Organizer submits request
        login(client, 'org', 'org123')
        resp = client.post('/requests/create', data={
            'event_id': str(event.id),
            'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': T2.strftime('%Y-%m-%dT%H:%M'),
            'notes': 'e2e test',
            'item_type[]': ['Auditorium', 'Projector'],
            'item_qty[]': ['1', '1'],
            'item_preferred[]': [str(auditorium.id), str(projector.id)],
        }, follow_redirects=True)
        assert resp.status_code == 200

        req = ResourceRequest.query.filter_by(event_id=event.id).first()
        assert req is not None
        assert req.status == 'Pending'
        assert ResourceAllocation.query.count() == 0   # no allocations yet

        client.get('/logout')

        # Step 2: Admin approves
        login(client, 'admin', 'admin123')

        # Verify pending request appears in admin's list
        list_resp = client.get('/requests?status=Pending')
        assert f'#{req.id}'.encode() in list_resp.data

        # Approve
        approve_resp = client.post(f'/requests/{req.id}/approve', follow_redirects=True)
        assert approve_resp.status_code == 200

        db.session.refresh(req)
        assert req.status == 'Approved'
        allocs = ResourceAllocation.query.filter_by(request_id=req.id, status='Allocated').all()
        assert len(allocs) == 2   # auditorium + projector

        client.get('/logout')

        # Step 3: Organizer views result
        login(client, 'org', 'org123')
        detail_resp = client.get(f'/requests/{req.id}')
        assert detail_resp.status_code == 200
        assert b'Approved' in detail_resp.data
        # Organizer sees allocated resources
        assert auditorium.name.encode() in detail_resp.data
        assert projector.name.encode() in detail_resp.data
        # Organizer does NOT see approve button
        assert b'Approve &amp; Allocate' not in detail_resp.data
        assert b'Confirm Rejection' not in detail_resp.data

    def test_atomic_rollback_org_submit_admin_partial_fail(
        self, client, organizer, admin, event, auditorium, projector, microphone, db
    ):
        """
        Organizer requests 3 resources.
        Before admin approves, one is booked elsewhere.
        Admin approval must fail atomically → 0 new allocations.
        """
        # Organizer submits
        login(client, 'org', 'org123')
        client.post('/requests/create', data={
            'event_id': str(event.id),
            'start_datetime': T.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': T2.strftime('%Y-%m-%dT%H:%M'),
            'notes': 'atomic test',
            'item_type[]': ['Auditorium', 'Projector', 'Microphone'],
            'item_qty[]': ['1', '1', '1'],
            'item_preferred[]': [str(auditorium.id), str(projector.id), str(microphone.id)],
        }, follow_redirects=True)
        client.get('/logout')

        req = ResourceRequest.query.filter_by(event_id=event.id).first()
        assert req.status == 'Pending'

        # Another request books the microphone before admin acts
        make_allocation(db, event, microphone, T, T2)
        before_count = ResourceAllocation.query.filter_by(status='Allocated').count()

        # Admin tries to approve — should fail atomically
        login(client, 'admin', 'admin123')
        resp = client.post(f'/requests/{req.id}/approve', follow_redirects=True)
        assert resp.status_code == 200

        db.session.refresh(req)
        # Request is still Pending (not partially approved)
        assert req.status == 'Pending'

        after_count = ResourceAllocation.query.filter_by(status='Allocated').count()
        # No new allocations for this request
        assert after_count == before_count

    def test_rejection_reason_visible_to_organizer(
        self, client, organizer, admin, event, db
    ):
        """Rejection reason set by admin must be visible to organizer."""
        req = ResourceRequest(
            event_id=event.id, requester_id=organizer.id,
            start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        # Admin rejects with a reason
        login(client, 'admin', 'admin123')
        client.post(f'/requests/{req.id}/reject',
            data={'rejection_reason': 'Auditorium A is under maintenance.'})
        client.get('/logout')

        # Organizer sees the reason
        login(client, 'org', 'org123')
        resp = client.get(f'/requests/{req.id}')
        assert b'under maintenance' in resp.data
