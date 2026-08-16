"""
Core business logic for resource allocation.

Conflict Detection:
  A conflict exists when two allocations for the same resource overlap in time.
  Overlap condition: existing.start < new.end AND existing.end > new.start
  The boundary case (new.start == existing.end) is NOT a conflict (back-to-back is allowed).

Alternative Selection:
  When a requested resource is unavailable or unsuitable, the system:
  1. Filters resources by the same type as requested.
  2. Filters out inactive resources.
  3. For capacity-based types (Auditorium, Laboratory), filters by capacity >= event attendance.
  4. Checks each candidate for time conflicts.
  5. Returns the first available candidate (sorted by capacity ascending to prefer smallest fit).
"""

from models import db, Resource, ResourceAllocation, ResourceRequest, ResourceRequestItem
from datetime import datetime
from sqlalchemy import and_, or_


def check_resource_conflict(resource_id: int, start_dt: datetime, end_dt: datetime, exclude_request_id: int = None) -> bool:
    """
    Returns True if the resource has an active allocation that overlaps with [start_dt, end_dt).
    Boundary sharing (new.start == existing.end) is NOT a conflict.
    """
    query = ResourceAllocation.query.filter(
        ResourceAllocation.resource_id == resource_id,
        ResourceAllocation.status == 'Allocated',
        ResourceAllocation.start_datetime < end_dt,
        ResourceAllocation.end_datetime > start_dt,
    )
    if exclude_request_id:
        query = query.filter(ResourceAllocation.request_id != exclude_request_id)
    return query.first() is not None


def get_conflicting_bookings(resource_id: int, start_dt: datetime, end_dt: datetime):
    """Returns all active allocations that conflict with the given time window."""
    return ResourceAllocation.query.filter(
        ResourceAllocation.resource_id == resource_id,
        ResourceAllocation.status == 'Allocated',
        ResourceAllocation.start_datetime < end_dt,
        ResourceAllocation.end_datetime > start_dt,
    ).all()


def find_alternative(resource_type: str, required_capacity: int, start_dt: datetime, end_dt: datetime, exclude_ids: list = None):
    """
    Finds the best alternative resource of the given type that:
    - Is active
    - Meets capacity (if applicable)
    - Is available at the requested time
    Returns a Resource object or None.
    """
    exclude_ids = exclude_ids or []

    query = Resource.query.filter(
        Resource.resource_type == resource_type,
        Resource.is_active == True,
        Resource.id.notin_(exclude_ids)
    )

    # For capacity-based types, filter by minimum capacity
    if resource_type in Resource.CAPACITY_TYPES and required_capacity:
        query = query.filter(Resource.capacity >= required_capacity)

    candidates = query.order_by(
        Resource.capacity.asc().nullsfirst() if resource_type in Resource.CAPACITY_TYPES else Resource.name.asc()
    ).all()

    for candidate in candidates:
        if not check_resource_conflict(candidate.id, start_dt, end_dt):
            return candidate
    return None


