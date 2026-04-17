"""
Participant role: browse events, register for events, view own history.
"""
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import and_, func

from auth_decorators import ROLE_STUDENT, ROLE_PARTICIPANT, roles_required
from models import Event, EventParticipation, Participant, Result, db
from services.activity_logger import log_action

bp = Blueprint("participant", __name__, url_prefix="/participant")


def _profile_for_session(user_id: int) -> Participant | None:
    return Participant.query.filter_by(user_id=user_id).first()


def _optional_date(raw: str | None):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _event_counts_map() -> dict[int, int]:
    return {
        eid: cnt
        for eid, cnt in (
            db.session.query(EventParticipation.event_id, func.count(EventParticipation.id))
            .group_by(EventParticipation.event_id)
            .all()
        )
    }


def _registration_meta_for_events(profile: Participant, events: list[Event], event_counts: dict[int, int]):
    joined_ids = {
        ep.event_id
        for ep in EventParticipation.query.filter_by(participant_id=profile.id).all()
    }
    can_register_map = {}
    for ev in events:
        can_reg, reason = ev.can_accept_registration(event_counts.get(ev.id, 0))
        if profile.user and profile.user.is_external_user and not ev.allow_external:
            can_reg = False
            reason = "Registration Closed"
        can_register_map[ev.id] = {
            "allowed": can_reg,
            "reason": reason,
            "count": event_counts.get(ev.id, 0),
        }
    return joined_ids, can_register_map


def _refresh_all_event_statuses():
    for ev in Event.query.all():
        ev.refresh_status()
    db.session.commit()


@bp.route("/dashboard")
@roles_required(ROLE_STUDENT, ROLE_PARTICIPANT)
def dashboard():
    from flask import session

    _refresh_all_event_statuses()
    profile = _profile_for_session(session["user_id"])
    if not profile:
        flash("Your profile is missing. Please contact support.", "error")
        return redirect(url_for("main.index"))
    reg_count = EventParticipation.query.filter_by(participant_id=profile.id).count()
    all_events = Event.query.order_by(Event.date.asc()).all()
    event_counts = _event_counts_map()
    joined_ids, can_register_map = _registration_meta_for_events(profile, all_events, event_counts)

    upcoming_events = []
    ongoing_events = []
    completed_events = []
    for ev in all_events:
        if ev.status == Event.STATUS_COMPLETED:
            completed_events.append(ev)
        elif ev.status == Event.STATUS_ONGOING:
            ongoing_events.append(ev)
        else:
            upcoming_events.append(ev)

    return render_template(
        "participant/dashboard.html",
        profile=profile,
        registration_count=reg_count,
        upcoming_events=upcoming_events,
        ongoing_events=ongoing_events,
        completed_events=completed_events,
        joined_ids=joined_ids,
        can_register_map=can_register_map,
    )


