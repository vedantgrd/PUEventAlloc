"""
Core business logic for resource allocation.

Conflict Detection
==================
A conflict exists when two allocations for the same resource overlap in time.
Canonical formula:
    existing.start_datetime < requested.end_datetime
    AND
    existing.end_datetime   > requested.start_datetime

This correctly handles ALL overlap shapes:
  - Partial overlap     : existing 10-14, new 12-16  → CONFLICT
  - Exact same interval : existing 10-14, new 10-14  → CONFLICT
  - Contained           : existing 10-14, new 11-12  → CONFLICT
  - Containing          : existing 10-14, new 08-16  → CONFLICT
  - Back-to-back (end=start) : existing 10-14, new 14-16 → ALLOWED
  - Before              : existing 10-14, new 08-10  → ALLOWED

Only 'Allocated' allocations block resources. Cancelled/Rejected ones are ignored.

Alternative Selection
=====================
When a preferred resource is unavailable/unsuitable, find_alternative():
  1. Filters by same resource type
  2. Filters out inactive resources
  3. For capacity types (Auditorium, Laboratory): capacity >= required_capacity
  4. Checks each candidate for time conflicts
  5. Orders by capacity ascending (smallest sufficient room preferred) or name for equipment
  6. Returns the first conflict-free candidate, or None

Atomic Allocation (process_allocation)
=======================================
All-or-nothing: if any resource in the request fails at approval time,
the entire db.session is rolled back and zero allocations are created.
Approval-time revalidation checks: active status, type match, capacity, conflict.
"""

import logging
from datetime import datetime
from models import db, Resource, ResourceAllocation, ResourceRequest, ResourceRequestItem

log = logging.getLogger(__name__)


# ─── Conflict Detection ───────────────────────────────────────────────────────

def check_resource_conflict(
    resource_id: int,
    start_dt: datetime,
    end_dt: datetime,
    exclude_request_id: int = None,
) -> bool:
    """Return True if resource has an active allocation overlapping [start_dt, end_dt)."""
    query = ResourceAllocation.query.filter(
        ResourceAllocation.resource_id == resource_id,
        ResourceAllocation.status == 'Allocated',
        ResourceAllocation.start_datetime < end_dt,   # existing starts before new ends
        ResourceAllocation.end_datetime > start_dt,   # existing ends after new starts
    )
    if exclude_request_id:
        query = query.filter(ResourceAllocation.request_id != exclude_request_id)
    return query.first() is not None


def get_conflicting_bookings(
    resource_id: int,
    start_dt: datetime,
    end_dt: datetime,
) -> list:
    """Return all active allocations conflicting with the given window."""
    return ResourceAllocation.query.filter(
        ResourceAllocation.resource_id == resource_id,
        ResourceAllocation.status == 'Allocated',
        ResourceAllocation.start_datetime < end_dt,
        ResourceAllocation.end_datetime > start_dt,
    ).all()


# ─── Alternative Selection ────────────────────────────────────────────────────

def find_alternative(
    resource_type: str,
    required_capacity: int | None,
    start_dt: datetime,
    end_dt: datetime,
    exclude_ids: list = None,
) -> Resource | None:
    """
    Return the best available alternative Resource, or None.

    Selection priority: active → same type → sufficient capacity → no conflict →
    smallest sufficient capacity (for venues) / alphabetical (for equipment).
    """
    exclude_ids = exclude_ids or []

    query = Resource.query.filter(
        Resource.resource_type == resource_type,
        Resource.is_active == True,          # noqa: E712
        Resource.id.notin_(exclude_ids),
    )

    if resource_type in Resource.CAPACITY_TYPES and required_capacity:
        query = query.filter(Resource.capacity >= required_capacity)

    order = (
        Resource.capacity.asc().nullsfirst()
        if resource_type in Resource.CAPACITY_TYPES
        else Resource.name.asc()
    )
    candidates = query.order_by(order).all()

    for candidate in candidates:
        if not check_resource_conflict(candidate.id, start_dt, end_dt):
            return candidate
    return None


# ─── Request Validation ───────────────────────────────────────────────────────

