import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_migrate import Migrate
from datetime import datetime
from models import db, Event, Resource, ResourceRequest, ResourceRequestItem, ResourceAllocation
from services import (
    check_resource_conflict, find_alternative, validate_request_items,
    process_allocation, cancel_allocation, get_resource_availability
)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'pillai-university-secret-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///pillai_events.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)


def parse_datetime(s):
    """Parse datetime from HTML input (format: YYYY-MM-DDTHH:MM)."""
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%dT%H:%M')
    except ValueError:
        try:
            return datetime.strptime(s, '%Y-%m-%d %H:%M')
        except ValueError:
            return None


# ─── Dashboard ───────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    total_events = Event.query.count()
    total_resources = Resource.query.count()
    active_resources = Resource.query.filter_by(is_active=True).count()
    pending_requests = ResourceRequest.query.filter_by(status='Pending').count()
    upcoming_events = Event.query.filter(
        Event.start_datetime >= datetime.utcnow(),
        Event.status.in_(['Approved', 'Pending'])
    ).order_by(Event.start_datetime).limit(5).all()
    recent_requests = ResourceRequest.query.order_by(
        ResourceRequest.created_at.desc()
    ).limit(5).all()

    status_counts = {}
    for s in Event.STATUS_CHOICES:
        status_counts[s] = Event.query.filter_by(status=s).count()

    return render_template(
        'dashboard.html',
        total_events=total_events,
        total_resources=total_resources,
        active_resources=active_resources,
        pending_requests=pending_requests,
        upcoming_events=upcoming_events,
        recent_requests=recent_requests,
        status_counts=status_counts
    )


# ─── Events ──────────────────────────────────────────────────────────────────

@app.route('/events')
def events_list():
    status_filter = request.args.get('status', '')
    date_filter = request.args.get('date', '')
    query = Event.query

    if status_filter:
        query = query.filter_by(status=status_filter)

    if date_filter:
        try:
            filter_date = datetime.strptime(date_filter, '%Y-%m-%d')
            query = query.filter(
                db.func.date(Event.start_datetime) == filter_date.date()
            )
        except ValueError:
            pass

    events = query.order_by(Event.start_datetime.desc()).all()

    return render_template(
        'events/list.html',
        events=events,
        status_filter=status_filter,
        date_filter=date_filter,
        status_choices=Event.STATUS_CHOICES
    )


@app.route('/events/create', methods=['GET', 'POST'])
def event_create():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        organizer = request.form.get('organizer', '').strip()
        attendance_str = request.form.get('expected_attendance', '').strip()
        start_str = request.form.get('start_datetime', '').strip()
        end_str = request.form.get('end_datetime', '').strip()
        status = request.form.get('status', 'Draft')
        description = request.form.get('description', '').strip()

        errors = []

        if not name:
            errors.append("Event name is required.")

        if not organizer:
            errors.append("Organizer is required.")

        if not attendance_str:
            errors.append("Expected attendance is required.")
        else:
            try:
                attendance = int(attendance_str)
                if attendance <= 0:
                    errors.append("Expected attendance must be a positive number.")
            except ValueError:
                errors.append("Expected attendance must be a valid number.")
                attendance = 0

        start_dt = parse_datetime(start_str)
        end_dt = parse_datetime(end_str)

        if not start_str:
            errors.append("Start date/time is required.")
        elif not start_dt:
            errors.append("Invalid start date/time format.")

        if not end_str:
            errors.append("End date/time is required.")
        elif not end_dt:
            errors.append("Invalid end date/time format.")

        if start_dt and end_dt and end_dt <= start_dt:
            errors.append("End date/time must be after start date/time.")

        if status not in Event.STATUS_CHOICES:
            errors.append("Invalid status.")

        if errors:
            for e in errors:
                flash(e, 'error')

            return render_template(
                'events/form.html',
                status_choices=Event.STATUS_CHOICES,
                form_data=request.form,
                action='Create'
            )

        event = Event(
            name=name,
            organizer=organizer,
            expected_attendance=int(attendance_str),
            start_datetime=start_dt,
            end_datetime=end_dt,
            status=status,
            description=description
        )

        db.session.add(event)
        db.session.commit()

        flash(
            f"Event '{event.name}' created successfully.",
            'success'
        )

        return redirect(
            url_for('event_detail', event_id=event.id)
        )

    return render_template(
        'events/form.html',
        status_choices=Event.STATUS_CHOICES,
        form_data={},
        action='Create'
    )


