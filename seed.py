"""
Demo seed data for Pillai University Event Resource Allocation System.
Run: python seed.py

Creates:
  Admin  : username=admin      password=admin123
  Organizer: username=organizer  password=org123

Demo accounts use weak passwords intentionally — change them in production.
"""
from app import create_app
from models import db, User, Resource, Event, ResourceRequest, ResourceRequestItem, ResourceAllocation
from datetime import datetime, timedelta

app = create_app()

def seed():
    with app.app_context():
        db.create_all()

        if User.query.count() > 0:
            print("Database already seeded. Delete pillai_events.db to reseed.")
            return

        # ── Users ─────────────────────────────────────────────────────────
        admin = User(username='admin', name='Admin User',
                     email='admin@pillai.edu', role='Admin')
        admin.set_password('admin123')

        org1 = User(username='organizer', name='CS Department',
                    email='cs@pillai.edu', role='Organizer')
        org1.set_password('org123')

        org2 = User(username='organizer2', name='IT Department',
                    email='it@pillai.edu', role='Organizer')
        org2.set_password('org123')

        db.session.add_all([admin, org1, org2])
        db.session.flush()

        # ── Resources ─────────────────────────────────────────────────────
        resources = [
            Resource(name='Main Auditorium',   resource_type='Auditorium',  capacity=500, is_active=True,  description='Main campus auditorium – 500 seats'),
            Resource(name='Mini Auditorium',   resource_type='Auditorium',  capacity=200, is_active=True,  description='Departmental auditorium – 200 seats'),
            Resource(name='Seminar Hall A',    resource_type='Auditorium',  capacity=100, is_active=True,  description='Block A seminar hall – 100 seats'),
            Resource(name='Seminar Hall B',    resource_type='Auditorium',  capacity=80,  is_active=False, description='Under renovation (inactive)'),
            Resource(name='Computer Lab 1',    resource_type='Laboratory',  capacity=60,  is_active=True,  description='CS Dept – 60 workstations'),
            Resource(name='Computer Lab 2',    resource_type='Laboratory',  capacity=40,  is_active=True,  description='IT Dept – 40 workstations'),
            Resource(name='Science Lab',       resource_type='Laboratory',  capacity=30,  is_active=True,  description='Physics/Chemistry lab'),
            Resource(name='Projector P1',      resource_type='Projector',                is_active=True,  description='4K laser projector'),
            Resource(name='Projector P2',      resource_type='Projector',                is_active=True,  description='Full HD projector'),
            Resource(name='Projector P3',      resource_type='Projector',                is_active=False, description='Faulty – awaiting repair (inactive)'),
            Resource(name='Wireless Mic 1',    resource_type='Microphone',               is_active=True,  description='Sennheiser handheld'),
            Resource(name='Wireless Mic 2',    resource_type='Microphone',               is_active=True,  description='Sennheiser handheld'),
            Resource(name='Lapel Mic 1',       resource_type='Microphone',               is_active=True,  description='Clip-on lapel mic'),
            Resource(name='DSLR Camera 1',     resource_type='Camera',                   is_active=True,  description='Canon EOS R5'),
            Resource(name='Video Camera',      resource_type='Camera',                   is_active=True,  description='Sony professional camcorder'),
        ]
        db.session.add_all(resources)
        db.session.flush()
        r = {res.name: res for res in resources}

        # ── Events ────────────────────────────────────────────────────────
        base = datetime(2026, 9, 20)
        e1 = Event(name='Technical Workshop 2026', organizer='CS Department',
                   expected_attendance=80, owner_id=org1.id,
                   start_datetime=base.replace(hour=9),
                   end_datetime=base.replace(hour=17), status='Approved',
                   description='Annual technical workshop for CS students.')

        e2 = Event(name='Cultural Fest', organizer='Student Council',
                   expected_attendance=450, owner_id=org2.id,
                   start_datetime=(base + timedelta(days=7)).replace(hour=10),
                   end_datetime=(base + timedelta(days=7)).replace(hour=20),
                   status='Pending', description='Annual cultural festival.')

        e3 = Event(name='Alumni Meet 2026', organizer='Admin Office',
                   expected_attendance=300, owner_id=org1.id,
                   start_datetime=(base + timedelta(days=14)).replace(hour=11),
                   end_datetime=(base + timedelta(days=14)).replace(hour=16),
                   status='Draft')

        e4 = Event(name='Hackathon', organizer='IT Department',
                   expected_attendance=120, owner_id=org2.id,
                   start_datetime=(base - timedelta(days=5)).replace(hour=8),
                   end_datetime=(base - timedelta(days=5)).replace(hour=20),
                   status='Completed', description='24-hour hackathon.')

        db.session.add_all([e1, e2, e3, e4])
        db.session.flush()

        # ── Approved request with allocation (Technical Workshop) ─────────
        req1 = ResourceRequest(
            event_id=e1.id, requester_id=org1.id,
            start_datetime=base.replace(hour=9),
            end_datetime=base.replace(hour=13),
            status='Approved', notes='Morning session setup',
        )
        db.session.add(req1)
        db.session.flush()

        req1_items = [
            ResourceRequestItem(request_id=req1.id, resource_type='Auditorium',  quantity=1, preferred_resource_id=r['Seminar Hall A'].id),
            ResourceRequestItem(request_id=req1.id, resource_type='Projector',   quantity=1, preferred_resource_id=r['Projector P1'].id),
            ResourceRequestItem(request_id=req1.id, resource_type='Microphone',  quantity=2),
        ]
        db.session.add_all(req1_items)

        allocs = [
            ResourceAllocation(request_id=req1.id, resource_id=r['Seminar Hall A'].id,
                start_datetime=base.replace(hour=9), end_datetime=base.replace(hour=13), status='Allocated'),
            ResourceAllocation(request_id=req1.id, resource_id=r['Projector P1'].id,
                start_datetime=base.replace(hour=9), end_datetime=base.replace(hour=13), status='Allocated'),
            ResourceAllocation(request_id=req1.id, resource_id=r['Wireless Mic 1'].id,
                start_datetime=base.replace(hour=9), end_datetime=base.replace(hour=13), status='Allocated'),
            ResourceAllocation(request_id=req1.id, resource_id=r['Wireless Mic 2'].id,
                start_datetime=base.replace(hour=9), end_datetime=base.replace(hour=13), status='Allocated'),
        ]
        db.session.add_all(allocs)

        # ── Pending request (Cultural Fest – will conflict with another) ──
        fest_day = base + timedelta(days=7)
        req2 = ResourceRequest(
            event_id=e2.id, requester_id=org2.id,
            start_datetime=fest_day.replace(hour=10),
            end_datetime=fest_day.replace(hour=20),
            status='Pending', notes='Need main hall for entire fest.',
        )
        db.session.add(req2)
        db.session.flush()
        db.session.add(ResourceRequestItem(
            request_id=req2.id, resource_type='Auditorium', quantity=1,
            preferred_resource_id=r['Main Auditorium'].id,
        ))

        # ── Conflicting booking to demonstrate conflict detection ──────────
        req3 = ResourceRequest(
            event_id=e2.id, requester_id=org2.id,
            start_datetime=fest_day.replace(hour=10),
            end_datetime=fest_day.replace(hour=14),
            status='Approved',
        )
        db.session.add(req3)
        db.session.flush()
        db.session.add(ResourceRequestItem(
            request_id=req3.id, resource_type='Auditorium', quantity=1,
            preferred_resource_id=r['Mini Auditorium'].id,
        ))
        db.session.add(ResourceAllocation(
            request_id=req3.id, resource_id=r['Mini Auditorium'].id,
            start_datetime=fest_day.replace(hour=10),
            end_datetime=fest_day.replace(hour=14),
            status='Allocated',
        ))

        db.session.commit()
        print("✅ Seed complete.")
        print()
        print("  Demo credentials:")
        print("    Admin     → username: admin       password: admin123")
        print("    Organizer → username: organizer   password: org123")
        print()
        print(f"  Resources   : {Resource.query.count()}")
        print(f"  Events      : {Event.query.count()}")
        print(f"  Requests    : {ResourceRequest.query.count()}")
        print(f"  Allocations : {ResourceAllocation.query.count()}")

if __name__ == '__main__':
    seed()
