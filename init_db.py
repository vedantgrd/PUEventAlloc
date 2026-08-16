"""Run this script once to initialize the database and seed sample data."""
from app import app, db, _seed_data

with app.app_context():
    db.create_all()
    _seed_data()
    print("✅ Database initialized successfully.")
    print("   Tables created: events, resources, resource_requests, resource_request_items, resource_allocations")