@app.route('/events/<int:event_id>')
def event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    return render_template('events/detail.html', event=event)


@app.route('/events/<int:event_id>/edit', methods=['GET', 'POST'])
def event_edit(event_id):
    event = Event.query.get_or_404(event_id)

    if event.status in ['Cancelled', 'Completed']:
        flash(f"Cannot edit a {event.status} event.", 'error')
        return redirect(url_for('event_detail', event_id=event.id))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        organizer = request.form.get('organizer', '').strip()
        attendance_str = request.form.get('expected_attendance', '').strip()
        start_str = request.form.get('start_datetime', '').strip()
        end_str = request.form.get('end_datetime', '').strip()
        status = request.form.get('status', event.status)
        description = request.form.get('description', '').strip()

        errors = []

        if not name:
            errors.append("Event name is required.")

        if not organizer:
            errors.append("Organizer is required.")

        attendance = event.expected_attendance

        if not attendance_str:
            errors.append("Expected attendance is required.")
        else:
            try:
                attendance = int(attendance_str)
                if attendance <= 0:
                    errors.append("Expected attendance must be a positive number.")
            except ValueError:
                errors.append("Expected attendance must be a valid number.")

        start_dt = parse_datetime(start_str)
        end_dt = parse_datetime(end_str)

        if not start_dt:
            errors.append("Invalid start date/time.")

        if not end_dt:
            errors.append("Invalid end date/time.")

        if start_dt and end_dt and end_dt <= start_dt:
            errors.append("End date/time must be after start date/time.")

        if status not in Event.STATUS_CHOICES:
            errors.append("Invalid status.")

        if errors:
            for e in errors:
                flash(e, 'error')

            return render_template(
                'events/form.html',
                event=event,
                status_choices=Event.STATUS_CHOICES,
                form_data=request.form,
                action='Edit'
            )

        event.name = name
        event.organizer = organizer
        event.expected_attendance = attendance
        event.start_datetime = start_dt
        event.end_datetime = end_dt
        event.status = status
        event.description = description

        db.session.commit()

        flash(
            f"Event '{event.name}' updated successfully.",
            'success'
        )

        return redirect(
            url_for('event_detail', event_id=event.id)
        )

    form_data = {
        'name': event.name,
        'organizer': event.organizer,
        'expected_attendance': event.expected_attendance,
        'start_datetime': event.start_datetime.strftime('%Y-%m-%dT%H:%M'),
        'end_datetime': event.end_datetime.strftime('%Y-%m-%dT%H:%M'),
        'status': event.status,
        'description': event.description or ''
    }

    return render_template(
        'events/form.html',
        event=event,
        status_choices=Event.STATUS_CHOICES,
        form_data=form_data,
        action='Edit'
    )


@app.route('/events/<int:event_id>/cancel', methods=['POST'])
def event_cancel(event_id):
    event = Event.query.get_or_404(event_id)

    if event.status == 'Cancelled':
        flash("Event is already cancelled.", 'error')
        return redirect(url_for('events_list'))

    if event.status == 'Completed':
        flash("Cannot cancel a completed event.", 'error')
        return redirect(url_for('event_detail', event_id=event.id))

    for req in event.resource_requests:
        if req.status in ['Pending', 'Approved']:
            cancel_allocation(req)

    event.status = 'Cancelled'
    db.session.commit()

    flash(
        f"Event '{event.name}' has been cancelled.",
        'success'
    )

    return redirect(url_for('events_list'))


# ─── Resources ───────────────────────────────────────────────────────────────

