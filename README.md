# Pillai University – Event Resource Allocation System

A web application for managing events and shared resources (auditoriums, labs, projectors, microphones, cameras, computers) at Pillai University.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask |
| ORM | SQLAlchemy + Flask-Migrate |
| Database | SQLite |
| Frontend | Jinja2, Tailwind CSS (CDN), vanilla JS |

---

## Installation & Running

```bash
# 1. Clone the repository
git clone <repo-url>
cd pillai_event_system

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set environment variables (optional — defaults work for local dev)
cp .env.example .env

# 5. Initialize the database (creates tables + seeds 15 sample resources)
python init_db.py

# 6. Run the application
python app.py
# → Open http://localhost:5000
```

---

## Database Setup

The database is SQLite (`pillai_events.db`, created automatically on first run).

**Tables:**

| Table | Purpose |
|---|---|
| `events` | Stores event details and status |
| `resources` | Stores all allocatable resources |
| `resource_requests` | A request to use resources for an event |
| `resource_request_items` | Each line item in a request (type, qty, preference) |
| `resource_allocations` | Confirmed allocations linking a resource to a request |

**Seed data:** Running `python init_db.py` creates 15 sample resources:
- 3 Auditoriums (Main 500, Mini 200, Seminar Hall A 100)
- 3 Laboratories (Computer Lab 1 × 60, Computer Lab 2 × 40, Science Lab × 30)
- 3 Projectors
- 3 Microphones
- 2 Cameras
- 1 Computer set

To reset the database, delete `pillai_events.db` and re-run `python init_db.py`.

---

## How Conflict Detection Works

A **time overlap** exists when:
```
existing.start_datetime < new.end_datetime
AND
existing.end_datetime > new.start_datetime
```

This correctly handles all overlap cases:
- Partial overlap (e.g. existing 10–14, new 12–16) → **conflict**
- Full containment → **conflict**
- Back-to-back (existing 10–14, new 14–18) → **allowed** (boundary touching is NOT a conflict)

The check runs in `services.check_resource_conflict()` against `resource_allocations` where `status = 'Allocated'`. Cancelled allocations are excluded, so cancelled bookings free the slot immediately.

**Atomicity:** When a request is approved, all resources are allocated inside a single SQLAlchemy transaction. If any resource fails (conflict discovered mid-allocation, inactive resource, etc.), the entire transaction is rolled back — no partial allocations occur.

---

## How Alternative Resources Are Selected

When a requested resource is unavailable or unsuitable, `services.find_alternative()` selects a substitute:

1. **Filter by type** — only resources of the same type are considered (e.g., Projector for Projector).
2. **Filter active** — inactive resources are excluded.
3. **Filter by capacity** — for venue types (Auditorium, Laboratory), only resources with `capacity ≥ event.expected_attendance` are eligible.
4. **Check availability** — each candidate is checked for time conflicts using the same overlap logic.
5. **Pick smallest fit** — candidates are sorted by capacity ascending (for venues), so the smallest room that fits is preferred over a larger one. For non-capacity types, sorted alphabetically.
6. **Return first match** — the first candidate that passes all checks is returned.

If no suitable alternative exists, `None` is returned and the user sees an error message.

---

## Important Assumptions

1. **No authentication** — the system has no login. All pages are accessible to any user. In production, role-based access (organizer vs. admin) should be enforced.
2. **UTC timestamps** — all datetimes are stored and compared in UTC. No timezone conversion is done in this version.
3. **Quantity handling** — if an organizer requests `2 × Microphone`, the system finds two *separate* microphone resources. Each gets its own allocation row. If only 1 is available, the whole request fails.
4. **Capacity check applies to venues only** — Auditoriums and Laboratories have capacity. Projectors, Microphones, Cameras, and Computers do not.
5. **Resource type validation** — the preferred resource must match the requested type; requesting a Microphone but specifying a Projector as preferred is rejected.
6. **Allocation is admin-driven** — resource requests are submitted by organizers but approved/rejected by an administrator via the UI.
7. **Event cancellation cascades** — cancelling an event automatically cancels and releases all its resource allocations.

---

## Application Pages

| URL | Page |
|---|---|
| `/` | Dashboard with stats and quick actions |
| `/events` | Event list with filter by status/date |
| `/events/create` | Create new event |
| `/events/<id>` | Event detail and its resource requests |
| `/events/<id>/edit` | Edit an event |
| `/resources` | Resource list with filter |
| `/resources/create` | Add a new resource |
| `/resources/availability` | Day-view availability calendar |
| `/requests` | All resource requests |
| `/requests/create` | Submit a new resource request |
| `/requests/<id>` | View, approve, reject, or cancel a request |
