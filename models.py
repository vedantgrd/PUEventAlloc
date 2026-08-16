from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Event(db.Model):
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    organizer = db.Column(db.String(100), nullable=False)
    expected_attendance = db.Column(db.Integer, nullable=False)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Draft')
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    resource_requests = db.relationship('ResourceRequest', backref='event', lazy=True, cascade='all, delete-orphan')

    STATUS_CHOICES = ['Draft', 'Pending', 'Approved', 'Rejected', 'Cancelled', 'Completed']

    def __repr__(self):
        return f'<Event {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'organizer': self.organizer,
            'expected_attendance': self.expected_attendance,
            'start_datetime': self.start_datetime.strftime('%Y-%m-%d %H:%M'),
            'end_datetime': self.end_datetime.strftime('%Y-%m-%d %H:%M'),
            'status': self.status,
            'description': self.description
        }


class Resource(db.Model):
    __tablename__ = 'resources'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    resource_type = db.Column(db.String(50), nullable=False)
    capacity = db.Column(db.Integer, nullable=True)  # Only for venues
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    allocations = db.relationship('ResourceAllocation', backref='resource', lazy=True)

    RESOURCE_TYPES = ['Auditorium', 'Laboratory', 'Projector', 'Microphone', 'Camera', 'Computer']
    CAPACITY_TYPES = ['Auditorium', 'Laboratory']  # Types that have capacity

    def __repr__(self):
        return f'<Resource {self.name}>'

    def has_capacity(self):
        return self.resource_type in self.CAPACITY_TYPES

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'resource_type': self.resource_type,
            'capacity': self.capacity,
            'is_active': self.is_active,
            'description': self.description
        }


class ResourceRequest(db.Model):
    __tablename__ = 'resource_requests'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pending')
    notes = db.Column(db.Text, nullable=True)
    rejection_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    items = db.relationship('ResourceRequestItem', backref='request', lazy=True, cascade='all, delete-orphan')
    allocations = db.relationship('ResourceAllocation', backref='request', lazy=True, cascade='all, delete-orphan')

    STATUS_CHOICES = ['Pending', 'Approved', 'Rejected', 'Cancelled']

    def __repr__(self):
        return f'<ResourceRequest {self.id} for Event {self.event_id}>'


class ResourceRequestItem(db.Model):
    __tablename__ = 'resource_request_items'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('resource_requests.id'), nullable=False)
    resource_type = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    preferred_resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=True)

    preferred_resource = db.relationship('Resource', foreign_keys=[preferred_resource_id])

    def __repr__(self):
        return f'<RequestItem {self.resource_type} x{self.quantity}>'


class ResourceAllocation(db.Model):
    __tablename__ = 'resource_allocations'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('resource_requests.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Allocated')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    STATUS_CHOICES = ['Allocated', 'Cancelled']

    def __repr__(self):
        return f'<Allocation Resource {self.resource_id} for Request {self.request_id}>'
