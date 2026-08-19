# PUEventAlloc — Pillai University Event Resource Allocation System

A Flask web application developed as an **internship task project** for managing university events and shared resources such as auditoriums, laboratories, projectors, microphones, and cameras.

The system supports event management, resource requests, availability checking, conflict detection, atomic allocation, and role-based access control.

---

## Features

- **Event Management** — Create, edit, cancel, and filter events
- **Resource Management** — Admin manages resources and their availability
- **Resource Requests** — Organizers request one or more resources for an event
- **Conflict Detection** — Prevents double-booking of resources
- **Atomic Allocation** — All resources in a request are allocated together or none are allocated
- **Alternative Suggestions** — Suggests suitable available alternatives
- **Role-Based Access** — Admin and Organizer roles with server-side authorization
- **Approval Workflow** — Pending → Approved / Rejected
- **Availability Validation** — Requests must fall within the event's time window
- **Approval-Time Revalidation** — Resources are checked again before allocation

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask 3 |
| ORM | SQLAlchemy 2 |
| Database | SQLite |
| Authentication | Flask-Login |
| Password Hashing | Werkzeug |
| Migrations | Flask-Migrate |
| Frontend | Jinja2 |
| Styling | Tailwind CSS |
| JavaScript | Vanilla JavaScript |
| Testing | pytest |
| CI | GitHub Actions |
| Deployment | Render |

---

## Project Structure

```text
PUEventAlloc/
├── app.py
├── models.py
├── services.py
├── seed.py
├── init_db.py
├── requirements.txt
├── .env.example
├── README.md
├── templates/
├── static/
└── tests/
```

---

## Installation & Running Locally

### 1. Clone the repository

```bash
git clone https://github.com/vedantgrd/PUEventAlloc.git
cd PUEventAlloc
```

### 2. Create a virtual environment

#### Windows

```bash
python -m venv venv
venv\Scripts\activate
```

#### macOS/Linux

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file based on `.env.example`:

```env
SECRET_KEY=replace-with-a-long-random-string
DATABASE_URL=sqlite:///pillai_events.db
FLASK_ENV=development
FLASK_DEBUG=1
```

Generate a secure secret key with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the generated value into `SECRET_KEY`.

### 5. Create and seed the database

```bash
python seed.py
```

### 6. Run the application

```bash
python app.py
```

Open:

```text
http://localhost:5000
```

---

## Database Setup

The application uses SQLite for the internship project and local development.

The database file is:

```text
pillai_events.db
```

The main tables are:

| Table | Purpose |
|---|---|
| `users` | Admin and Organizer accounts |
| `events` | Event details and status |
| `resources` | Allocatable university resources |
| `resource_requests` | Resource requests |
| `resource_request_items` | Individual requested resources |
| `resource_allocations` | Confirmed resource allocations |

---

## Important Local Database Fix

If the application code/models have been changed and the existing `pillai_events.db` was created using an older version of the models, `db.create_all()` will **not update existing tables**.

For example, if the current `Event` model contains:

```text
owner_id
```

but the old SQLite database does not contain that column, the application may produce:

```text
sqlite3.OperationalError:
table events has no column named owner_id
```

This is a schema mismatch between the existing SQLite database and the current models.

### Fix during local development

Delete the old database:

#### Windows

```cmd
del pillai_events.db
```

#### macOS/Linux

```bash
rm pillai_events.db
```

Then recreate and seed it:

```bash
python seed.py
```

This creates a fresh database using the current models.

**Warning:** Deleting the database removes the existing local data.

For a production application, database migrations should be used instead of deleting the database.

---

## Demo Credentials

The seed script creates the following demo accounts:

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin123` |
| Organizer | `organizer` | `org123` |
| Organizer | `organizer2` | `org123` |

These credentials are included for demonstration and testing purposes.

They should be changed before using the application in a real production environment.

---

## Event Workflow

Events follow a basic lifecycle:

```text
Draft
  ↓
Pending
  ↓