def validate_request_items(
    items_data: list,
    event_attendance: int,
    start_dt: datetime,
    end_dt: datetime,
) -> tuple[list, list, dict]:
    """
    Pre-flight check when a request is SUBMITTED (not approved).

    Returns:
        resolved   : list[Resource] ready to allocate
        errors     : list[str] human-readable error messages
        alternatives : dict[resource_type, Resource] suggestions for failed items
    """
    resolved = []
    errors = []
    alternatives = {}

    for item in items_data:
        rtype = item['resource_type']
        qty = item.get('quantity', 1)
        preferred_id = item.get('preferred_resource_id')

        if qty < 1:
            errors.append(f"Quantity for {rtype} must be at least 1.")
            continue

        if rtype not in Resource.RESOURCE_TYPES:
            errors.append(f"Unknown resource type: '{rtype}'.")
            continue

        req_cap = event_attendance if rtype in Resource.CAPACITY_TYPES else None

        if preferred_id:
            resource = db.session.get(Resource, preferred_id)
            if not resource:
                errors.append(f"Resource ID {preferred_id} does not exist.")
                _suggest_alt(alternatives, rtype, req_cap, start_dt, end_dt, [preferred_id])
                continue

            if resource.resource_type != rtype:
                errors.append(
                    f"'{resource.name}' is a {resource.resource_type}, not a {rtype}. "
                    f"Resource type must match the requested type."
                )
                continue

            if not resource.is_active:
                errors.append(f"'{resource.name}' is inactive and cannot be allocated.")
                _suggest_alt(alternatives, rtype, req_cap, start_dt, end_dt, [preferred_id])
                continue

            if resource.has_capacity() and resource.capacity is not None:
                if resource.capacity < event_attendance:
                    errors.append(
                        f"'{resource.name}' has capacity {resource.capacity}, "
                        f"but event attendance is {event_attendance}."
                    )
                    _suggest_alt(alternatives, rtype, req_cap, start_dt, end_dt, [preferred_id])
                    continue

            if qty > 1:
                found = _find_multiple(rtype, qty, event_attendance, start_dt, end_dt)
                if len(found) < qty:
                    errors.append(
                        f"Only {len(found)} {rtype}(s) available at that time; {qty} requested."
                    )
                    continue
                resolved.extend(found)
            else:
                if check_resource_conflict(resource.id, start_dt, end_dt):
                    conflicts = get_conflicting_bookings(resource.id, start_dt, end_dt)
                    conflict_info = _format_conflict(conflicts)
                    errors.append(
                        f"'{resource.name}' is already booked {conflict_info}. "
                        f"Cannot allocate for the requested time."
                    )
                    _suggest_alt(alternatives, rtype, req_cap, start_dt, end_dt, [preferred_id])
                    continue
                resolved.append(resource)

        else:
            # Auto-assign best match
            if qty > 1:
                found = _find_multiple(rtype, qty, event_attendance, start_dt, end_dt)
                if len(found) < qty:
                    errors.append(
                        f"Only {len(found)} {rtype}(s) available at that time; {qty} requested."
                    )
                    continue
                resolved.extend(found)
            else:
                resource = find_alternative(rtype, req_cap, start_dt, end_dt)
                if not resource:
                    errors.append(
                        f"No active {rtype} available for the requested time. "
                        f"All {rtype}s are either inactive, at capacity, or already booked."
                    )
                    continue
                resolved.append(resource)

    return resolved, errors, alternatives


def _suggest_alt(alternatives, rtype, req_cap, start_dt, end_dt, exclude_ids):
    alt = find_alternative(rtype, req_cap, start_dt, end_dt, exclude_ids=exclude_ids)
    if alt:
        alternatives[rtype] = alt


def _format_conflict(conflicts: list) -> str:
    if not conflicts:
        return "at the requested time"
    c = conflicts[0]
    return (
        f"from {c.start_datetime.strftime('%I:%M %p')} "
        f"to {c.end_datetime.strftime('%I:%M %p')} "
        f"(Request #{c.request_id})"
    )


def _find_multiple(
    rtype: str,
    qty: int,
    event_attendance: int,
    start_dt: datetime,
    end_dt: datetime,
) -> list:
    """Find up to `qty` individual available resources of the given type."""
    query = Resource.query.filter(
        Resource.resource_type == rtype,
        Resource.is_active == True,  # noqa: E712
    )
    if rtype in Resource.CAPACITY_TYPES and event_attendance:
        query = query.filter(Resource.capacity >= event_attendance)

    found = []
    for c in query.all():
        if not check_resource_conflict(c.id, start_dt, end_dt):
            found.append(c)
        if len(found) == qty:
            break
    return found


# ─── Approval & Allocation (Atomic) ──────────────────────────────────────────