@app.route('/resources')
def resources_list():
    type_filter = request.args.get('type', '')
    active_filter = request.args.get('active', '')

    query = Resource.query

    if type_filter:
        query = query.filter_by(resource_type=type_filter)

    if active_filter == '1':
        query = query.filter_by(is_active=True)
    elif active_filter == '0':
        query = query.filter_by(is_active=False)

    resources = query.order_by(
        Resource.resource_type,
        Resource.name
    ).all()

    return render_template(
        'resources/list.html',
        resources=resources,
        type_filter=type_filter,
        active_filter=active_filter,
        resource_types=Resource.RESOURCE_TYPES
    )


@app.route('/resources/create', methods=['GET', 'POST'])
def resource_create():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        rtype = request.form.get('resource_type', '').strip()
        capacity_str = request.form.get('capacity', '').strip()
        is_active = request.form.get('is_active') == 'on'
        description = request.form.get('description', '').strip()

        errors = []

        if not name:
            errors.append("Resource name is required.")

        if not rtype:
            errors.append("Resource type is required.")
        elif rtype not in Resource.RESOURCE_TYPES:
            errors.append(
                f"Invalid resource type. Choose from: "
                f"{', '.join(Resource.RESOURCE_TYPES)}."
            )

        capacity = None

        if rtype in Resource.CAPACITY_TYPES:
            if not capacity_str:
                errors.append(f"Capacity is required for {rtype}.")
            else:
                try:
                    capacity = int(capacity_str)
                    if capacity <= 0:
                        errors.append("Capacity must be a positive number.")
                except ValueError:
                    errors.append("Capacity must be a valid number.")

        if errors:
            for e in errors:
                flash(e, 'error')

            return render_template(
                'resources/form.html',
                resource_types=Resource.RESOURCE_TYPES,
                capacity_types=Resource.CAPACITY_TYPES,
                form_data=request.form,
                action='Add'
            )

        resource = Resource(
            name=name,
            resource_type=rtype,
            capacity=capacity,
            is_active=is_active,
            description=description
        )

        db.session.add(resource)
        db.session.commit()

        flash(
            f"Resource '{resource.name}' added successfully.",
            'success'
        )

        return redirect(url_for('resources_list'))

    return render_template(
        'resources/form.html',
        resource_types=Resource.RESOURCE_TYPES,
        capacity_types=Resource.CAPACITY_TYPES,
        form_data={},
        action='Add'
    )


@app.route('/resources/<int:resource_id>/edit', methods=['GET', 'POST'])
def resource_edit(resource_id):
    resource = Resource.query.get_or_404(resource_id)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        rtype = request.form.get('resource_type', '').strip()
        capacity_str = request.form.get('capacity', '').strip()
        is_active = request.form.get('is_active') == 'on'
        description = request.form.get('description', '').strip()

        errors = []

        if not name:
            errors.append("Resource name is required.")

        if not rtype or rtype not in Resource.RESOURCE_TYPES:
            errors.append("Valid resource type is required.")

        capacity = None

        if rtype in Resource.CAPACITY_TYPES:
            if not capacity_str:
                errors.append("Capacity is required for this resource type.")
            else:
                try:
                    capacity = int(capacity_str)
                    if capacity <= 0:
                        errors.append("Capacity must be positive.")
                except ValueError:
                    errors.append("Capacity must be a number.")

        if errors:
            for e in errors:
                flash(e, 'error')

            return render_template(
                'resources/form.html',
                resource=resource,
                resource_types=Resource.RESOURCE_TYPES,
                capacity_types=Resource.CAPACITY_TYPES,
                form_data=request.form,
                action='Edit'
            )

        resource.name = name
        resource.resource_type = rtype
        resource.capacity = capacity
        resource.is_active = is_active
        resource.description = description

        db.session.commit()

        flash(
            f"Resource '{resource.name}' updated.",
            'success'
        )

        return redirect(url_for('resources_list'))

    form_data = {
        'name': resource.name,
        'resource_type': resource.resource_type,
        'capacity': resource.capacity or '',
        'is_active': resource.is_active,
        'description': resource.description or ''
    }

    return render_template(
        'resources/form.html',
        resource=resource,
        resource_types=Resource.RESOURCE_TYPES,
        capacity_types=Resource.CAPACITY_TYPES,
        form_data=form_data,
        action='Edit'
    )


