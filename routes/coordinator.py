"""
Event coordinator role: CRUD on own events, view registrants, enter results for own events.
"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from auth_decorators import ROLE_COORDINATOR, roles_required
from models import Event, EventParticipation, Participant, Result, User, db
from services.activity_logger import log_action
from services.validation import clean_text, parse_int_in_range, parse_iso_date, parse_whatsapp_number
from scripts.data_processing import standardize_category, standardize_department

bp = Blueprint("coordinator", __name__, url_prefix="/coordinator")

ALLOWED_CATEGORIES = [
    ("technical", "Technical"),
    ("cultural", "Cultural"),
    ("sports", "Sports"),
    ("workshop", "Workshop"),
    ("other", "Other"),
]

ALLOWED_SCHOOLS = [(s, s) for s in Event.ALLOWED_SCHOOLS]


def _current_user() -> User:
    return db.session.get(User, session["user_id"])


def _can_manage_event(event: Event, user_id: int) -> bool:
    """Own event, or legacy row with no creator (any coordinator may manage)."""
    if event.created_by_id is None:
        return True
    return event.created_by_id == user_id


@bp.route("/dashboard")
@roles_required(ROLE_COORDINATOR)
def dashboard():
    user = _current_user()
    my_events = (
        Event.query.filter_by(created_by_id=user.id)
        .order_by(Event.date.desc())
        .all()
    )
    # Older rows before RBAC: any coordinator can manage until someone saves the event
    unassigned_events = (
        Event.query.filter(Event.created_by_id.is_(None))
        .order_by(Event.date.desc())
        .all()
    )
    for ev in my_events + unassigned_events:
        ev.refresh_status()
    db.session.commit()
    return render_template(
        "coordinator/dashboard.html",
        events=my_events,
        unassigned_events=unassigned_events,
        user=user,
    )


@bp.route("/events/add", methods=["GET", "POST"])
@roles_required(ROLE_COORDINATOR)
def add_event():
    user = _current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        school = (request.form.get("school") or "").strip()
        department = standardize_department(request.form.get("department"))
        category = standardize_category(request.form.get("category"))
        date_str = request.form.get("date", "").strip()
        deadline_str = request.form.get("registration_deadline", "").strip()
        max_participants_raw = request.form.get("max_participants", "").strip()
        allow_external = request.form.get("allow_external") == "on"
        venue = request.form.get("venue", "").strip()
        organizer = request.form.get("organizer", "").strip()

        try:
            name = clean_text(name, min_len=3, max_len=200)
            venue = clean_text(venue, min_len=2, max_len=200)
            organizer = clean_text(organizer, min_len=2, max_len=120)
            event_date = parse_iso_date(date_str)
            registration_deadline = parse_iso_date(deadline_str)
            max_participants = parse_int_in_range(max_participants_raw, min_value=1, max_value=50000, required=False)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("coordinator.add_event"))

        if school not in Event.ALLOWED_SCHOOLS:
            flash("Please choose a valid school.", "error")
            return redirect(url_for("coordinator.add_event"))

        if not all([department, category]):
            flash("Department and category are required.", "error")
            return redirect(url_for("coordinator.add_event"))

        if registration_deadline > event_date:
            flash("Registration deadline cannot be after event date.", "error")
            return redirect(url_for("coordinator.add_event"))

        ev = Event(
            name=name,
            school=school,
            department=department,
            category=category,
            date=event_date,
            registration_deadline=registration_deadline,
            max_participants=max_participants,
            allow_external=allow_external,
            venue=venue,
            organizer=organizer,
            created_by_id=user.id,
        )
        ev.refresh_status()
        db.session.add(ev)
        db.session.commit()
        log_action(
            "event_created",
            user_id=user.id,
            role=ROLE_COORDINATOR,
            details=f"event_id={ev.id} name={name}",
        )
        flash(f"Event “{name}” created.", "success")
        return redirect(url_for("coordinator.dashboard"))

    return render_template(
        "coordinator/add_event.html",
        categories=ALLOWED_CATEGORIES,
        schools=ALLOWED_SCHOOLS,
    )


@bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@roles_required(ROLE_COORDINATOR)
def edit_event(event_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("You cannot edit this event.", "error")
        return redirect(url_for("coordinator.dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        school = (request.form.get("school") or "").strip()
        department = standardize_department(request.form.get("department"))
        category = standardize_category(request.form.get("category"))
        date_str = request.form.get("date", "").strip()
        deadline_str = request.form.get("registration_deadline", "").strip()
        max_participants_raw = request.form.get("max_participants", "").strip()
        allow_external = request.form.get("allow_external") == "on"
        registration_closed_manually = request.form.get("registration_closed_manually") == "on"
        venue = request.form.get("venue", "").strip()
        organizer = request.form.get("organizer", "").strip()

        try:
            name = clean_text(name, min_len=3, max_len=200)
            venue = clean_text(venue, min_len=2, max_len=200)
            organizer = clean_text(organizer, min_len=2, max_len=120)
            event_date = parse_iso_date(date_str)
            registration_deadline = parse_iso_date(deadline_str)
            max_participants = parse_int_in_range(max_participants_raw, min_value=1, max_value=50000, required=False)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("coordinator.edit_event", event_id=event_id))

        if school not in Event.ALLOWED_SCHOOLS:
            flash("Please choose a valid school.", "error")
            return redirect(url_for("coordinator.edit_event", event_id=event_id))

        if not all([department, category]):
            flash("Department and category are required.", "error")
            return redirect(url_for("coordinator.edit_event", event_id=event_id))

        if registration_deadline > event_date:
            flash("Registration deadline cannot be after event date.", "error")
            return redirect(url_for("coordinator.edit_event", event_id=event_id))

        event.name = name
        event.school = school
        event.department = department
        event.category = category
        event.date = event_date
        event.registration_deadline = registration_deadline
        event.max_participants = max_participants
        event.allow_external = allow_external
        event.registration_closed_manually = registration_closed_manually
        event.venue = venue
        event.organizer = organizer
        event.refresh_status()
        if event.created_by_id is None:
            event.created_by_id = user.id
        db.session.commit()
        log_action(
            "event_updated",
            user_id=user.id,
            role=ROLE_COORDINATOR,
            details=f"event_id={event.id} name={event.name}",
        )
        flash("Event updated.", "success")
        return redirect(url_for("coordinator.dashboard"))

    return render_template(
        "coordinator/edit_event.html",
        event=event,
        categories=ALLOWED_CATEGORIES,
        schools=ALLOWED_SCHOOLS,
    )


@bp.route("/events/<int:event_id>/toggle-registration", methods=["POST"])
@roles_required(ROLE_COORDINATOR)
def toggle_registration(event_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("You cannot manage this event.", "error")
        return redirect(url_for("coordinator.dashboard"))

    if event.status == Event.STATUS_COMPLETED:
        flash("Completed events cannot reopen registration.", "warning")
        return redirect(url_for("coordinator.dashboard"))

    event.registration_closed_manually = not bool(event.registration_closed_manually)
    event.refresh_status()
    db.session.commit()
    flash(f"Registration status updated to: {event.status}", "success")
    return redirect(url_for("coordinator.dashboard"))


@bp.route("/events/<int:event_id>/delete", methods=["POST"])
@roles_required(ROLE_COORDINATOR)
def delete_event(event_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("You cannot delete this event.", "error")
        return redirect(url_for("coordinator.dashboard"))

    name = event.name
    db.session.delete(event)
    db.session.commit()
    log_action(
        "event_deleted",
        user_id=user.id,
        role=ROLE_COORDINATOR,
        details=f"event_id={event_id} name={name}",
    )
    flash(f"Deleted event “{name}”.", "success")
    return redirect(url_for("coordinator.dashboard"))


@bp.route("/events/<int:event_id>/participants")
@roles_required(ROLE_COORDINATOR)
def event_participants(event_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    rows = (
        db.session.query(EventParticipation, Participant)
        .join(Participant, EventParticipation.participant_id == Participant.id)
        .filter(EventParticipation.event_id == event_id)
        .order_by(Participant.name)
        .all()
    )
    event.refresh_status()
    db.session.commit()
    return render_template(
        "coordinator/event_participants.html",
        event=event,
        rows=rows,
    )


@bp.route("/events/<int:event_id>/participants/<int:participant_id>/edit", methods=["GET", "POST"])
@roles_required(ROLE_COORDINATOR)
def edit_participant(event_id: int, participant_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    ep = EventParticipation.query.filter_by(event_id=event_id, participant_id=participant_id).first()
    participant = db.session.get(Participant, participant_id)
    if not ep or not participant:
        flash("Participant record not found for this event.", "error")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        department = (request.form.get("department") or "").strip()
        whatsapp_raw = (request.form.get("whatsapp_number") or "").strip()
        organization = (request.form.get("organization") or "").strip() or None
        is_external = request.form.get("is_external") == "on"

        try:
            participant.name = clean_text(name, min_len=2, max_len=120)
            participant.whatsapp_number = parse_whatsapp_number(whatsapp_raw, required=True)
            participant.department = standardize_department(department) if department else None
            participant.organization = organization
            ep.is_external = is_external
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("coordinator.edit_participant", event_id=event_id, participant_id=participant_id))

        db.session.commit()
        log_action(
            "participant_updated",
            user_id=user.id,
            role=ROLE_COORDINATOR,
            details=f"event_id={event_id} participant_id={participant_id}",
        )
        flash("Details Updated", "success")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    return render_template(
        "coordinator/edit_participant.html",
        event=event,
        participant=participant,
        participation=ep,
    )


@bp.route("/events/<int:event_id>/results", methods=["GET", "POST"])
@roles_required(ROLE_COORDINATOR)
def event_results(event_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    # Participants registered for this event only
    regs = (
        db.session.query(EventParticipation, Participant)
        .join(Participant, EventParticipation.participant_id == Participant.id)
        .filter(EventParticipation.event_id == event_id)
        .order_by(Participant.name)
        .all()
    )

    if request.method == "POST":
        try:
            participant_id = int(request.form.get("participant_id", ""))
        except ValueError:
            flash("Choose a participant.", "error")
            return redirect(url_for("coordinator.event_results", event_id=event_id))

        if not any(p.id == participant_id for _, p in regs):
            flash("Invalid participant for this event.", "error")
            return redirect(url_for("coordinator.event_results", event_id=event_id))

        rank_raw = request.form.get("rank", "").strip()
        prize = request.form.get("prize", "").strip() or None
        rank = None
        if rank_raw:
            try:
                rank = parse_int_in_range(rank_raw, min_value=1, max_value=10000, required=False)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("coordinator.event_results", event_id=event_id))

        existing = Result.query.filter_by(
            event_id=event_id, participant_id=participant_id
        ).first()
        if existing:
            existing.rank = rank
            existing.prize = prize
            flash("Result updated.", "success")
        else:
            db.session.add(
                Result(
                    event_id=event_id,
                    participant_id=participant_id,
                    rank=rank,
                    prize=prize,
                )
            )
            flash("Result saved.", "success")
        event.refresh_status()
        db.session.commit()
        log_action(
            "result_saved",
            user_id=user.id,
            role=ROLE_COORDINATOR,
            details=f"event_id={event_id} participant_id={participant_id} rank={rank}",
        )
        return redirect(url_for("coordinator.event_results", event_id=event_id))

    existing_results = {
        r.participant_id: r
        for r in Result.query.filter_by(event_id=event_id).all()
    }
    return render_template(
        "coordinator/event_results.html",
        event=event,
        regs=regs,
        existing_results=existing_results,
    )
