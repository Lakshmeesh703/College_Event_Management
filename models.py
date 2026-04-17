"""
SQLAlchemy ORM models — normalized schema (2NF+).

Relationships:
  User 1—0..1 Participant (participant accounts only)
  User 1—* Event (events created by a coordinator via created_by_id)
  Event 1—* EventParticipation *—1 Participant
  Event 1—* Result *—1 Participant

Each row stores created_at for audit / assignment requirements.
"""
from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint

db = SQLAlchemy()


class User(db.Model):
    """Login account. Roles: student, coordinator, management (legacy participant still supported)."""

    __tablename__ = "users"

    ROLE_STUDENT = "student"
    ROLE_PARTICIPANT = "participant"  # legacy alias
    ROLE_COORDINATOR = "coordinator"
    ROLE_MANAGEMENT = "management"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), nullable=False, index=True)
    is_external_user = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Participant profile (only for role=participant); other roles leave this empty
    participant_profile = db.relationship(
        "Participant",
        back_populates="user",
        uselist=False,
    )
    coordinator_profile = db.relationship(
        "CoordinatorProfile",
        back_populates="user",
        uselist=False,
        foreign_keys="CoordinatorProfile.user_id",
    )
    management_profile = db.relationship(
        "ManagementProfile",
        back_populates="user",
        uselist=False,
        foreign_keys="ManagementProfile.user_id",
    )

    events_created = db.relationship(
        "Event",
        back_populates="creator",
        foreign_keys="Event.created_by_id",
    )

    def __repr__(self):
        return f"<User {self.id} {self.email!r} {self.role}>"


class Event(db.Model):
    __tablename__ = "events"

    STATUS_UPCOMING = "Upcoming"
    STATUS_ONGOING = "Ongoing"
    STATUS_CLOSED = "Closed"
    STATUS_COMPLETED = "Completed"
    ALLOWED_SCHOOLS = (
        "School of Arts, Humanities and Social Sciences",
        "School of Management Sciences",
        "School of Mathematics and Natural Sciences",
        "School of Law, Governance and Public Policy",
        "School of Biosciences",
        "School of Engineering",
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    department = db.Column(db.String(120), nullable=False, index=True)
    school = db.Column(db.String(160), nullable=True, index=True)
    category = db.Column(db.String(50), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    venue = db.Column(db.String(200), nullable=False)
    organizer = db.Column(db.String(120), nullable=False)
    registration_deadline = db.Column(db.Date, nullable=True, index=True)
    max_participants = db.Column(db.Integer, nullable=True)
    allow_external = db.Column(db.Boolean, default=False, nullable=False)
    registration_closed_manually = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(32), default=STATUS_UPCOMING, nullable=False, index=True)
    # Coordinator who owns this event (optional for legacy rows before RBAC)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    creator = db.relationship("User", back_populates="events_created", foreign_keys=[created_by_id])
    participations = db.relationship(
        "EventParticipation", back_populates="event", cascade="all, delete-orphan"
    )
    results = db.relationship(
        "Result", back_populates="event", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Event {self.id} {self.name!r}>"

    @property
    def school_or_department(self) -> str:
        return self.school or self.department

    def refresh_status(self) -> str:
        """Status rules: Completed > Closed > Ongoing > Upcoming."""
        has_results = len(self.results) > 0
        today = date.today()
        deadline_passed = bool(self.registration_deadline and today > self.registration_deadline)

        if has_results:
            self.status = self.STATUS_COMPLETED
        elif self.registration_closed_manually or deadline_passed:
            self.status = self.STATUS_CLOSED
        elif today == self.date:
            self.status = self.STATUS_ONGOING
        elif today > self.date:
            self.status = self.STATUS_CLOSED
        else:
            self.status = self.STATUS_UPCOMING
        return self.status

    def can_accept_registration(self, current_count: int) -> tuple[bool, str]:
        status = self.refresh_status()
        if status == self.STATUS_COMPLETED:
            return False, "Event Completed"
        if status == self.STATUS_CLOSED:
            return False, "Registration Closed"
        if self.registration_closed_manually:
            return False, "Registration Closed"
        if self.registration_deadline and date.today() > self.registration_deadline:
            return False, "Registration Closed"
        if self.max_participants and current_count >= self.max_participants:
            return False, "Event Full"
        return True, "Open"


class Participant(db.Model):
    __tablename__ = "participants"

    id = db.Column(db.Integer, primary_key=True)
    # Links this profile to the logged-in participant account (unique — one profile per user)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    roll_number = db.Column(db.String(40), nullable=True, index=True)
    department = db.Column(db.String(120), nullable=True, index=True)
    organization = db.Column(db.String(180), nullable=True)
    whatsapp_number = db.Column(db.String(10), nullable=True, index=True)
    year = db.Column(db.SmallInteger, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="participant_profile")
    participations = db.relationship(
        "EventParticipation", back_populates="participant", cascade="all, delete-orphan"
    )
    results = db.relationship(
        "Result", back_populates="participant", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Participant {self.id} {self.name!r}>"


class CoordinatorProfile(db.Model):
    __tablename__ = "coordinator_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False, index=True)
    phone_number = db.Column(db.String(10), nullable=False, index=True)
    university = db.Column(db.String(180), nullable=False)
    university_outlook_email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    position = db.Column(db.String(120), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", foreign_keys=[user_id], back_populates="coordinator_profile")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


class ManagementProfile(db.Model):
    __tablename__ = "management_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False, index=True)
    phone_number = db.Column(db.String(10), nullable=False, index=True)
    university = db.Column(db.String(180), nullable=False)
    university_outlook_email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    position = db.Column(db.String(120), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", foreign_keys=[user_id], back_populates="management_profile")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


class EventParticipation(db.Model):
    __tablename__ = "event_participation"
    __table_args__ = (
        UniqueConstraint("event_id", "participant_id", name="uq_event_participant"),
        db.Index("ix_event_participation_event_id", "event_id"),
        db.Index("ix_event_participation_participant_id", "participant_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    is_external = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    event = db.relationship("Event", back_populates="participations")
    participant = db.relationship("Participant", back_populates="participations")


class Result(db.Model):
    __tablename__ = "results"
    __table_args__ = (
        UniqueConstraint("event_id", "participant_id", name="uq_result_event_participant"),
        db.Index("ix_results_event_id", "event_id"),
        db.Index("ix_results_participant_id", "participant_id"),
        db.Index("ix_results_rank", "rank"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    rank = db.Column(db.Integer, nullable=True)
    prize = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    event = db.relationship("Event", back_populates="results")
    participant = db.relationship("Participant", back_populates="results")


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"
    __table_args__ = (
        db.Index("ix_activity_logs_created_at", "created_at"),
        db.Index("ix_activity_logs_user_id", "user_id"),
        db.Index("ix_activity_logs_action", "action"),
    )

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(80), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    role = db.Column(db.String(32), nullable=True)
    details = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User")


class EmailOTP(db.Model):
    __tablename__ = "email_otps"
    __table_args__ = (
        db.Index("ix_email_otps_email", "email"),
        db.Index("ix_email_otps_purpose", "purpose"),
        db.Index("ix_email_otps_expires_at", "expires_at"),
    )

    PURPOSE_SIGNUP = "signup"
    PURPOSE_PASSWORD_RESET = "password_reset"
    PURPOSE_STAFF_CREATE = "staff_create"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False)
    purpose = db.Column(db.String(40), nullable=False)
    otp_code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def is_active(self) -> bool:
        return self.used_at is None and self.expires_at >= datetime.utcnow()