def process_allocation(req: ResourceRequest) -> tuple[bool, str]:
    """
    Approve a ResourceRequest and allocate all its resources atomically.

    Full revalidation at approval time:
      - Resource still exists
      - Resource still active
      - Resource type still matches
      - Capacity still sufficient
      - No conflicts (race-condition safe)

    If ANY check fails, the entire transaction is rolled back.
    Returns (success, human-readable message).
    """
    if not req.can_transition_to('Approved'):
        return False, f"Request is already {req.status} and cannot be approved."

    try:
        allocations_to_add = []

        for item in req.items:
            rtype = item.resource_type
            qty = item.quantity
            preferred_id = item.preferred_resource_id
            req_cap = (
                req.event.expected_attendance
                if rtype in Resource.CAPACITY_TYPES else 0
            )

            # Resolve resources for this item
            if preferred_id:
                resource = db.session.get(Resource, preferred_id)
                resources_to_alloc = [resource] if resource else []
            else:
                resources_to_alloc = _find_multiple(
                    rtype, qty, req_cap,
                    req.start_datetime, req.end_datetime,
                )

            # Validate count
            if len(resources_to_alloc) < qty or None in resources_to_alloc:
                db.session.rollback()
                return False, (
                    f"Cannot allocate {qty} {rtype}(s) — not enough resources found. "
                    f"Entire request aborted; no resources were allocated."
                )

            for res in resources_to_alloc:
                # ── Approval-time revalidation ──────────────────────────────
                if not res.is_active:
                    db.session.rollback()
                    return False, (
                        f"'{res.name}' has been deactivated since this request was submitted. "
                        f"Allocation aborted."
                    )

                if res.resource_type != rtype:
                    db.session.rollback()
                    return False, (
                        f"'{res.name}' is now a {res.resource_type} (expected {rtype}). "
                        f"Allocation aborted."
                    )

                if res.has_capacity() and res.capacity is not None:
                    if res.capacity < req.event.expected_attendance:
                        db.session.rollback()
                        return False, (
                            f"'{res.name}' capacity ({res.capacity}) is insufficient for "
                            f"{req.event.expected_attendance} attendees. Allocation aborted."
                        )

                if check_resource_conflict(res.id, req.start_datetime, req.end_datetime):
                    conflicts = get_conflicting_bookings(res.id, req.start_datetime, req.end_datetime)
                    conflict_info = _format_conflict(conflicts)
                    db.session.rollback()
                    return False, (
                        f"'{res.name}' was booked {conflict_info} before this request could be approved. "
                        f"Allocation aborted."
                    )
                # ────────────────────────────────────────────────────────────

                allocations_to_add.append(ResourceAllocation(
                    request_id=req.id,
                    resource_id=res.id,
                    start_datetime=req.start_datetime,
                    end_datetime=req.end_datetime,
                    status='Allocated',
                ))

        # All checks passed — commit everything in one shot
        for alloc in allocations_to_add:
            db.session.add(alloc)
        req.status = 'Approved'
        db.session.commit()
        return True, f"All {len(allocations_to_add)} resource(s) allocated successfully."

    except Exception as exc:
        db.session.rollback()
        log.exception("Unexpected error during allocation for request %s", req.id)
        return False, "An unexpected error occurred during allocation. No resources were allocated."


# ─── Cancellation ─────────────────────────────────────────────────────────────

def cancel_allocation(req: ResourceRequest) -> tuple[bool, str]:
    """
    Cancel a request and release all its allocations.
    Allocations are marked Cancelled (not deleted) so history is preserved.
    """
    if not req.can_transition_to('Cancelled'):
        return False, f"Request is {req.status} and cannot be cancelled."

    try:
        count = 0
        for alloc in req.allocations:
            if alloc.status == 'Allocated':
                alloc.status = 'Cancelled'
                count += 1
        req.status = 'Cancelled'
        db.session.commit()
        return True, f"Request cancelled. {count} resource allocation(s) released."
    except Exception as exc:
        db.session.rollback()
        log.exception("Error cancelling request %s", req.id)
        return False, "Could not cancel the request due to an unexpected error."


# ─── Availability ─────────────────────────────────────────────────────────────

def get_resource_availability(resource_id: int, date) -> list:
    """Return all active allocations for a resource on a given date."""
    from datetime import timedelta
    day_start = datetime.combine(date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    return ResourceAllocation.query.filter(
        ResourceAllocation.resource_id == resource_id,
        ResourceAllocation.status == 'Allocated',
        ResourceAllocation.start_datetime < day_end,
        ResourceAllocation.end_datetime > day_start,
    ).all()