@app.route('/resources/<int:resource_id>/toggle', methods=['POST'])
def resource_toggle(resource_id):
    resource = Resource.query.get_or_404(resource_id)

    resource.is_active = not resource.is_active

    db.session.commit()

    state = 'activated' if resource.is_active else 'deactivated'

    flash(
        f"Resource '{resource.name}' has been {state}.",
        'success'
    )

    return redirect(url_for('resources_list'))


@app.route('/resources/availability')
def resource_availability():
    date_str = request.args.get(
        'date',
        datetime.utcnow().strftime('%Y-%m-%d')
    )

    resource_id = request.args.get(
        'resource_id',
        type=int
    )

    try:
        selected_date = datetime.strptime(
            date_str,
            '%Y-%m-%d'
        ).date()
    except ValueError:
        selected_date = datetime.utcnow().date()

    resources = Resource.query.filter_by(
        is_active=True
    ).order_by(
        Resource.resource_type,
        Resource.name
    ).all()

    availability_data = {}

    for r in resources:
        allocations = get_resource_availability(
            r.id,
            selected_date
        )
        availability_data[r.id] = allocations

    return render_template(
        'resources/availability.html',
        resources=resources,
        availability_data=availability_data,
        selected_date=selected_date,
        date_str=date_str
    )


# ─── Resource Requests ───────────────────────────────────────────────────────

@app.route('/requests')
def requests_list():
    status_filter = request.args.get('status', '')

    query = ResourceRequest.query

    if status_filter:
        query = query.filter_by(status=status_filter)

    reqs = query.order_by(
        ResourceRequest.created_at.desc()
    ).all()

    return render_template(
        'requests/list.html',
        requests=reqs,
        status_filter=status_filter,
        status_choices=ResourceRequest.STATUS_CHOICES
    )