Approved
  ↓
Completed
```

An event may also be:

```text
Pending → Rejected
```

or:

```text
Approved → Cancelled
```

---

## Resource Request Workflow

```text
Organizer
    ↓
Select Event
    ↓
Select Usage Time
    ↓
Select Resources
    ↓
Submit Request
    ↓
System validates availability
    ↓
Admin reviews request
    ↓
Approve / Reject
    ↓
Resources allocated if approved
```

---

## Conflict Detection

A resource is considered unavailable when another active allocation overlaps the requested time.

The system uses:

```text
existing.start_datetime < requested.end_datetime
AND
existing.end_datetime > requested.start_datetime
```

Examples:

| Existing | Requested | Result |
|---|---|---|
| 10:00–14:00 | 12:00–16:00 | ❌ Conflict |
| 10:00–14:00 | 14:00–16:00 | ✅ Allowed |
| 10:00–14:00 | 08:00–10:00 | ✅ Allowed |
| 10:00–14:00 | 10:00–14:00 | ❌ Conflict |
| 10:00–14:00 | 09:00–15:00 | ❌ Conflict |
| 10:00–14:00 | 11:00–12:00 | ❌ Conflict |

Back-to-back bookings are allowed.

Cancelled allocations do not block resources.

---

## Atomic Resource Allocation

Resource allocation is performed as an all-or-nothing transaction.

For example, if a request contains:

```text
1 Auditorium
1 Projector
2 Microphones
```

and one of the required resources cannot be allocated, the entire request fails.

No partial allocation is left in the database.

The system revalidates:

1. Resource existence
2. Resource active status
3. Resource type
4. Capacity
5. Time availability

Only after all checks succeed are the allocations committed.

---

## Alternative Resource Selection

When a preferred resource is unavailable, the system can suggest an alternative.

The selection process considers:

1. Matching resource type
2. Active resource
3. Required capacity
4. Time availability
5. Best suitable capacity for venues

For example:

```text
Expected attendance: 80

Seminar Hall A → Capacity 100
Main Auditorium → Capacity 500
```

The system prefers the smaller suitable venue.

---

## Request Within Event Validation

Resource usage must fall completely within the selected event's scheduled time.

The rule is:

```text
event.start_datetime <= request.start_datetime
AND
request.end_datetime <= event.end_datetime
```

Example:

```text
Event:
09:00 → 17:00

Valid:
10:00 → 15:00

Invalid:
08:00 → 15:00
```

This validation is enforced on the backend.

---

## Role-Based Access Control

The application has two roles:

- Admin
- Organizer

| Action | Admin | Organizer |
|---|---:|---:|
| Create events | ✅ | ✅ |
| Edit own events | ✅ | ✅ |
| View other organizers' events | ✅ | ❌ |
| Manage resources | ✅ | ❌ |
| Activate/deactivate resources | ✅ | ❌ |
| View all requests | ✅ | ❌ |
| View own requests | ✅ | ✅ |
| Approve requests | ✅ | ❌ |
| Reject requests | ✅ | ❌ |

Authorization is enforced server-side rather than relying only on hidden frontend buttons.

---

## Approval-Time Revalidation

Resources are checked again when an administrator approves a request.

This prevents stale requests from allocating resources that became unavailable after the request was submitted.

Example:

```text
Request submitted
      ↓
Resource available
      ↓
Another request books resource
      ↓
Admin approves original request
      ↓
System detects conflict
      ↓
