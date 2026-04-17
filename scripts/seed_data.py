"""
Sample events, users (RBAC), registrations, and results.

Run from project root:

  python scripts/seed_data.py

Requires DATABASE_URL / DB_* or USE_SQLITE_LOCAL in .env.
Re-running on the same DB will skip if demo users already exist.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"))

from werkzeug.security import generate_password_hash

from app import app
from models import Event, EventParticipation, Participant, Result, User, db


def seed():
    with app.app_context():
        if User.query.filter_by(email="coordinator@demo.local").first():
            print("Demo users already exist — skipping seed. Delete DB to re-seed.")
            return

        coord = User(
            name="Demo Coordinator",
            email="coordinator@demo.local",
            password_hash=generate_password_hash("coord12345"),
            role=User.ROLE_COORDINATOR,
        )
        mgmt = User(
            name="Demo Management",
            email="management@demo.local",
            password_hash=generate_password_hash("admin12345"),
            role=User.ROLE_MANAGEMENT,
        )
        db.session.add_all([coord, mgmt])
        db.session.flush()

        u_rahul = User(
            name="Rahul Kumar",
            email="rahul@demo.local",
            password_hash=generate_password_hash("student12345"),
            role=User.ROLE_STUDENT,
        )
        u_sneha = User(
            name="Sneha Desai",
            email="sneha@demo.local",
            password_hash=generate_password_hash("student12345"),
            role=User.ROLE_STUDENT,
        )
        u_external = User(
            name="External Delegate",
            email="external@demo.local",
            password_hash=generate_password_hash("student12345"),
            role=User.ROLE_STUDENT,
            is_external_user=True,
        )
        db.session.add_all([u_rahul, u_sneha, u_external])
        db.session.flush()

        base_deadline = date.today() + timedelta(days=10)

        ev_samyuthi = Event(
            name="Samyuthi — Inter-college Cultural Gala",
            school="School of Arts, Humanities and Social Sciences",
            department="Computer Science",
            category="cultural",
            date=date(2026, 3, 15),
            registration_deadline=base_deadline,
            max_participants=300,
            allow_external=True,
            venue="Open Air Stage",
            organizer="Dr. Meera Nair",
            created_by_id=coord.id,
        )
        ev_ojas = Event(
            name="Ojas — Annual Tech Fest Hackathon",
            school="School of Engineering",
            department="Computer Science",
            category="technical",
            date=date(2026, 2, 10),
            registration_deadline=base_deadline,
            max_participants=120,
            allow_external=False,
            venue="Main Seminar Hall",
            organizer="Prof. Arjun Rao",
            created_by_id=coord.id,
        )
        ev_sports = Event(
            name="Inter-Department Cricket",
            school="School of Engineering",
            department="Mechanical",
            category="sports",
            date=date(2026, 1, 22),
            registration_deadline=base_deadline,
            max_participants=80,
            allow_external=False,
            venue="Sports Ground",
            organizer="Mr. Vikram Shetty",
            created_by_id=coord.id,
        )
        ev_ws = Event(
            name="Data Visualization Workshop",
            school="School of Mathematics and Natural Sciences",
            department="Information Science",
            category="workshop",
            date=date(2026, 4, 5),
            registration_deadline=base_deadline,
            max_participants=150,
            allow_external=True,
            venue="Lab 3",
            organizer="Ms. Ananya Kulkarni",
            created_by_id=coord.id,
        )
        db.session.add_all([ev_samyuthi, ev_ojas, ev_sports, ev_ws])
        db.session.flush()

        p1 = Participant(
            user_id=u_rahul.id,
            name="Rahul Kumar",
            roll_number="1DA21CS045",
            department="Computer Science",
            whatsapp_number="9876543210",
            year=3,
        )
        p2 = Participant(
            user_id=u_sneha.id,
            name="Sneha Desai",
            roll_number="1DA22IS012",
            department="Information Science",
            whatsapp_number="9123456780",
            year=2,
        )
        p3 = Participant(
            user_id=u_external.id,
            name="External Guest Speaker",
            roll_number=None,
            department=None,
            organization="Global Tech Forum",
            whatsapp_number="9012345678",
            year=None,
        )
        p4 = Participant(
            user_id=None,
            name="Vikram Patil",
            roll_number="1DA21ME033",
            department="Mechanical",
            whatsapp_number="9988776655",
            year=3,
        )
        db.session.add_all([p1, p2, p3, p4])
        db.session.flush()

        regs = [
            EventParticipation(
                event_id=ev_ojas.id, participant_id=p1.id, is_external=False
            ),
            EventParticipation(
                event_id=ev_ojas.id, participant_id=p2.id, is_external=False
            ),
            EventParticipation(
                event_id=ev_samyuthi.id, participant_id=p1.id, is_external=False
            ),
            EventParticipation(
                event_id=ev_samyuthi.id, participant_id=p2.id, is_external=False
            ),
            EventParticipation(
                event_id=ev_samyuthi.id, participant_id=p3.id, is_external=True
            ),
            EventParticipation(
                event_id=ev_sports.id, participant_id=p4.id, is_external=False
            ),
            EventParticipation(
                event_id=ev_ws.id, participant_id=p2.id, is_external=False
            ),
        ]
        db.session.add_all(regs)

        results = [
            Result(event_id=ev_ojas.id, participant_id=p1.id, rank=1, prize="₹10,000"),
            Result(event_id=ev_ojas.id, participant_id=p2.id, rank=2, prize="₹5,000"),
            Result(event_id=ev_sports.id, participant_id=p4.id, rank=1, prize="Trophy"),
            Result(
                event_id=ev_ws.id,
                participant_id=p2.id,
                rank=None,
                prize="Certificate of completion",
            ),
        ]
        db.session.add_all(results)

        for ev in [ev_samyuthi, ev_ojas, ev_sports, ev_ws]:
            ev.refresh_status()

        db.session.commit()
        print("Seed data inserted.")
        print("  Coordinator: coordinator@demo.local / coord12345")
        print("  Management:  management@demo.local / admin12345")
        print("  Students:    rahul@demo.local, sneha@demo.local / student12345")


if __name__ == "__main__":
    seed()
