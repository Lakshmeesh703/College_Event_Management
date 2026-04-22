"""
Generate realistic dummy data for demonstrations and load testing.
Creates at least 50 events and 200 participants by default.
"""
import random
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from ..app import app
from ..models import Event, EventParticipation, Participant, User, db

DEPARTMENTS = [
    "Computer Science",
    "Mechanical",
    "Civil",
    "Electronics",
    "Information Science",
]
SCHOOLS = [
    "School of Arts, Humanities and Social Sciences",
    "School of Management Sciences",
    "School of Mathematics and Natural Sciences",
    "School of Law, Governance and Public Policy",
    "School of Biosciences",
    "School of Engineering",
    "Chanakya Student Affairs",
]
CATEGORIES = ["technical", "cultural", "sports", "workshop", "other"]
VENUES = ["Main Auditorium", "Seminar Hall", "Sports Ground", "Lab Block", "Open Theater"]


def _random_date(start_days=0, end_days=240):
    return date.today() + timedelta(days=random.randint(start_days, end_days))


def _ensure_coordinator() -> User:
    email = "bulk.coordinator@demo.local"
    user = User.query.filter_by(email=email).first()
    if user:
        return user
    user = User(
        name="Bulk Coordinator",
        email=email,
        password_hash=generate_password_hash("coord12345"),
        role=User.ROLE_COORDINATOR,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _create_participants(count: int):
    created = 0
    for i in range(1, count + 1):
        email = f"student{i:03d}@demo.local"
        if User.query.filter_by(email=email).first():
            continue

        name = f"Student {i:03d}"
        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash("student12345"),
            role=User.ROLE_STUDENT,
        )
        db.session.add(user)
        db.session.flush()

        p = Participant(
            user_id=user.id,
            name=name,
            roll_number=f"CSE{i:03d}",
            department=random.choice(DEPARTMENTS),
            whatsapp_number=f"9{random.randint(100000000, 999999999)}",
            year=random.randint(1, 4),
        )
        db.session.add(p)
        created += 1

    db.session.commit()
    return created


def _create_events(count: int, coordinator_id: int):
    created = 0
    for i in range(1, count + 1):
        name = f"Event {i:03d}"
        if Event.query.filter_by(name=name).first():
            continue

        event_date = _random_date()
        deadline = event_date - timedelta(days=random.randint(1, 20))

        event = Event(
            name=name,
            school=random.choice(SCHOOLS),
            department=random.choice(DEPARTMENTS),
            category=random.choice(CATEGORIES),
            date=event_date,
            registration_deadline=deadline,
            max_participants=random.randint(60, 300),
            allow_external=bool(random.getrandbits(1)),
            venue=random.choice(VENUES),
            organizer="Department Committee",
            created_by_id=coordinator_id,
        )
        event.refresh_status()
        db.session.add(event)
        created += 1

    db.session.commit()
    return created


def _create_registrations(min_per_event=5, max_per_event=20):
    participants = Participant.query.all()
    events = Event.query.all()
    created = 0

    if not participants or not events:
        return 0

    for event in events:
        picks = random.sample(participants, k=min(len(participants), random.randint(min_per_event, max_per_event)))
        for p in picks:
            exists = EventParticipation.query.filter_by(event_id=event.id, participant_id=p.id).first()
            if exists:
                continue
            db.session.add(EventParticipation(event_id=event.id, participant_id=p.id, is_external=False))
            created += 1

    db.session.commit()
    return created


def main(events=50, participants=200):
    with app.app_context():
        coordinator = _ensure_coordinator()
        created_participants = _create_participants(participants)
        created_events = _create_events(events, coordinator.id)
        created_regs = _create_registrations()

        print("Dummy data generation complete")
        print(f"Participants created: {created_participants}")
        print(f"Events created: {created_events}")
        print(f"Registrations created: {created_regs}")


if __name__ == "__main__":
    main()
