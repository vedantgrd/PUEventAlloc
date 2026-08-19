"""
Phase 13 + 17 — Alternative Resource Selection Tests
"""
import pytest
from datetime import timedelta
from tests.conftest import T, T2, T3, make_allocation
from services import find_alternative
from models import db, Resource


class TestAlternativeSelection:

    def test_returns_none_when_none_available(self, db, event, auditorium):
        """No alternative if only resource is booked and nothing else exists."""
        make_allocation(db, event, auditorium, T, T2)
        alt = find_alternative('Auditorium', 100, T, T2)
        assert alt is None

    def test_finds_available_alternative(self, db, event, auditorium, small_hall):
        """Main hall booked → system suggests small_hall (if capacity allows)."""
        make_allocation(db, event, auditorium, T, T2)
        # small_hall capacity=50, we need capacity for 30 people
        alt = find_alternative('Auditorium', 30, T, T2, exclude_ids=[auditorium.id])
        assert alt is not None
        assert alt.id == small_hall.id

    def test_excludes_insufficient_capacity(self, db, event, auditorium, small_hall):
        """Alternative must meet capacity — small_hall (cap 50) rejected for 200 people."""
        make_allocation(db, event, auditorium, T, T2)
        alt = find_alternative('Auditorium', 200, T, T2, exclude_ids=[auditorium.id])
        assert alt is None  # small_hall can't fit 200

    def test_excludes_inactive_resource(self, db, event, inactive_resource):
        """Inactive resources must never be suggested as alternatives."""
        # Only a Microphone exists but it's inactive
        alt = find_alternative('Microphone', None, T, T2)
        assert alt is None

    def test_excludes_wrong_type(self, db, event, auditorium, projector):
        """Alternative must be same type — Projector cannot substitute Auditorium."""
        make_allocation(db, event, auditorium, T, T2)
        alt = find_alternative('Auditorium', 100, T, T2)
        # projector is not an auditorium so it should not appear
        if alt:
            assert alt.resource_type == 'Auditorium'

    def test_smallest_fit_preferred(self, db, event):
        """Given Hall A (cap 100) and Hall B (cap 200), prefer Hall A for attendance=80."""
        hall_a = Resource(name='Hall A', resource_type='Auditorium', capacity=100, is_active=True)
        hall_b = Resource(name='Hall B', resource_type='Auditorium', capacity=200, is_active=True)
        db.session.add_all([hall_a, hall_b])
        db.session.commit()

        alt = find_alternative('Auditorium', 80, T, T2)
        assert alt is not None
        assert alt.name == 'Hall A'  # smallest that fits

    def test_skips_booked_candidates(self, db, event, auditorium, small_hall):
        """If both halls are booked during requested time, no alternative returned."""
        make_allocation(db, event, auditorium, T, T2)
        make_allocation(db, event, small_hall, T, T2)
        alt = find_alternative('Auditorium', 30, T, T2)
        assert alt is None

    def test_back_to_back_is_available(self, db, event, auditorium):
        """Hall booked 10-14 → back-to-back 14-16 is NOT a conflict → valid alternative slot."""
        make_allocation(db, event, auditorium, T, T2)
        # auditorium is available at T2-T3 (back-to-back)
        alt = find_alternative('Auditorium', 50, T2, T3)
        assert alt is not None
        assert alt.id == auditorium.id