@app.route('/requests/create', methods=['GET', 'POST'])
def request_create():
    events = Event.query.filter(
        Event.status.in_(['Draft', 'Pending', 'Approved'])
    ).order_by(Event.name).all()

    resources = Resource.query.filter_by(
        is_active=True
    ).order_by(
        Resource.resource_type,
        Resource.name
    ).all()

    # Convert SQLAlchemy Resource objects to dictionaries
    # so they can safely be passed through Jinja's tojson filter.
    resources_json = [
        r.to_dict()
        for r in resources
    ]

    if request.method == 'POST':
        event_id = request.form.get(
            'event_id',
            type=int
        )

        start_str = request.form.get(
            'start_datetime',
            ''
        ).strip()

        end_str = request.form.get(
            'end_datetime',
            ''
        ).strip()

        notes = request.form.get(
            'notes',
            ''
        ).strip()

        # Parse resource items from form
        item_types = request.form.getlist(
            'item_type[]'
        )

        item_qtys = request.form.getlist(
            'item_qty[]'
        )

        item_prefs = request.form.getlist(
            'item_preferred[]'
        )

        errors = []
        event = None

        if not event_id:
            errors.append("Please select an event.")
        else:
            event = Event.query.get(event_id)

            if not event:
                errors.append(
                    "Selected event does not exist."
                )

            elif event.status in ['Cancelled', 'Rejected']:
                errors.append(
                    f"Cannot request resources for a "
                    f"{event.status} event."
                )

        start_dt = parse_datetime(start_str)
        end_dt = parse_datetime(end_str)

        if not start_dt:
            errors.append(
                "Valid start date/time is required."
            )

        if not end_dt:
            errors.append(
                "Valid end date/time is required."
            )

        if start_dt and end_dt and end_dt <= start_dt:
            errors.append(
                "End time must be after start time."
            )

        if not item_types or all(
            t == '' for t in item_types
        ):
            errors.append(
                "Please add at least one resource to request."
            )

        items_data = []

        if not errors:
            for i, rtype in enumerate(item_types):
                if not rtype:
                    continue

                qty_str = (
                    item_qtys[i]
                    if i < len(item_qtys)
                    else '1'
                )

                pref_str = (
                    item_prefs[i]
                    if i < len(item_prefs)
                    else ''
                )

                try:
                    qty = int(qty_str) if qty_str else 1
                except ValueError:
                    qty = 1

                pref_id = (
                    int(pref_str)
                    if pref_str and pref_str.isdigit()
                    else None
                )

                items_data.append({
                    'resource_type': rtype,
                    'quantity': qty,
                    'preferred_resource_id': pref_id
                })

        if not errors and items_data:
            resolved, item_errors, alternatives = validate_request_items(
                items_data,
                event.expected_attendance,
                start_dt,
                end_dt
            )

            if item_errors:
                for e in item_errors:
                    flash(e, 'error')

                if alternatives:
                    for rtype, alt in alternatives.items():
                        cap_info = (
                            f" (Capacity: {alt.capacity})"
                            if alt.capacity
                            else ""
                        )

                        flash(
                            f"💡 Alternative for {rtype}: "
                            f"{alt.name}{cap_info} is available.",
                            'info'
                        )

                return render_template(
                    'requests/form.html',
                    events=events,
                    resources=resources,
                    resources_json=resources_json,
                    resource_types=Resource.RESOURCE_TYPES,
                    capacity_types=Resource.CAPACITY_TYPES,
                    form_data=request.form,
                    action='Create'
                )

        if errors:
            for e in errors:
                flash(e, 'error')

            return render_template(
                'requests/form.html',
                events=events,
                resources=resources,
                resources_json=resources_json,
                resource_types=Resource.RESOURCE_TYPES,
                capacity_types=Resource.CAPACITY_TYPES,
                form_data=request.form,
                action='Create'
            )

        # Create request
        req = ResourceRequest(
            event_id=event_id,
            start_datetime=start_dt,
            end_datetime=end_dt,
            status='Pending',
            notes=notes
        )

        db.session.add(req)

        # Get req.id
        db.session.flush()

        for item_d in items_data:
            ri = ResourceRequestItem(
                request_id=req.id,
                resource_type=item_d['resource_type'],
                quantity=item_d['quantity'],
                preferred_resource_id=item_d['preferred_resource_id']
            )

            db.session.add(ri)

        db.session.commit()

        flash(
            "Resource request submitted successfully.",
            'success'
        )

        return redirect(
            url_for(
                'request_detail',
                request_id=req.id
            )
        )

    return render_template(
        'requests/form.html',
        events=events,
        resources=resources,
        resources_json=resources_json,
        resource_types=Resource.RESOURCE_TYPES,
        capacity_types=Resource.CAPACITY_TYPES,
        form_data={},
        action='Create'
    )


@app.route('/requests/<int:request_id>')
def request_detail(request_id):
    req = ResourceRequest.query.get_or_404(request_id)

    alternatives = {}

    if req.status == 'Pending':
        for item in req.items:
            pref_id = item.preferred_resource_id

            att = (
                req.event.expected_attendance
                if item.resource_type in Resource.CAPACITY_TYPES
                else None
            )

            alt = find_alternative(
                item.resource_type,
                att,
                req.start_datetime,
                req.end_datetime,
                exclude_ids=[pref_id] if pref_id else []
            )

            if alt:
                alternatives[item.id] = alt

    return render_template(
        'requests/detail.html',
        req=req,
        alternatives=alternatives
    )


@app.route('/requests/<int:request_id>/approve', methods=['POST'])
def request_approve(request_id):
    req = ResourceRequest.query.get_or_404(request_id)

    if req.status != 'Pending':
        flash(
            f"Request is already {req.status}.",
            'error'
        )

        return redirect(
            url_for(
                'request_detail',
                request_id=req.id
            )
        )

    success, message = process_allocation(req)

    if success:
        flash(message, 'success')
    else:
        flash(message, 'error')

    return redirect(
        url_for(
            'request_detail',
            request_id=req.id
        )
    )


@app.route('/requests/<int:request_id>/reject', methods=['POST'])
def request_reject(request_id):
    req = ResourceRequest.query.get_or_404(request_id)

    if req.status != 'Pending':
        flash(
            f"Request is already {req.status}.",
            'error'
        )

        return redirect(
            url_for(
                'request_detail',
                request_id=req.id
            )
        )

    reason = request.form.get(
        'rejection_reason',
        ''
    ).strip()

    req.status = 'Rejected'
    req.rejection_reason = reason

    db.session.commit()

    flash(
        "Request has been rejected.",
        'success'
    )

    return redirect(
        url_for(
            'request_detail',
            request_id=req.id
        )
    )


