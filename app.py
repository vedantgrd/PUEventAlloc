import os
import logging
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, abort, g
)
from flask_migrate import Migrate
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from dotenv import load_dotenv

load_dotenv()

from models import db, User, Event, Resource, ResourceRequest, ResourceRequestItem, ResourceAllocation
from services import (
    check_resource_conflict, find_alternative, validate_request_items,
    process_allocation, cancel_allocation, get_resource_availability
)

# ─── App Setup ────────────────────────────────────────────────────────────────

def create_app(config_overrides: dict = None):
    app = Flask(__name__)

    secret = os.environ.get('SECRET_KEY', '')
    if not secret:
        if os.environ.get('FLASK_ENV') == 'production':
            raise RuntimeError("SECRET_KEY must be set in production.")
        secret = 'dev-only-insecure-key-change-me'
        app.logger.warning("Using insecure dev SECRET_KEY. Set SECRET_KEY env var for production.")

    app.config['SECRET_KEY'] = secret
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
        'DATABASE_URL', 'sqlite:///pillai_events.db'
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', '0') == '1'

    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)
    Migrate(app, db)

    login_manager = LoginManager(app)
    login_manager.login_view = 'auth_login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'error'

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    logging.basicConfig(level=logging.INFO)

    _register_routes(app)
    _register_error_handlers(app)

    return app


# ─── Auth Decorators ──────────────────────────────────────────────────────────

def admin_required(f):
    """Route decorator: requires Admin role."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def owner_or_admin(get_event_fn):
    """
    Decorator factory for routes where an organizer may only touch their own events.
    get_event_fn(kwargs) -> Event
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            event = get_event_fn(kwargs)
            if not current_user.is_admin and event.owner_id != current_user.id:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


# ─── Helpers ─────────────────────────────────────────────────────────────────

