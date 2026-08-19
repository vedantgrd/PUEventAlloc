# PUEventAlloc — Pillai University Event Resource Allocation System

A Flask web application for managing college events and shared resources (auditoriums, labs, projectors, microphones, cameras, computers) with conflict detection, atomic allocation, and role-based access control.

---

## Features

- **Event Management** — Create, edit, cancel, and filter events (Draft → Pending → Approved → Completed/Cancelled)
- **Resource Management** — Admin manages resources with activate/deactivate; inactive resources cannot be allocated
- **Resource Requests** — Organizers request one or more resources per event with a specific time window
- **Conflict Detection** — Backend prevents double-booking; back-to-back bookings are allowed
- **Atomic Allocation** — If any resource in a request fails, zero are allocated (all-or-nothing transaction)
- **Alternative Suggestions** — System suggests the best available alternative when a resource is unavailable
- **Role-Based Access** — Admin vs Organizer with server-side enforcement; no IDOR vulnerabilities
- **Approval Workflow** — Pending → Approved (Allocated) / Rejected; cancellation releases resources

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask 3 |
| ORM | SQLAlchemy 2 + Flask-Migrate |
| Database | SQLite |
| Auth | Flask-Login + Werkzeug password hashing |
| Frontend | Jinja2, Tailwind CSS (CDN), vanilla JS |
| Testing | pytest + pytest-flask |
| CI | GitHub Actions |

---

## Architecture

```
Browser
  │
  ▼
Flask Routes (app.py)
  │  ← login_required / admin_required decorators
  ▼
Business Services (services.py)
  │  ← conflict detection, alternative selection, atomic allocation
  ▼
SQLAlchemy ORM (models.py)
  │
  ▼
SQLite (pillai_events.db)
```

---

## Installation & Running

```bash
# 1. Clone the repository
git clone https://github.com/vedantgrd/PUEventAlloc
cd PUEventAlloc

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and set a strong SECRET_KEY

# 5. Seed demo data (creates DB + sample users/events/resources)
python seed.py

# 6. Run the application
python app.py
# → Open http://localhost:5000
```

---

## Database Setup

SQLite database is created automatically. Tables are created by `db.create_all()` on startup.

**Schema:**

| Table | Purpose |
|---|---|
| `users` | Admin and Organizer accounts with hashed passwords |
| `events` | Event details and lifecycle status |
| `resources` | Allocatable resources with type, capacity, active flag |
| `resource_requests` | A request to use resources for an event at a specific time |
| `resource_request_items` | Line items in a request (type, quantity, preferred resource) |
| `resource_allocations` | Confirmed allocations; cancelled records are preserved for history |

**Indexes on `resource_allocations`:**
- `ix_alloc_resource_time` — `(resource_id, start_datetime, end_datetime)` — makes conflict queries fast
- `ix_alloc_status` — `status` — fast filtering of active vs cancelled

To reset: delete `pillai_events.db` then `python seed.py`.

---

## Demo Credentials

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin123` |
| Organizer | `organizer` | `org123` |
| Organizer | `organizer2` | `org123` |

> Change passwords before deploying anywhere public.

---

## How Conflict Detection Works

A conflict exists when two allocations for the same resource **overlap in time**. The canonical formula:

```
existing.start_datetime < requested.end_datetime
AND
existing.end_datetime   > requested.start_datetime
```

This correctly identifies all overlap shapes:

| Case | Existing | New | Result |
|---|---|---|---|
| Partial overlap | 10:00–14:00 | 12:00–16:00 | ❌ CONFLICT |
| Back-to-back | 10:00–14:00 | 14:00–16:00 | ✅ ALLOWED |
| Before | 10:00–14:00 | 08:00–10:00 | ✅ ALLOWED |
| Exact same | 10:00–14:00 | 10:00–14:00 | ❌ CONFLICT |
| New contains existing | 10:00–14:00 | 09:00–15:00 | ❌ CONFLICT |
| New inside existing | 10:00–14:00 | 11:00–12:00 | ❌ CONFLICT |
| New wraps existing | 10:00–14:00 | 08:00–16:00 | ❌ CONFLICT |

Only `status = 'Allocated'` records block resources. Cancelled allocations are excluded.

Back-to-back bookings are allowed because `14:00 < 14:00` is `False` — the formula naturally handles this without special-casing.

---

## Atomic Multi-Resource Transactions

Implemented in `services.process_allocation()`:

```
BEGIN (implicit SQLAlchemy session)
  For each resource in the request:
    1. Revalidate: resource still exists
    2. Revalidate: resource still active (race condition safe)
    3. Revalidate: resource type still matches
    4. Revalidate: capacity still sufficient
    5. Revalidate: no time conflict (race condition safe)
    If any check fails → db.session.rollback() → return error
  All checks passed → db.session.add(all allocations) → commit
