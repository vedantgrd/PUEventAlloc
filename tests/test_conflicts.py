"""
Phase 9 — Conflict Detection Tests

Verifies all 7 overlap cases plus boundary cases and
that cancelled/rejected allocations do NOT block resources.
"""
import pytest
from datetime import datetime, timedelta
from tests.conftest import T, T2, T3, make_allocation
from services import check_resource_conflict
from models import db, ResourceAllocation

# Aliases for readability
H10 = T          # 10:00
H12 = T + timedelta(hours=2)   # 12:00
H14 = T2         # 14:00
H16 = T + timedelta(hours=6)   # 16:00
H8  = T - timedelta(hours=2)   # 08:00
H11 = T + timedelta(hours=1)   # 11:00
H13 = T + timedelta(hours=3)   # 13:00


class TestConflictDetection:

    def test_case1_partial_overlap_right(self, db, event, auditorium):
        """Existing 10-14, new 12-16 → CONFLICT."""
        make_allocation(db, event, auditorium, H10, H14)
        assert check_resource_conflict(auditorium.id, H12, H16) is True

    def test_case2_back_to_back_allowed(self, db, event, auditorium):
        """Existing 10-14, new 14-16 → ALLOWED (boundary touch is not a conflict)."""
        make_allocation(db, event, auditorium, H10, H14)
        assert check_resource_conflict(auditorium.id, H14, H16) is False

    def test_case3_adjacent_before_allowed(self, db, event, auditorium):
        """Existing 10-14, new 08-10 → ALLOWED."""
        make_allocation(db, event, auditorium, H10, H14)
        assert check_resource_conflict(auditorium.id, H8, H10) is False

    def test_case4_exact_same_interval(self, db, event, auditorium):
        """Existing 10-14, new 10-14 → CONFLICT."""
        make_allocation(db, event, auditorium, H10, H14)
        assert check_resource_conflict(auditorium.id, H10, H14) is True

    def test_case5_new_contains_existing(self, db, event, auditorium):
        """Existing 10-14, new 09-15 → CONFLICT."""
        make_allocation(db, event, auditorium, H10, H14)
        h9  = T - timedelta(hours=1)
        h15 = T + timedelta(hours=5)
        assert check_resource_conflict(auditorium.id, h9, h15) is True

    def test_case6_existing_contains_new(self, db, event, auditorium):
        """Existing 10-14, new 11-12 → CONFLICT."""
        make_allocation(db, event, auditorium, H10, H14)
        assert check_resource_conflict(auditorium.id, H11, H12) is True

    def test_case7_new_wraps_existing(self, db, event, auditorium):
        """Existing 10-14, new 08-16 → CONFLICT."""
        make_allocation(db, event, auditorium, H10, H14)
        assert check_resource_conflict(auditorium.id, H8, H16) is True

    def test_cancelled_allocation_does_not_block(self, db, event, auditorium):
        """Cancelled allocations must not prevent future bookings."""
        _, alloc = make_allocation(db, event, auditorium, H10, H14)
        alloc.status = 'Cancelled'
        db.session.commit()
        assert check_resource_conflict(auditorium.id, H10, H14) is False

    def test_different_resource_no_conflict(self, db, event, auditorium, projector):
        """Conflict is resource-specific; booking hall doesn't affect projector."""
        make_allocation(db, event, auditorium, H10, H14)
        assert check_resource_conflict(projector.id, H10, H14) is False

    def test_exclude_own_request(self, db, event, auditorium):
        """A request should not conflict with itself (used for editing)."""
        req, _ = make_allocation(db, event, auditorium, H10, H14)
        assert check_resource_conflict(
            auditorium.id, H10, H14, exclude_request_id=req.id
        ) is False