def parse_datetime(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _validate_event_form(form, existing_event=None):
    """Validate event form fields. Returns (data_dict, errors_list)."""
    name = form.get('name', '').strip()
    organizer = form.get('organizer', '').strip()
    attendance_str = form.get('expected_attendance', '').strip()
    start_str = form.get('start_datetime', '').strip()
    end_str = form.get('end_datetime', '').strip()
    status = form.get('status', 'Draft')
    description = form.get('description', '').strip()

    errors = []
    attendance = 0

    if not name:
        errors.append("Event name is required.")
    if not organizer:
        errors.append("Organizer name is required.")
    if not attendance_str:
        errors.append("Expected attendance is required.")
    else:
        try:
            attendance = int(attendance_str)
            if attendance <= 0:
                errors.append("Expected attendance must be a positive number.")
        except ValueError:
            errors.append("Expected attendance must be a valid whole number.")

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
        errors.append(f"Invalid status '{status}'.")
    elif existing_event and status != existing_event.status:
        if not existing_event.can_transition_to(status):
            errors.append(
                f"Cannot change status from '{existing_event.status}' to '{status}'. "
                f"Allowed next states: {Event.VALID_TRANSITIONS.get(existing_event.status, [])}."
            )

    return {
        'name': name, 'organizer': organizer, 'attendance': attendance,
        'start_dt': start_dt, 'end_dt': end_dt,
        'status': status, 'description': description,
    }, errors


def _register_routes(app):

    # ── Auth ─────────────────────────────────────────────────────────────────

    @app.route('/login', methods=['GET', 'POST'])
    def auth_login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                login_user(user, remember=request.form.get('remember') == 'on')
                flash(f"Welcome back, {user.name}!", 'success')
                return redirect(request.args.get('next') or url_for('dashboard'))
            flash("Invalid username or password.", 'error')
        return render_template('auth/login.html')

    @app.route('/logout')
    @login_required
    def auth_logout():
        logout_user()
        flash("You have been logged out.", 'success')
        return redirect(url_for('auth_login'))

    # ── Dashboard ─────────────────────────────────────────────────────────────

    @app.route('/')
    @login_required
    def dashboard():
        # ── Scoped event count ────────────────────────────────────────────────
        event_q = Event.query if current_user.is_admin else Event.query.filter_by(owner_id=current_user.id)
        total_events = event_q.count()

        # ── Resource stats (admin only; organizer doesn't manage resources) ──
        total_resources  = Resource.query.count() if current_user.is_admin else 0
        active_resources = Resource.query.filter_by(is_active=True).count() if current_user.is_admin else 0

        # ── Request stats ─────────────────────────────────────────────────────
        req_q = ResourceRequest.query if current_user.is_admin else \
                ResourceRequest.query.filter_by(requester_id=current_user.id)

        pending_requests  = req_q.filter_by(status='Pending').count()
        approved_requests = req_q.filter_by(status='Approved').count()
        rejected_requests = req_q.filter_by(status='Rejected').count()
        total_requests    = req_q.count()

        # ── Pending request list (admin: all pending; organizer: own pending) ─
        pending_q = ResourceRequest.query.filter_by(status='Pending') if current_user.is_admin else \
                    ResourceRequest.query.filter_by(status='Pending', requester_id=current_user.id)
        pending_request_list = pending_q.order_by(ResourceRequest.created_at.asc()).limit(8).all()

        # ── Approved requests list (organizer dashboard) ──────────────────────
        approved_request_list = (
            ResourceRequest.query
            .filter_by(status='Approved', requester_id=current_user.id)
            .order_by(ResourceRequest.created_at.desc())
            .limit(5).all()
        ) if not current_user.is_admin else []

        # ── Upcoming events ───────────────────────────────────────────────────
        upcoming_q = Event.query.filter(
            Event.start_datetime >= datetime.utcnow(),
            Event.status.in_(['Approved', 'Pending'])
        )
        if not current_user.is_admin:
            upcoming_q = upcoming_q.filter_by(owner_id=current_user.id)
        upcoming_events = upcoming_q.order_by(Event.start_datetime).limit(5).all()

        # ── Event status breakdown (admin only) ───────────────────────────────
        status_counts = {s: Event.query.filter_by(status=s).count() for s in Event.STATUS_CHOICES} \
                        if current_user.is_admin else {}

        return render_template('dashboard.html',
            total_events=total_events,
            total_resources=total_resources,
            active_resources=active_resources,
            pending_requests=pending_requests,
            approved_requests=approved_requests,
            rejected_requests=rejected_requests,
            total_requests=total_requests,
            pending_request_list=pending_request_list,
            approved_request_list=approved_request_list,
            upcoming_events=upcoming_events,
            status_counts=status_counts,
        )

    # ── Events ────────────────────────────────────────────────────────────────

    @app.route('/events')
    @login_required
    def events_list():
        status_filter = request.args.get('status', '')
        date_filter = request.args.get('date', '')
        q = Event.query
        if not current_user.is_admin:
            q = q.filter_by(owner_id=current_user.id)
        if status_filter:
            q = q.filter_by(status=status_filter)
        if date_filter:
            try:
                fd = datetime.strptime(date_filter, '%Y-%m-%d')
                q = q.filter(db.func.date(Event.start_datetime) == fd.date())
            except ValueError:
                pass
        events = q.order_by(Event.start_datetime.desc()).all()
        return render_template('events/list.html', events=events,
            status_filter=status_filter, date_filter=date_filter,
            status_choices=Event.STATUS_CHOICES)

    @app.route('/events/create', methods=['GET', 'POST'])
    @login_required
    def event_create():
        if request.method == 'POST':
            data, errors = _validate_event_form(request.form)
            if errors:
                for e in errors:
                    flash(e, 'error')
                return render_template('events/form.html',
                    status_choices=Event.STATUS_CHOICES, form_data=request.form, action='Create')

            event = Event(
                name=data['name'], organizer=data['organizer'],
                expected_attendance=data['attendance'],
                start_datetime=data['start_dt'], end_datetime=data['end_dt'],
                status=data['status'], description=data['description'],
                owner_id=current_user.id,
            )
            db.session.add(event)
            db.session.commit()
            flash(f"Event '{event.name}' created successfully.", 'success')
            return redirect(url_for('event_detail', event_id=event.id))

        return render_template('events/form.html',
            status_choices=Event.STATUS_CHOICES, form_data={}, action='Create')

    @app.route('/events/<int:event_id>')
    @login_required
    def event_detail(event_id):
        event = Event.query.get_or_404(event_id)
        if not current_user.is_admin and event.owner_id != current_user.id:
            abort(403)
        return render_template('events/detail.html', event=event)

    @app.route('/events/<int:event_id>/edit', methods=['GET', 'POST'])
    @login_required
    def event_edit(event_id):
        event = Event.query.get_or_404(event_id)
        if not current_user.is_admin and event.owner_id != current_user.id:
            abort(403)
        if event.status in ['Cancelled', 'Completed']:
            flash(f"Cannot edit a {event.status} event.", 'error')
            return redirect(url_for('event_detail', event_id=event.id))

        if request.method == 'POST':
            data, errors = _validate_event_form(request.form, existing_event=event)
            if errors:
                for e in errors:
                    flash(e, 'error')
                return render_template('events/form.html', event=event,
                    status_choices=Event.STATUS_CHOICES, form_data=request.form, action='Edit')

            event.name = data['name']
            event.organizer = data['organizer']
            event.expected_attendance = data['attendance']
            event.start_datetime = data['start_dt']
            event.end_datetime = data['end_dt']
            event.status = data['status']
            event.description = data['description']
            db.session.commit()
            flash(f"Event '{event.name}' updated successfully.", 'success')
            return redirect(url_for('event_detail', event_id=event.id))

        form_data = {
            'name': event.name, 'organizer': event.organizer,
            'expected_attendance': event.expected_attendance,
            'start_datetime': event.start_datetime.strftime('%Y-%m-%dT%H:%M'),
            'end_datetime': event.end_datetime.strftime('%Y-%m-%dT%H:%M'),
            'status': event.status, 'description': event.description or '',
        }
        return render_template('events/form.html', event=event,
            status_choices=Event.STATUS_CHOICES, form_data=form_data, action='Edit')

    @app.route('/events/<int:event_id>/cancel', methods=['POST'])
    @login_required
    def event_cancel(event_id):
        event = Event.query.get_or_404(event_id)
        if not current_user.is_admin and event.owner_id != current_user.id:
            abort(403)
        if event.status in ['Cancelled', 'Completed']:
            flash(f"Event is already {event.status}.", 'error')
            return redirect(url_for('event_detail', event_id=event.id))
        for req in event.resource_requests:
            if req.status in ['Pending', 'Approved']:
                cancel_allocation(req)
        event.status = 'Cancelled'
        db.session.commit()
        flash(f"Event '{event.name}' cancelled and all resource allocations released.", 'success')
        return redirect(url_for('events_list'))

    # ── Resources (Admin only) ────────────────────────────────────────────────

    @app.route('/resources')
    @login_required
    def resources_list():
        type_filter = request.args.get('type', '')
        active_filter = request.args.get('active', '')
        q = Resource.query
        if type_filter:
            q = q.filter_by(resource_type=type_filter)
        if active_filter == '1':
            q = q.filter_by(is_active=True)
        elif active_filter == '0':
            q = q.filter_by(is_active=False)
        resources = q.order_by(Resource.resource_type, Resource.name).all()
        return render_template('resources/list.html', resources=resources,
            type_filter=type_filter, active_filter=active_filter,
            resource_types=Resource.RESOURCE_TYPES)

    @app.route('/resources/create', methods=['GET', 'POST'])
    @admin_required
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
            elif Resource.query.filter_by(name=name).first():
                errors.append(f"A resource named '{name}' already exists.")

            if not rtype or rtype not in Resource.RESOURCE_TYPES:
                errors.append(f"Valid resource type is required. Choose from: {', '.join(Resource.RESOURCE_TYPES)}.")

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
                        errors.append("Capacity must be a whole number.")

            if errors:
                for e in errors:
                    flash(e, 'error')
                return render_template('resources/form.html',
                    resource_types=Resource.RESOURCE_TYPES,
                    capacity_types=Resource.CAPACITY_TYPES,
                    form_data=request.form, action='Add')

            resource = Resource(name=name, resource_type=rtype, capacity=capacity,
                is_active=is_active, description=description)
            db.session.add(resource)
            db.session.commit()
            flash(f"Resource '{resource.name}' added successfully.", 'success')
            return redirect(url_for('resources_list'))

        return render_template('resources/form.html',
            resource_types=Resource.RESOURCE_TYPES,
            capacity_types=Resource.CAPACITY_TYPES, form_data={}, action='Add')

    @app.route('/resources/<int:resource_id>/edit', methods=['GET', 'POST'])
    @admin_required
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
            else:
                existing = Resource.query.filter_by(name=name).first()
                if existing and existing.id != resource_id:
                    errors.append(f"A resource named '{name}' already exists.")

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
                return render_template('resources/form.html', resource=resource,
                    resource_types=Resource.RESOURCE_TYPES,
                    capacity_types=Resource.CAPACITY_TYPES,
                    form_data=request.form, action='Edit')

            resource.name = name
            resource.resource_type = rtype
            resource.capacity = capacity
            resource.is_active = is_active
            resource.description = description
            db.session.commit()
            flash(f"Resource '{resource.name}' updated.", 'success')
            return redirect(url_for('resources_list'))

        form_data = {
            'name': resource.name, 'resource_type': resource.resource_type,
            'capacity': resource.capacity or '', 'is_active': resource.is_active,
            'description': resource.description or '',
        }
        return render_template('resources/form.html', resource=resource,
            resource_types=Resource.RESOURCE_TYPES,
            capacity_types=Resource.CAPACITY_TYPES,
            form_data=form_data, action='Edit')

    @app.route('/resources/<int:resource_id>/toggle', methods=['POST'])
    @admin_required
    def resource_toggle(resource_id):
        resource = Resource.query.get_or_404(resource_id)
        resource.is_active = not resource.is_active
        db.session.commit()
        state = 'activated' if resource.is_active else 'deactivated'
        flash(f"Resource '{resource.name}' {state}.", 'success')
        return redirect(url_for('resources_list'))

    @app.route('/resources/availability')
    @login_required
    def resource_availability():
        date_str = request.args.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = datetime.utcnow().date()
        resources = Resource.query.filter_by(is_active=True).order_by(
            Resource.resource_type, Resource.name).all()
        availability_data = {r.id: get_resource_availability(r.id, selected_date) for r in resources}
        return render_template('resources/availability.html',
            resources=resources, availability_data=availability_data,
            selected_date=selected_date, date_str=date_str)

    # ── Resource Requests ─────────────────────────────────────────────────────

    @app.route('/requests')
    @login_required
    def requests_list():
        status_filter = request.args.get('status', '')
        q = ResourceRequest.query
        if not current_user.is_admin:
            q = q.filter_by(requester_id=current_user.id)
        if status_filter:
            q = q.filter_by(status=status_filter)
        reqs = q.order_by(ResourceRequest.created_at.desc()).all()
        return render_template('requests/list.html', reqs=reqs,
            status_filter=status_filter,
            status_choices=ResourceRequest.STATUS_CHOICES)

    @app.route('/requests/create', methods=['GET', 'POST'])
    @login_required
    def request_create():
        events_q = Event.query.filter(Event.status.in_(['Draft', 'Pending', 'Approved']))
        if not current_user.is_admin:
            events_q = events_q.filter_by(owner_id=current_user.id)
        events = events_q.order_by(Event.name).all()
        resources = Resource.query.filter_by(is_active=True).order_by(
            Resource.resource_type, Resource.name).all()
        resources_json = [r.to_dict() for r in resources]  # ← fix for tojson bug

        if request.method == 'POST':
            event_id = request.form.get('event_id', type=int)
            start_str = request.form.get('start_datetime', '').strip()
            end_str = request.form.get('end_datetime', '').strip()
            notes = request.form.get('notes', '').strip()
            item_types = request.form.getlist('item_type[]')
            item_qtys = request.form.getlist('item_qty[]')
            item_prefs = request.form.getlist('item_preferred[]')

            errors = []
            event = None

            if not event_id:
                errors.append("Please select an event.")
            else:
                event = Event.query.get(event_id)
                if not event:
                    errors.append("Selected event does not exist.")
                elif not current_user.is_admin and event.owner_id != current_user.id:
                    abort(403)
                elif event.status in ['Cancelled', 'Rejected', 'Completed']:
                    errors.append(f"Cannot request resources for a {event.status} event.")

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

            if start_dt and end_dt:
                if end_dt <= start_dt:
                    errors.append("End time must be after start time.")
                # ── Phase 8: request must fall within event time ──────────
                elif event:
                    if start_dt < event.start_datetime:
                        errors.append(
                            f"Resource usage cannot start before the event starts "
                            f"({event.start_datetime.strftime('%d %b %Y, %I:%M %p')})."
                        )
                    if end_dt > event.end_datetime:
                        errors.append(
                            f"Resource usage cannot end after the event ends "
                            f"({event.end_datetime.strftime('%d %b %Y, %I:%M %p')})."
                        )

            if not any(t for t in item_types):
                errors.append("Please add at least one resource type to the request.")

            items_data = []
            if not errors:
                for i, rtype in enumerate(item_types):
                    if not rtype:
                        continue
                    qty_str = item_qtys[i] if i < len(item_qtys) else '1'
                    pref_str = item_prefs[i] if i < len(item_prefs) else ''
                    try:
                        qty = max(1, int(qty_str)) if qty_str else 1
                    except ValueError:
                        qty = 1
                    pref_id = int(pref_str) if pref_str and pref_str.isdigit() else None
                    items_data.append({
                        'resource_type': rtype, 'quantity': qty, 'preferred_resource_id': pref_id
                    })

            if not errors and items_data:
                _, item_errors, alts = validate_request_items(
                    items_data, event.expected_attendance, start_dt, end_dt
                )
                if item_errors:
                    for e in item_errors:
                        flash(e, 'error')
                    for rtype, alt in alts.items():
                        cap = f" (Capacity: {alt.capacity})" if alt.capacity else ""
                        flash(
                            f"💡 Alternative for {rtype}: '{alt.name}'{cap} is available at that time.",
                            'info'
                        )
                    errors.append("__items__")  # sentinel to skip save

            if errors and errors != ['__items__']:
                for e in errors:
                    if e != '__items__':
                        flash(e, 'error')
                return render_template('requests/form.html',
                    events=events, resources_json=resources_json,
                    resource_types=Resource.RESOURCE_TYPES,
                    capacity_types=Resource.CAPACITY_TYPES,
                    form_data=request.form, action='Create')

            if '__items__' in errors:
                return render_template('requests/form.html',
                    events=events, resources_json=resources_json,
                    resource_types=Resource.RESOURCE_TYPES,
                    capacity_types=Resource.CAPACITY_TYPES,
                    form_data=request.form, action='Create')

            req = ResourceRequest(
                event_id=event_id, requester_id=current_user.id,
                start_datetime=start_dt, end_datetime=end_dt,
                status='Pending', notes=notes,
            )
            db.session.add(req)
            db.session.flush()

            for item_d in items_data:
                db.session.add(ResourceRequestItem(
                    request_id=req.id,
                    resource_type=item_d['resource_type'],
                    quantity=item_d['quantity'],
                    preferred_resource_id=item_d['preferred_resource_id'],
                ))
            db.session.commit()
            flash("Resource request submitted successfully.", 'success')
            return redirect(url_for('request_detail', request_id=req.id))

        return render_template('requests/form.html',
            events=events, resources_json=resources_json,
            resource_types=Resource.RESOURCE_TYPES,
            capacity_types=Resource.CAPACITY_TYPES,
            form_data={}, action='Create')

    @app.route('/requests/<int:request_id>')
    @login_required
    def request_detail(request_id):
        req = ResourceRequest.query.get_or_404(request_id)
        if not current_user.is_admin and req.requester_id != current_user.id:
            abort(403)
        alternatives = {}
        if req.status == 'Pending':
            for item in req.items:
                pref_id = item.preferred_resource_id
                req_cap = (
                    req.event.expected_attendance
                    if item.resource_type in Resource.CAPACITY_TYPES else None
                )
                alt = find_alternative(
                    item.resource_type, req_cap,
                    req.start_datetime, req.end_datetime,
                    exclude_ids=[pref_id] if pref_id else [],
                )
                if alt:
                    alternatives[item.id] = alt
        return render_template('requests/detail.html', req=req, alternatives=alternatives)

    @app.route('/requests/<int:request_id>/approve', methods=['POST'])
    @admin_required
    def request_approve(request_id):
        req = ResourceRequest.query.get_or_404(request_id)
        success, message = process_allocation(req)
        flash(message, 'success' if success else 'error')
        return redirect(url_for('request_detail', request_id=req.id))

    @app.route('/requests/<int:request_id>/reject', methods=['POST'])
    @admin_required
    def request_reject(request_id):
        req = ResourceRequest.query.get_or_404(request_id)
        if not req.can_transition_to('Rejected'):
            flash(f"Request is already {req.status} and cannot be rejected.", 'error')
            return redirect(url_for('request_detail', request_id=req.id))
        reason = request.form.get('rejection_reason', '').strip()
        req.status = 'Rejected'
        req.rejection_reason = reason or None
        db.session.commit()
        flash("Request rejected.", 'success')
        return redirect(url_for('request_detail', request_id=req.id))

    @app.route('/requests/<int:request_id>/cancel', methods=['POST'])
    @login_required
    def request_cancel(request_id):
        req = ResourceRequest.query.get_or_404(request_id)
        if not current_user.is_admin and req.requester_id != current_user.id:
            abort(403)
        success, message = cancel_allocation(req)
        flash(message, 'success' if success else 'error')
        return redirect(url_for('request_detail', request_id=req.id))

    # ── API ───────────────────────────────────────────────────────────────────

    @app.route('/api/resources-by-type')
    @login_required
    def api_resources_by_type():
        rtype = request.args.get('type', '')
        resources = Resource.query.filter_by(
            resource_type=rtype, is_active=True
        ).order_by(Resource.name).all()
        return jsonify([r.to_dict() for r in resources])


def _register_error_handlers(app):
    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Internal server error")
        return render_template('errors/500.html'), 500


# ─── Entry Point ──────────────────────────────────────────────────────────────

app = create_app()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=False)