Allocation fails
```

---

## Testing

Run the complete test suite:

```bash
pytest tests/ -v
```

Tests cover:

| Test Module | Purpose |
|---|---|
| `test_conflicts.py` | Conflict detection |
| `test_allocation.py` | Atomic allocation and cancellation |
| `test_alternatives.py` | Alternative resource selection |
| `test_authorization.py` | Authentication and authorization |
| `test_events.py` | Event validation |
| `test_requests.py` | Resource request validation |
| `test_resources.py` | Resource management |

---

## Deployment on Render

The project is deployed as a Python Web Service on Render.

### Build Command

```bash
pip install -r requirements.txt
```

### Start Command

```bash
gunicorn app:app
```

### Environment Variables

Set the following in Render:

```text
SECRET_KEY=<your-generated-random-secret>
DATABASE_URL=sqlite:///pillai_events.db
```

The `SECRET_KEY` should be a long random value.

Generate one locally with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Render SQLite Note

The internship version uses SQLite.

SQLite is convenient for this project, but a SQLite file stored inside a deployment container should not be treated as permanent production storage. Redeployments or instance changes can cause the database file to be lost or recreated.

Therefore:

```text
Local development
→ SQLite is convenient

Internship/demo deployment
→ SQLite can be used with the above limitation

Production deployment
→ PostgreSQL is recommended
```

For persistent production data, the application should be migrated to PostgreSQL or another persistent database.

---

## Deployment-Specific Schema Issue

During deployment, an issue can occur if an old SQLite database exists while the current SQLAlchemy models contain newer columns.

For example:

```text
Current Event model
        ↓
contains owner_id

Existing SQLite database
        ↓
events table does not contain owner_id

Result
        ↓
sqlite3.OperationalError
```

The local development fix is to remove the old database and run:

```bash
python seed.py
```

The original project code did not have this problem when starting with a fresh database. It occurred when deploying/reusing an SQLite database whose schema was created from an older version of the project.

For a proper production deployment, Flask-Migrate/Alembic migrations should be used to update the database schema without deleting existing data.

---

## Environment Variables

`.env.example`:

```env
SECRET_KEY=replace-with-a-long-random-string
DATABASE_URL=sqlite:///pillai_events.db
FLASK_ENV=development
FLASK_DEBUG=0
```

For production, set:

```text
FLASK_DEBUG=0
```

and use a strong random `SECRET_KEY`.

---

## Security Considerations

The application includes:

- Password hashing with Werkzeug
- Flask-Login authentication
- Role-based authorization
- Server-side access control
- Resource ownership checks
- Approval-time resource validation
- Atomic database transactions

For a production-ready system, the following would additionally be recommended:

- CSRF protection
- Secure cookies
- HTTPS
- PostgreSQL
- Database migrations
- Rate limiting
- Password reset functionality
- Production monitoring and logging

---

## Known Limitations

This project was developed as an **internship task** and is intended primarily as a functional demonstration.

Current limitations include:

- SQLite is used instead of PostgreSQL
- SQLite is not ideal for high-concurrency production workloads
- No email notification system
- No password reset flow
- No file uploads
- No pagination for large datasets
- No timezone-aware datetime handling
- No CSRF implementation
- Demo credentials are included for testing
- Tailwind CSS is loaded through CDN

---

## Future Improvements

Possible future improvements include:

1. PostgreSQL database
2. Proper database migrations
3. CSRF protection
4. Email notifications
5. Password reset functionality
6. Pagination
7. Advanced search and filtering
8. Calendar-based resource availability
9. Audit logs
10. Docker support
11. Automated deployment pipeline
12. Timezone-aware event scheduling

---

## Architecture

```text
Browser
   │
   ▼
Flask Routes
   │
   ├── Authentication / Authorization
   │
   ▼
Business Services
   │
   ├── Conflict Detection
   ├── Resource Availability
   ├── Alternative Selection
   └── Atomic Allocation
   │
   ▼
SQLAlchemy ORM
   │
   ▼
SQLite Database
```

---

## Project Status

**Completed — Internship Task Project**

The application demonstrates the complete event resource allocation workflow:

```text
Event Creation
      ↓
Resource Request
      ↓
Availability Validation
      ↓
Conflict Detection
      ↓
Admin Approval
      ↓
Atomic Resource Allocation
```

---

## Repository

GitHub:

https://github.com/vedantgrd/PUEventAlloc

---

## Author

**Vedant Garud**

This project was developed as part of an internship task.