def validate_request_items(items_data: list, event_attendance: int, start_dt: datetime, end_dt: datetime):
    """
    Validates each requested item and resolves which specific resource to allocate.
    items_data: list of dicts with keys: resource_type, quantity, preferred_resource_id (optional)

    Returns:
        (resolved_list, errors, alternatives_info)
        resolved_list: list of (resource, quantity) tuples ready to allocate
        errors: list of error strings
        alternatives_info: dict mapping resource_type to suggested alternative Resource
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
            errors.append(f"Unknown resource type: {rtype}.")
            continue

        if preferred_id:
            resource = Resource.query.get(preferred_id)
            if not resource:
                errors.append(f"Resource ID {preferred_id} not found.")
                alt = find_alternative(rtype, event_attendance if rtype in Resource.CAPACITY_TYPES else None, start_dt, end_dt)
                if alt:
                    alternatives[rtype] = alt
                continue

            if not resource.is_active:
                errors.append(f"'{resource.name}' is inactive and cannot be allocated.")
                alt = find_alternative(rtype, event_attendance if rtype in Resource.CAPACITY_TYPES else None, start_dt, end_dt, exclude_ids=[preferred_id])
                if alt:
                    alternatives[rtype] = alt
                continue

            if resource.resource_type != rtype:
                errors.append(f"'{resource.name}' is a {resource.resource_type}, not a {rtype}.")
                continue

            if resource.has_capacity() and resource.capacity and resource.capacity < event_attendance:
                errors.append(f"'{resource.name}' has capacity {resource.capacity}, but event attendance is {event_attendance}.")
                alt = find_alternative(rtype, event_attendance, start_dt, end_dt, exclude_ids=[preferred_id])
                if alt:
                    alternatives[rtype] = alt
                continue

            # For qty > 1, we need multiple individual resources of the same type
            if qty > 1:
                # Find qty resources of this type that are available
                allocated_resources = _find_multiple(rtype, qty, event_attendance, start_dt, end_dt)
                if len(allocated_resources) < qty:
                    errors.append(f"Only {len(allocated_resources)} {rtype}(s) available, {qty} requested.")
                    continue
                for r in allocated_resources:
                    resolved.append(r)
            else:
                if check_resource_conflict(resource.id, start_dt, end_dt):
                    errors.append(f"'{resource.name}' is already booked during the requested time.")
                    alt = find_alternative(rtype, event_attendance if rtype in Resource.CAPACITY_TYPES else None, start_dt, end_dt, exclude_ids=[preferred_id])
                    if alt:
                        alternatives[rtype] = alt
                    continue
                resolved.append(resource)
        else:
            # No preference — auto-assign
            if qty > 1:
                allocated_resources = _find_multiple(rtype, qty, event_attendance, start_dt, end_dt)
                if len(allocated_resources) < qty:
                    errors.append(f"Only {len(allocated_resources)} {rtype}(s) available, {qty} requested.")
                    continue
                for r in allocated_resources:
                    resolved.append(r)
            else:
                resource = find_alternative(rtype, event_attendance if rtype in Resource.CAPACITY_TYPES else None, start_dt, end_dt)
                if not resource:
                    errors.append(f"No available {rtype} found for the requested time.")
                    continue
                resolved.append(resource)

    return resolved, errors, alternatives


def _find_multiple(rtype: str, qty: int, event_attendance: int, start_dt: datetime, end_dt: datetime) -> list:
    """Find multiple individual resources of a type that are all available."""
    query = Resource.query.filter(
        Resource.resource_type == rtype,
        Resource.is_active == True
    )
    if rtype in Resource.CAPACITY_TYPES and event_attendance:
        query = query.filter(Resource.capacity >= event_attendance)

    candidates = query.all()
    found = []
    for c in candidates:
        if not check_resource_conflict(c.id, start_dt, end_dt):
            found.append(c)
        if len(found) == qty:
            break
    return found


def process_allocation(request: ResourceRequest):
    """
    Approves and allocates all resources in the request atomically.
    If ANY resource fails, the entire transaction is rolled back.

    Returns: (success: bool, message: str)
    """
    try:
        for item in request.items:
            rtype = item.resource_type
            qty = item.quantity
            preferred_id = item.preferred_resource_id

            if preferred_id:
                resources_to_alloc = [Resource.query.get(preferred_id)]
            else:
                resources_to_alloc = _find_multiple(rtype, qty,
                    request.event.expected_attendance if rtype in Resource.CAPACITY_TYPES else 0,
                    request.start_datetime, request.end_datetime)

            if len(resources_to_alloc) < qty or None in resources_to_alloc:
                db.session.rollback()
                return False, f"Could not allocate {qty} {rtype}(s). Allocation aborted — no resources were allocated."

            for res in resources_to_alloc:
                # Double-check conflict right before allocating (within transaction)
                if check_resource_conflict(res.id, request.start_datetime, request.end_datetime):
                    db.session.rollback()
                    return False, f"'{res.name}' was just booked by another request. Allocation aborted."

                allocation = ResourceAllocation(
                    request_id=request.id,
                    resource_id=res.id,
                    start_datetime=request.start_datetime,
                    end_datetime=request.end_datetime,
                    status='Allocated'
                )
                db.session.add(allocation)

        request.status = 'Approved'
        db.session.commit()
        return True, "All resources allocated successfully."

    except Exception as e:
        db.session.rollback()
        return False, f"Allocation failed due to an internal error: {str(e)}"


def cancel_allocation(request: ResourceRequest):
    """Cancels a request and releases all its allocated resources."""
    try:
        for alloc in request.allocations:
            alloc.status = 'Cancelled'
        request.status = 'Cancelled'
        db.session.commit()
        return True, "Request cancelled and resources released."
    except Exception as e:
        db.session.rollback()
        return False, str(e)


def get_resource_availability(resource_id: int, date: datetime.date):
    """Returns all allocations for a resource on a given date."""
    from datetime import datetime, timedelta
    day_start = datetime.combine(date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    return ResourceAllocation.query.filter(
        ResourceAllocation.resource_id == resource_id,
        ResourceAllocation.status == 'Allocated',
        ResourceAllocation.start_datetime < day_end,
        ResourceAllocation.end_datetime > day_start
    ).all()