@bp.route("/events")
@roles_required(ROLE_STUDENT, ROLE_PARTICIPANT)
def events_list():
    from flask import session

    _refresh_all_event_statuses()
    profile = _profile_for_session(session["user_id"])
    if not profile:
        flash("Profile missing.", "error")
        return redirect(url_for("participant.dashboard"))

    search = (request.args.get("search") or "").strip()
    category = (request.args.get("category") or "").strip()
    school = (request.args.get("school") or "").strip()
    status = (request.args.get("status") or "").strip()
    date_from = _optional_date(request.args.get("date_from", type=str))
    date_to = _optional_date(request.args.get("date_to", type=str))
    sort = (request.args.get("sort") or "latest").strip()
    page = request.args.get("page", default=1, type=int)
    per_page = 10

    query = Event.query
    if search:
        query = query.filter(Event.name.ilike(f"%{search}%"))
    if category:
        query = query.filter(Event.category == category)
    if school:
        query = query.filter(Event.school == school)
    if date_from:
        query = query.filter(Event.date >= date_from)
    if date_to:
        query = query.filter(Event.date <= date_to)
    if status:
        query = query.filter(Event.status == status)

    if sort == "popular":
        query = (
            query.outerjoin(EventParticipation, EventParticipation.event_id == Event.id)
            .group_by(Event.id)
            .order_by(func.count(EventParticipation.id).desc(), Event.date.desc())
        )
    else:
        query = query.order_by(Event.date.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    all_events = pagination.items
    event_counts = _event_counts_map()
    joined_ids, can_register_map = _registration_meta_for_events(profile, all_events, event_counts)

    for ev in all_events:
        ev.refresh_status()

    categories = [
        ("", "All"),
        ("technical", "Technical"),
        ("cultural", "Cultural"),
        ("sports", "Sports"),
        ("workshop", "Workshop"),
        ("other", "Other"),
    ]
    schools = [("", "All")] + [(s, s) for s in Event.ALLOWED_SCHOOLS]
    statuses = [
        ("", "All"),
        (Event.STATUS_UPCOMING, Event.STATUS_UPCOMING),
        (Event.STATUS_ONGOING, Event.STATUS_ONGOING),
        (Event.STATUS_CLOSED, Event.STATUS_CLOSED),
        (Event.STATUS_COMPLETED, Event.STATUS_COMPLETED),
    ]

    return render_template(
        "participant/events.html",
        events=all_events,
        joined_ids=joined_ids,
        can_register_map=can_register_map,
        profile=profile,
        categories=categories,
        schools=schools,
        statuses=statuses,
        pagination=pagination,
        filters={
            "search": search,
            "category": category,
            "school": school,
            "status": status,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "sort": sort,
        },
    )


@bp.route("/events/<int:event_id>/register", methods=["POST"])
@roles_required(ROLE_STUDENT, ROLE_PARTICIPANT)
def register_for_event(event_id: int):
    from flask import session

    profile = _profile_for_session(session["user_id"])
    if not profile:
        flash("Profile missing.", "error")
        return redirect(url_for("participant.dashboard"))

    event = db.session.get(Event, event_id)
    if not event:
        flash("Event not found.", "error")
        return redirect(url_for("participant.events_list"))

    exists = EventParticipation.query.filter_by(
        event_id=event_id, participant_id=profile.id
    ).first()
    if exists:
        flash("You are already registered for this event.", "warning")
        return redirect(url_for("participant.events_list"))

    event_count = EventParticipation.query.filter_by(event_id=event_id).count()
    can_reg, reason = event.can_accept_registration(event_count)
    if profile.user and profile.user.is_external_user and not event.allow_external:
        can_reg = False
        reason = "Registration Closed"
    if not can_reg:
        flash("Registration Closed or Results Announced", "warning")
        db.session.commit()
        return redirect(url_for("participant.events_list"))

    row = EventParticipation(
        event_id=event_id,
        participant_id=profile.id,
        is_external=bool(profile.user and profile.user.is_external_user),
    )
    db.session.add(row)
    db.session.commit()
    log_action(
        "event_registration",
        user_id=session.get("user_id"),
        role=ROLE_STUDENT,
        details=f"event_id={event_id} participant_id={profile.id}",
    )
    log_action(
        "notification_simulated",
        user_id=session.get("user_id"),
        role=ROLE_STUDENT,
        details=f"registration_confirmation event_id={event_id}",
    )
    flash("Registered Successfully", "success")
    return redirect(url_for("participant.events_list"))


@bp.route("/history")
@roles_required(ROLE_STUDENT, ROLE_PARTICIPANT)
def history():
    from flask import session

    _refresh_all_event_statuses()
    profile = _profile_for_session(session["user_id"])
    if not profile:
        return redirect(url_for("participant.dashboard"))

    rows = (
        db.session.query(EventParticipation, Event, Result)
        .join(Event, EventParticipation.event_id == Event.id)
        .outerjoin(
            Result,
            and_(
                Result.event_id == EventParticipation.event_id,
                Result.participant_id == EventParticipation.participant_id,
            ),
        )
        .filter(EventParticipation.participant_id == profile.id)
        .order_by(Event.date.desc())
        .all()
    )
    return render_template(
        "participant/history.html",
        profile=profile,
        rows=rows,
    )