@app.route('/requests/<int:request_id>/cancel', methods=['POST'])
def request_cancel(request_id):
    req = ResourceRequest.query.get_or_404(request_id)

    if req.status not in ['Pending', 'Approved']:
        flash(
            f"Cannot cancel a {req.status} request.",
            'error'
        )

        return redirect(
            url_for(
                'request_detail',
                request_id=req.id
            )
        )

    success, message = cancel_allocation(req)

    if success:
        flash(message, 'success')
    else:
        flash(message, 'error')

    return redirect(
        url_for(
            'request_detail',
            request_id=req.id
        )
    )


# ─── API endpoint for dynamic form ───────────────────────────────────────────

@app.route('/api/resources-by-type')
def api_resources_by_type():
    rtype = request.args.get('type', '')

    resources = Resource.query.filter_by(
        resource_type=rtype,
        is_active=True
    ).order_by(
        Resource.name
    ).all()

    return jsonify([
        r.to_dict()
        for r in resources
    ])


# ─── Error Handlers ──────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('errors/500.html'), 500


# ─── DB Init ─────────────────────────────────────────────────────────────────

@app.cli.command('init-db')
def init_db():
    """Initialize the database with tables and seed data."""
    db.create_all()
    _seed_data()
    print("Database initialized and seeded.")


def _seed_data():
    """Seed initial resources and a sample event."""

    if Resource.query.count() > 0:
        return

    resources = [
        Resource(
            name='Main Auditorium',
            resource_type='Auditorium',
            capacity=500,
            is_active=True,
            description='Main campus auditorium'
        ),
        Resource(
            name='Mini Auditorium',
            resource_type='Auditorium',
            capacity=200,
            is_active=True,
            description='Smaller auditorium for departmental events'
        ),
        Resource(
            name='Seminar Hall A',
            resource_type='Auditorium',
            capacity=100,
            is_active=True,
            description='Seminar hall in Block A'
        ),
        Resource(
            name='Computer Lab 1',
            resource_type='Laboratory',
            capacity=60,
            is_active=True,
            description='CS Department – 60 workstations'
        ),
        Resource(
            name='Computer Lab 2',
            resource_type='Laboratory',
            capacity=40,
            is_active=True,
            description='IT Department – 40 workstations'
        ),
        Resource(
            name='Science Lab',
            resource_type='Laboratory',
            capacity=30,
            is_active=True,
            description='Physics/Chemistry lab'
        ),
        Resource(
            name='Projector P1',
            resource_type='Projector',
            is_active=True,
            description='4K laser projector'
        ),
        Resource(
            name='Projector P2',
            resource_type='Projector',
            is_active=True,
            description='Full HD projector'
        ),
        Resource(
            name='Projector P3',
            resource_type='Projector',
            is_active=True,
            description='Portable projector'
        ),
        Resource(
            name='Wireless Mic 1',
            resource_type='Microphone',
            is_active=True,
            description='Sennheiser handheld'
        ),
        Resource(
            name='Wireless Mic 2',
            resource_type='Microphone',
            is_active=True,
            description='Sennheiser handheld'
        ),
        Resource(
            name='Lapel Mic 1',
            resource_type='Microphone',
            is_active=True,
            description='Clip-on lapel mic'
        ),
        Resource(
            name='DSLR Camera 1',
            resource_type='Camera',
            is_active=True,
            description='Canon EOS R5'
        ),
        Resource(
            name='Video Camera',
            resource_type='Camera',
            is_active=True,
            description='Sony professional video camera'
        ),
        Resource(
            name='Desktop PC Lab 1',
            resource_type='Computer',
            capacity=30,
            is_active=True,
            description='30 desktop workstations'
        ),
    ]

    db.session.add_all(resources)
    db.session.commit()

    print(f"Seeded {len(resources)} resources.")


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _seed_data()

    app.run(debug=True, port=5000)