```

**No allocations are created until every resource passes all checks.** If the commit fails, SQLAlchemy rolls back automatically.

---

## Alternative Resource Selection

Implemented in `services.find_alternative()`:

1. **Filter by type** — same `resource_type` as requested
2. **Filter active** — `is_active = True` only
3. **Filter by capacity** — for venues (Auditorium, Laboratory): `capacity >= event.expected_attendance`
4. **Check availability** — no active allocation overlapping the requested time
5. **Rank** — ascending capacity (smallest room that fits) for venues; alphabetical for equipment
6. **Return first match** — deterministic; first conflict-free candidate is suggested

Example: attendance=80, Hall A (cap 100) and Hall B (cap 200) both available → Hall A is suggested (smallest fit).

---

## Approval-Time Revalidation (Race Condition Safety)

Resources are revalidated **at the moment of approval**, not just at request creation:

- A resource deactivated after the request was submitted → approval fails
- A resource booked by another request after submission → approval fails

This prevents stale state from causing incorrect allocations.

---

## Authentication & Authorization

- Passwords hashed with Werkzeug (PBKDF2 + SHA-256)
- Sessions managed by Flask-Login
- Two roles: `Admin` and `Organizer`

| Action | Admin | Organizer |
|---|---|---|
| Create/edit own events | ✅ | ✅ |
| View others' events | ✅ | ❌ (403) |
| Add/edit resources | ✅ | ❌ (403) |
| Activate/deactivate resources | ✅ | ❌ (403) |
| Approve/reject requests | ✅ | ❌ (403) |
| View all requests | ✅ | Own only |

Authorization is enforced server-side on every route. Hiding buttons in HTML is not sufficient — direct URL access is blocked.

---

## Request-Within-Event Validation

A resource request's time window must fall within the event's time window:

```
event.start_datetime <= request.start_datetime
AND
request.end_datetime <= event.end_datetime
```

Enforced on the backend in `app.py::request_create`.

---

## Testing

```bash
# Run all 59 tests
pytest tests/ -v

# Run a specific module
pytest tests/test_conflicts.py -v
```

**Test coverage:**

| Module | Tests |
|---|---|
| `test_conflicts.py` | 10 — all 7 overlap cases + boundary + cancelled exclusion |
| `test_allocation.py` | 8 — atomic rollback, cancellation, history preservation |
| `test_alternatives.py` | 8 — capacity, inactive, type, smallest-fit, back-to-back |
| `test_authorization.py` | 9 — admin routes, IDOR, state transitions |
| `test_events.py` | 8 — validation + authorization |
| `test_requests.py` | 8 — time-window, inactive, wrong type, capacity |
| `test_resources.py` | 7 — CRUD, capacity rules, duplicate names |
| **Total** | **59 tests, all passing** |

---

## Environment Variables

See `.env.example`:

```
SECRET_KEY=replace-with-a-long-random-string
DATABASE_URL=sqlite:///pillai_events.db
FLASK_ENV=development
FLASK_DEBUG=0
```

Never commit `.env` to version control. Set `SECRET_KEY` to a random 32+ character string in production.

---

## Important Assumptions

1. **No timezone handling** — all datetimes stored and compared in UTC. Production would need timezone-aware timestamps.
2. **SQLite concurrency** — SQLite has limited concurrent write support. For high traffic, migrate to PostgreSQL.
3. **No email notifications** — approval/rejection notifications are UI-only.
4. **No CSRF tokens** — forms rely on Flask-Login's session cookie. Adding Flask-WTF CSRF tokens is recommended for production.
5. **Organizer identity** — the `organizer` field on events is a free-text name; it is separate from the `owner_id` FK to the logged-in user.
6. **Capacity for equipment** — Projectors, Microphones, Cameras, and Computers have no meaningful capacity; only Auditoriums and Laboratories require it.

## Known Limitations

- No pagination on long lists
- No email/password reset flow
- No file uploads or attachments
- SQLite not suitable for concurrent production load
