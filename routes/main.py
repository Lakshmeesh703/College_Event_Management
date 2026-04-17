"""
Public pages, calendar, published results lookup, and redirects for old URLs.

Data entry is role-based: coordinators and participants use their own blueprints.
"""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func

from auth_decorators import ROLE_COORDINATOR, ROLE_MANAGEMENT, ROLE_PARTICIPANT, ROLE_STUDENT
from models import Event, EventParticipation, Participant, Result, User, db
from routes.auth import dashboard_url_for_role

bp = Blueprint("main", __name__)

ALLOWED_CATEGORIES = [
    ("technical", "Technical"),
    ("cultural", "Cultural"),
    ("sports", "Sports"),
    ("workshop", "Workshop"),
    ("other", "Other"),
]


@bp.route("/")
def index():
    """Send logged-in users to their role hub; guests use the single login page."""
    uid = session.get("user_id")
    if uid:
        user = db.session.get(User, uid)
        if user:
            return redirect(dashboard_url_for_role(user.role))
    return redirect(url_for("auth.login"))


def _legacy_redirect_coordinator():
    if not session.get("user_id"):
        return redirect(url_for("auth.login", next=request.url))
    if session.get("user_role") == ROLE_COORDINATOR:
        return None  # caller supplies target
    return redirect(url_for("auth.access_denied"))


# --- Old paths (bookmark-friendly): send users to the new RBAC routes ---

@bp.route("/events/add")
def add_event():
    """Legacy URL → coordinator event form (coordinators only)."""
    r = _legacy_redirect_coordinator()
    if r:
        return r
    return redirect(url_for("coordinator.add_event"))


@bp.route("/participants/add")
def add_participant():
    flash("Participant profiles are created when students sign up.", "warning")
    return redirect(url_for("auth.signup"))


@bp.route("/participation/add")
def register_participation():
    if not session.get("user_id"):
        return redirect(url_for("auth.login", next=request.url))
    if session.get("user_role") in (ROLE_STUDENT, ROLE_PARTICIPANT):
        return redirect(url_for("participant.events_list"))
    return redirect(url_for("auth.access_denied"))


@bp.route("/results/add")
def add_result():
    r = _legacy_redirect_coordinator()
    if r:
        return r
    flash("Pick an event from your dashboard, then use Results.", "success")
    return redirect(url_for("coordinator.dashboard"))


@bp.route("/calendar")
def calendar_view():
    """Public read-only calendar."""
    events = Event.query.order_by(Event.date.asc()).all()
    upcoming = []
    ongoing = []
    completed = []

    for e in events:
        status = e.refresh_status()
        if status == Event.STATUS_UPCOMING:
            upcoming.append(e)
        elif status == Event.STATUS_ONGOING:
            ongoing.append(e)
        else:
            completed.append(e)

    calendar_sections = [
        {"key": "upcoming", "title": "Upcoming Events", "events": upcoming},
        {"key": "ongoing", "title": "Ongoing Events", "events": ongoing},
        {"key": "completed", "title": "Completed Events", "events": completed},
    ]
    return render_template("calendar.html", calendar_sections=calendar_sections)


@bp.route("/lookup/results")
def lookup_results():
    """Published results (read-only, public)."""
    event_id = request.args.get("event_id", type=int)
    events = Event.query.order_by(Event.name).all()
    rows = []
    chosen = None
    if event_id:
        chosen = db.session.get(Event, event_id)
        if chosen:
            rows = (
                db.session.query(Result, Participant)
                .join(Participant, Result.participant_id == Participant.id)
                .filter(Result.event_id == event_id)
                .order_by(Result.rank.asc().nulls_last(), Participant.name)
                .all()
            )
    return render_template(
        "results_lookup.html", events=events, rows=rows, chosen=chosen
    )


@bp.route("/students/history")
def student_history():
    """Old URL: participants should use /participant/history (own data only)."""
    if session.get("user_role") in (ROLE_STUDENT, ROLE_PARTICIPANT):
        return redirect(url_for("participant.history"))
    roll = (request.args.get("roll") or "").strip().upper()
    rows = []
    if roll:
        p = (
            Participant.query.filter(
                func.upper(Participant.roll_number) == roll.upper()
            ).first()
        )
        if p:
            rows = (
                db.session.query(EventParticipation, Event)
                .join(Event, EventParticipation.event_id == Event.id)
                .filter(EventParticipation.participant_id == p.id)
                .order_by(Event.date.desc())
                .all()
            )
    else:
        p = None

    return render_template("student_history.html", roll=roll, participant=p, rows=rows)
