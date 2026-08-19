"""
Phase 11 + 17 — Atomic Multi-Resource Transaction Tests

Critical: if ANY resource in a request fails, ZERO allocations are created.
"""
import pytest
from datetime import timedelta
from tests.conftest import T, T2, T3, make_allocation
from services import process_allocation, cancel_allocation
from models import db, Resource, ResourceRequest, ResourceRequestItem, ResourceAllocation


class TestAtomicAllocation:

    def _make_request(self, db, event, items):
        """Helper: create a pending request with given items."""
        req = ResourceRequest(
            event_id=event.id,
            start_datetime=T, end_datetime=T2,
            status='Pending',
        )
        db.session.add(req)
        db.session.flush()
        for rtype, qty, pref_id in items:
            db.session.add(ResourceRequestItem(
                request_id=req.id,
                resource_type=rtype,
                quantity=qty,
                preferred_resource_id=pref_id,
            ))
        db.session.commit()
        return req

    def test_full_success_all_allocated(self, db, event, auditorium, projector, microphone):
        """All 3 resources available → all 3 allocated, status=Approved."""
        req = self._make_request(db, event, [
            ('Auditorium', 1, auditorium.id),
            ('Projector',  1, projector.id),
            ('Microphone', 1, microphone.id),
        ])
        success, msg = process_allocation(req)
        assert success is True
        allocs = ResourceAllocation.query.filter_by(request_id=req.id, status='Allocated').all()
        assert len(allocs) == 3
        db.session.refresh(req)
        assert req.status == 'Approved'

    def test_one_conflict_zero_allocated(self, db, event, auditorium, projector, microphone):
        """
        Auditorium booked, Projector & Mic available.
        → 0 allocations created (atomic rollback).
        """
        make_allocation(db, event, auditorium, T, T2)

        req = self._make_request(db, event, [
            ('Auditorium', 1, auditorium.id),
            ('Projector',  1, projector.id),
            ('Microphone', 1, microphone.id),
        ])
        before = ResourceAllocation.query.filter_by(status='Allocated').count()
        success, msg = process_allocation(req)
        after = ResourceAllocation.query.filter_by(status='Allocated').count()

        assert success is False
        assert after == before   # zero new allocations
        db.session.refresh(req)
        assert req.status == 'Pending'  # status not changed

    def test_inactive_resource_aborts_all(self, db, event, auditorium, inactive_resource):
        """If a resource is deactivated before approval, entire request fails."""
        req = self._make_request(db, event, [
            ('Auditorium', 1, auditorium.id),
            ('Microphone', 1, inactive_resource.id),
        ])
        before = ResourceAllocation.query.count()
        success, msg = process_allocation(req)
        after = ResourceAllocation.query.count()

        assert success is False
        assert after == before

    def test_double_approve_fails(self, db, event, auditorium):
        """Approving an already-approved request must fail gracefully."""
        req = self._make_request(db, event, [('Auditorium', 1, auditorium.id)])
        process_allocation(req)  # first approval
        db.session.refresh(req)
        assert req.status == 'Approved'

        success, msg = process_allocation(req)  # second approval
        assert success is False
        assert 'cannot be approved' in msg.lower() or 'already' in msg.lower()


class TestCancellation:

    def test_cancel_releases_resource(self, db, event, auditorium, projector):
        """After cancellation, resource must be available again."""
        from services import check_resource_conflict
        req, alloc = make_allocation(db, event, auditorium, T, T2)

        assert check_resource_conflict(auditorium.id, T, T2) is True
        cancel_allocation(req)
        assert check_resource_conflict(auditorium.id, T, T2) is False

    def test_cancel_preserves_history(self, db, event, auditorium):
        """Cancelled allocations remain in DB (not deleted)."""
        req, alloc = make_allocation(db, event, auditorium, T, T2)
        alloc_id = alloc.id
        cancel_allocation(req)

        row = db.session.get(ResourceAllocation, alloc_id)
        assert row is not None
        assert row.status == 'Cancelled'

    def test_cancel_pending_request(self, db, event, auditorium):
        """Pending requests (no allocations yet) can be cancelled."""
        req = ResourceRequest(
            event_id=event.id, start_datetime=T, end_datetime=T2, status='Pending'
        )
        db.session.add(req)
        db.session.commit()

        success, _ = cancel_allocation(req)
        assert success is True
        db.session.refresh(req)
        assert req.status == 'Cancelled'

    def test_cancel_already_cancelled_fails(self, db, event, auditorium):
        """Cannot cancel a request that is already cancelled."""
        req, _ = make_allocation(db, event, auditorium, T, T2)
        cancel_allocation(req)
        success, msg = cancel_allocation(req)
        assert success is False
