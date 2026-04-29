"""
Event coordinator role: CRUD on own events, view registrants, enter results for own events.
"""
import csv
import io

from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for

from ..auth_decorators import ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT, roles_required
from ..models import Competition, CoordinatorProfile, Event, EventParticipation, Participant, Result, Team, TeamMember, User, db
from ..services.file_handler import save_brochure, delete_brochure
from ..services.activity_logger import log_action
from ..services.validation import clean_text, parse_int_in_range, parse_iso_date, parse_whatsapp_number
from ..scripts.data_processing import standardize_category, standardize_department

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


def _convener_school(user: User) -> str | None:
    if not user or user.role != User.ROLE_CONVENER:
        return None
    profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
    return profile.school if profile and profile.school else None


def _coordinator_allotment(user: User) -> tuple[int | None, int | None]:
    if not user or user.role != User.ROLE_COORDINATOR:
        return None, None
    profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
    if not profile:
        return None, None
    return profile.allotted_event_id, profile.allotted_competition_id


def _can_manage_event(event: Event, user_id: int) -> bool:
    """Own event, or legacy row with no creator (any coordinator may manage)."""
    user = db.session.get(User, user_id)
    if user and user.role in (User.ROLE_ADMIN, User.ROLE_MANAGEMENT):
        return True
    if user and user.role == User.ROLE_CONVENER:
        convener_school = _convener_school(user)
        return bool(convener_school and event.school == convener_school)
    if user and user.role == User.ROLE_COORDINATOR:
        allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
        if allotted_event_id:
            return event.id == allotted_event_id
        if allotted_competition_id:
            return Competition.query.filter_by(id=allotted_competition_id, event_id=event.id).first() is not None
    if event.created_by_id is None:
        return True
    return event.created_by_id == user_id


def _competition_registration_rows(event_id: int, competition: Competition):
    if competition.is_team_event:
        rows = (
            db.session.query(EventParticipation, Team, Participant)
            .join(Team, EventParticipation.team_id == Team.id)
            .join(Participant, EventParticipation.participant_id == Participant.id)
            .filter(
                EventParticipation.event_id == event_id,
                EventParticipation.competition_id == competition.id,
            )
            .order_by(Team.name.asc(), Participant.name.asc())
            .all()
        )
        return [
            {
                "participation": participation,
                "participant": captain,
                "team": team,
                "key_id": team.id,
                "label": team.name,
                "detail": f"Captain: {captain.name}" if captain else "",
            }
            for participation, team, captain in rows
        ]

    rows = (
        db.session.query(EventParticipation, Participant)
        .join(Participant, EventParticipation.participant_id == Participant.id)
        .filter(
            EventParticipation.event_id == event_id,
            EventParticipation.competition_id == competition.id,
        )
        .order_by(Participant.name.asc())
        .all()
    )
    return [
        {
            "participation": participation,
            "participant": participant,
            "team": None,
            "key_id": participant.id,
            "label": participant.name,
            "detail": participant.roll_number or "",
        }
        for participation, participant in rows
    ]


@bp.route("/dashboard")
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def dashboard():
    user = _current_user()
    convener_school = _convener_school(user)
    if user.role in (User.ROLE_ADMIN, User.ROLE_MANAGEMENT):
        my_events = Event.query.order_by(Event.date.desc()).all()
        unassigned_events = []
    elif user.role == User.ROLE_CONVENER:
        if not convener_school:
            flash("Convener account is missing assigned school. Contact admin.", "error")
            return redirect(url_for("auth.logout"))
        my_events = (
            Event.query.filter(Event.school == convener_school)
            .order_by(Event.date.desc())
            .all()
        )
        unassigned_events = []
    else:
        allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
        if allotted_event_id or allotted_competition_id:
            if allotted_event_id:
                my_events = Event.query.filter_by(id=allotted_event_id).order_by(Event.date.desc()).all()
            else:
                my_events = (
                    Event.query.join(Competition, Competition.event_id == Event.id)
                    .filter(Competition.id == allotted_competition_id)
                    .order_by(Event.date.desc())
                    .all()
                )
            unassigned_events = []
        else:
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
        convener_school=convener_school,
        can_create=user.role in (User.ROLE_CONVENER, User.ROLE_ADMIN, User.ROLE_MANAGEMENT),
    )


@bp.route("/events/add", methods=["GET", "POST"])
@roles_required(ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def add_event():
    user = _current_user()
    allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
    if user.role == User.ROLE_COORDINATOR and (allotted_event_id or allotted_competition_id):
        flash("You are allotted to a specific event/competition and cannot create new events.", "warning")
        return redirect(url_for("coordinator.dashboard"))

    convener_school = _convener_school(user)
    if user.role == User.ROLE_CONVENER and not convener_school:
        flash("Convener account is missing assigned school. Contact admin.", "error")
        return redirect(url_for("coordinator.dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        school = convener_school if user.role == User.ROLE_CONVENER else (request.form.get("school") or "").strip()
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

        # Handle brochure upload
        brochure_path = None
        if "brochure" in request.files:
            brochure_file = request.files["brochure"]
            if brochure_file and brochure_file.filename:
                brochure_path = save_brochure(brochure_file, prefix="event")
                if not brochure_path:
                    flash("Invalid brochure file. Please upload a PDF (max 10MB).", "error")
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
            brochure_path=brochure_path,
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
        flash(f"Event “{name}” created. Add sub-events/competitions below.", "success")
        return redirect(url_for("coordinator.event_participants", event_id=ev.id))

    return render_template(
        "coordinator/add_event.html",
        categories=ALLOWED_CATEGORIES,
        schools=ALLOWED_SCHOOLS,
        forced_school=convener_school if user.role == User.ROLE_CONVENER else None,
    )


@bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def edit_event(event_id: int):
    user = _current_user()
    convener_school = _convener_school(user)
    if user.role == User.ROLE_CONVENER and not convener_school:
        flash("Convener account is missing assigned school. Contact admin.", "error")
        return redirect(url_for("coordinator.dashboard"))

    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("You cannot edit this event.", "error")
        return redirect(url_for("coordinator.dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        school = convener_school if user.role == User.ROLE_CONVENER else (request.form.get("school") or "").strip()
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

        brochure_path = event.brochure_path
        if "brochure" in request.files:
            brochure_file = request.files["brochure"]
            if brochure_file and brochure_file.filename:
                new_brochure_path = save_brochure(brochure_file, prefix="event")
                if not new_brochure_path:
                    flash("Invalid brochure file. Please upload a PDF (max 10MB).", "error")
                    return redirect(url_for("coordinator.edit_event", event_id=event_id))
                brochure_path = new_brochure_path

        old_brochure_path = event.brochure_path
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
        event.brochure_path = brochure_path
        event.refresh_status()
        if event.created_by_id is None:
            event.created_by_id = user.id
        db.session.commit()
        if brochure_path and old_brochure_path and old_brochure_path != brochure_path:
            delete_brochure(old_brochure_path)
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
        forced_school=convener_school if user.role == User.ROLE_CONVENER else None,
    )


@bp.route("/events/<int:event_id>/toggle-registration", methods=["POST"])
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
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
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def delete_event(event_id: int):
    user = _current_user()
    if user.role == User.ROLE_COORDINATOR:
        flash("Coordinators are not allowed to delete events.", "error")
        return redirect(url_for("coordinator.dashboard"))

    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("You cannot delete this event.", "error")
        return redirect(url_for("coordinator.dashboard"))

    name = event.name
    if event.brochure_path:
        delete_brochure(event.brochure_path)
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
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def event_participants(event_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    participations = EventParticipation.query.filter_by(event_id=event_id).all()
    if user.role == User.ROLE_COORDINATOR:
        _allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
        if allotted_competition_id:
            participations = [ep for ep in participations if ep.competition_id == allotted_competition_id]

    rows = []
    for ep in participations:
        if ep.team_id:
            team = db.session.get(Team, ep.team_id)
            if team:
                rows.append(
                    {
                        "type": "team",
                        "team": team,
                        "participation": ep,
                        "members": team.members if team.members else [],
                    }
                )
        else:
            participant = db.session.get(Participant, ep.participant_id)
            if participant:
                rows.append(
                    {
                        "type": "participant",
                        "participant": participant,
                        "participation": ep,
                        "team": None,
                    }
                )
    competitions_query = (
        Competition.query.filter_by(event_id=event_id)
    )
    if user.role == User.ROLE_COORDINATOR:
        _allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
        if allotted_competition_id:
            competitions_query = competitions_query.filter(Competition.id == allotted_competition_id)
    competitions = competitions_query.order_by(Competition.date.asc(), Competition.created_at.asc()).all()
    event.refresh_status()
    db.session.commit()
    return render_template(
        "coordinator/event_participants.html",
        event=event,
        rows=rows,
        competitions=competitions,
    )


@bp.route("/events/<int:event_id>/competitions")
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def list_competitions(event_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    competitions_query = Competition.query.filter_by(event_id=event_id)
    if user.role == User.ROLE_COORDINATOR:
        _allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
        if allotted_competition_id:
            competitions_query = competitions_query.filter(Competition.id == allotted_competition_id)
    competitions = competitions_query.order_by(Competition.date.asc(), Competition.created_at.asc()).all()

    participations = EventParticipation.query.filter_by(event_id=event_id).all()
    if user.role == User.ROLE_COORDINATOR:
        _allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
        if allotted_competition_id:
            participations = [ep for ep in participations if ep.competition_id == allotted_competition_id]

    rows = []
    for ep in participations:
        if ep.team_id:
            team = db.session.get(Team, ep.team_id)
            if team:
                rows.append(
                    {
                        "type": "team",
                        "team": team,
                        "participation": ep,
                        "members": team.members if team.members else [],
                    }
                )
        else:
            participant = db.session.get(Participant, ep.participant_id)
            if participant:
                rows.append(
                    {
                        "type": "participant",
                        "participant": participant,
                        "participation": ep,
                        "team": None,
                    }
                )
    return render_template(
        "coordinator/event_participants.html",
        event=event,
        rows=rows,
        competitions=competitions,
    )


@bp.route("/events/<int:event_id>/competitions/create", methods=["POST"])
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def create_competition(event_id: int):
    user = _current_user()
    allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
    if user.role == User.ROLE_COORDINATOR and (allotted_event_id or allotted_competition_id):
        flash("You are allotted to a specific competition and cannot create competitions.", "warning")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    rules = (request.form.get("rules") or "").strip() or None
    date_raw = (request.form.get("date") or "").strip()
    max_participants_raw = (request.form.get("max_participants") or "").strip()
    is_team_event = request.form.get("is_team_event") == "on"
    min_team_size_raw = (request.form.get("min_team_size") or "").strip()
    max_team_size_raw = (request.form.get("max_team_size") or "").strip()

    try:
        name = clean_text(name, min_len=2, max_len=200)
        comp_date = parse_iso_date(date_raw)
        max_participants = parse_int_in_range(
            max_participants_raw,
            min_value=1,
            max_value=50000,
            required=False,
        )
        min_team_size = parse_int_in_range(
            min_team_size_raw,
            min_value=1,
            max_value=100,
            required=False,
        )
        max_team_size = parse_int_in_range(
            max_team_size_raw,
            min_value=1,
            max_value=100,
            required=False,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    if description:
        try:
            description = clean_text(description, min_len=2, max_len=4000)
        except ValueError as exc:
            flash(f"Description error: {exc}", "error")
            return redirect(url_for("coordinator.event_participants", event_id=event_id))

    if rules:
        try:
            rules = clean_text(rules, min_len=2, max_len=4000)
        except ValueError as exc:
            flash(f"Rules error: {exc}", "error")
            return redirect(url_for("coordinator.event_participants", event_id=event_id))

    if is_team_event:
        if min_team_size is None:
            flash("Team competitions need a minimum team size.", "error")
            return redirect(url_for("coordinator.event_participants", event_id=event_id))
        if max_team_size is None:
            flash("Team competitions need a maximum team size.", "error")
            return redirect(url_for("coordinator.event_participants", event_id=event_id))
        if min_team_size < 2:
            flash("Team competitions must allow at least 2 members.", "error")
            return redirect(url_for("coordinator.event_participants", event_id=event_id))
        if max_team_size < min_team_size:
            flash("Maximum team size must be greater than or equal to the minimum.", "error")
            return redirect(url_for("coordinator.event_participants", event_id=event_id))
    else:
        min_team_size = None
        max_team_size = None

    # Handle brochure upload
    brochure_path = None
    if "brochure" in request.files:
        brochure_file = request.files["brochure"]
        if brochure_file and brochure_file.filename:
            brochure_path = save_brochure(brochure_file, prefix="competition")
            if not brochure_path:
                flash("Invalid brochure file. Please upload a PDF (max 10MB).", "error")
                return redirect(url_for("coordinator.event_participants", event_id=event_id))

    comp = Competition(
        event_id=event_id,
        name=name,
        description=description,
        rules=rules,
        max_participants=max_participants,
        is_team_event=is_team_event,
        min_team_size=min_team_size,
        max_team_size=max_team_size,
        date=comp_date,
        brochure_path=brochure_path,
    )
    db.session.add(comp)
    db.session.commit()
    log_action(
        "competition_created",
        user_id=user.id,
        role=ROLE_COORDINATOR,
        details=f"event_id={event_id} competition_id={comp.id}",
    )
    flash("Competition created.", "success")
    return redirect(url_for("coordinator.event_participants", event_id=event_id))


@bp.route("/events/<int:event_id>/competitions/<int:competition_id>/edit", methods=["GET", "POST"])
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def edit_competition(event_id: int, competition_id: int):
    user = _current_user()
    _allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
    if user.role == User.ROLE_COORDINATOR and allotted_competition_id and competition_id != allotted_competition_id:
        flash("You can manage only your allotted competition.", "error")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    competition = Competition.query.filter_by(id=competition_id, event_id=event_id).first()
    if not competition:
        flash("Competition not found.", "error")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        rules = (request.form.get("rules") or "").strip() or None
        date_raw = (request.form.get("date") or "").strip()
        max_participants_raw = (request.form.get("max_participants") or "").strip()
        is_team_event = request.form.get("is_team_event") == "on"
        min_team_size_raw = (request.form.get("min_team_size") or "").strip()
        max_team_size_raw = (request.form.get("max_team_size") or "").strip()

        try:
            name = clean_text(name, min_len=2, max_len=200)
            comp_date = parse_iso_date(date_raw)
            max_participants = parse_int_in_range(max_participants_raw, min_value=1, max_value=50000, required=False)
            min_team_size = parse_int_in_range(min_team_size_raw, min_value=1, max_value=100, required=False)
            max_team_size = parse_int_in_range(max_team_size_raw, min_value=1, max_value=100, required=False)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("coordinator.edit_competition", event_id=event_id, competition_id=competition_id))

        if description:
            try:
                description = clean_text(description, min_len=2, max_len=4000)
            except ValueError as exc:
                flash(f"Description error: {exc}", "error")
                return redirect(url_for("coordinator.edit_competition", event_id=event_id, competition_id=competition_id))

        if rules:
            try:
                rules = clean_text(rules, min_len=2, max_len=4000)
            except ValueError as exc:
                flash(f"Rules error: {exc}", "error")
                return redirect(url_for("coordinator.edit_competition", event_id=event_id, competition_id=competition_id))

        if is_team_event:
            if min_team_size is None:
                flash("Team competitions need a minimum team size.", "error")
                return redirect(url_for("coordinator.edit_competition", event_id=event_id, competition_id=competition_id))
            if max_team_size is None:
                flash("Team competitions need a maximum team size.", "error")
                return redirect(url_for("coordinator.edit_competition", event_id=event_id, competition_id=competition_id))
            if min_team_size < 2:
                flash("Team competitions must allow at least 2 members.", "error")
                return redirect(url_for("coordinator.edit_competition", event_id=event_id, competition_id=competition_id))
            if max_team_size < min_team_size:
                flash("Maximum team size must be greater than or equal to the minimum.", "error")
                return redirect(url_for("coordinator.edit_competition", event_id=event_id, competition_id=competition_id))
        else:
            min_team_size = None
            max_team_size = None

        brochure_path = competition.brochure_path
        if "brochure" in request.files:
            brochure_file = request.files["brochure"]
            if brochure_file and brochure_file.filename:
                new_brochure_path = save_brochure(brochure_file, prefix="competition")
                if not new_brochure_path:
                    flash("Invalid brochure file. Please upload a PDF (max 10MB).", "error")
                    return redirect(url_for("coordinator.edit_competition", event_id=event_id, competition_id=competition_id))
                brochure_path = new_brochure_path

        old_brochure_path = competition.brochure_path
        competition.name = name
        competition.description = description
        competition.rules = rules
        competition.max_participants = max_participants
        competition.is_team_event = is_team_event
        competition.min_team_size = min_team_size
        competition.max_team_size = max_team_size
        competition.date = comp_date
        competition.brochure_path = brochure_path
        db.session.commit()
        if brochure_path and old_brochure_path and old_brochure_path != brochure_path:
            delete_brochure(old_brochure_path)
        log_action(
            "competition_updated",
            user_id=user.id,
            role=ROLE_COORDINATOR,
            details=f"event_id={event_id} competition_id={competition.id}",
        )
        flash("Competition updated.", "success")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    return render_template(
        "coordinator/edit_competition.html",
        event=event,
        competition=competition,
    )


@bp.route("/events/<int:event_id>/competitions/<int:competition_id>/delete", methods=["POST"])
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def delete_competition(event_id: int, competition_id: int):
    user = _current_user()
    _allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
    if user.role == User.ROLE_COORDINATOR and allotted_competition_id and competition_id != allotted_competition_id:
        flash("You can manage only your allotted competition.", "error")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    competition = Competition.query.filter_by(id=competition_id, event_id=event_id).first()
    if not competition:
        flash("Competition not found.", "error")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    if competition.brochure_path:
        delete_brochure(competition.brochure_path)
    db.session.delete(competition)
    db.session.commit()
    log_action(
        "competition_deleted",
        user_id=user.id,
        role=ROLE_COORDINATOR,
        details=f"event_id={event_id} competition_id={competition_id}",
    )
    flash("Competition deleted.", "success")
    return redirect(url_for("coordinator.event_participants", event_id=event_id))


@bp.route("/events/<int:event_id>/competitions/<int:competition_id>/export.csv", methods=["GET"])
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def export_competition_students_csv(event_id: int, competition_id: int):
    user = _current_user()
    _allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
    if user.role == User.ROLE_COORDINATOR and allotted_competition_id and competition_id != allotted_competition_id:
        flash("You can export only your allotted competition students.", "error")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    competition = Competition.query.filter_by(id=competition_id, event_id=event_id).first()
    if not competition:
        flash("Competition not found.", "error")
        return redirect(url_for("coordinator.event_participants", event_id=event_id))

    rows = _competition_registration_rows(event_id, competition)

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Competition Registered Students Report"])
    writer.writerow(["Event", event.name])
    writer.writerow(["Competition", competition.name])
    writer.writerow(["Competition Date", competition.date])
    writer.writerow(["School", event.school_or_department])
    writer.writerow(["Competition Type", "Team" if competition.is_team_event else "Individual"])
    writer.writerow(["Team Size Range", f"{competition.min_team_size or '—'} - {competition.max_team_size or '—'}"])
    writer.writerow(["Total Registered", len(rows)])
    writer.writerow([])
    if competition.is_team_event:
        writer.writerow([
            "Team Name",
            "Leader",
            "Member Name",
            "Roll Number",
            "Department",
            "University",
            "Email",
            "WhatsApp",
            "Registered On",
        ])
        for row in rows:
            team = row["team"]
            participation = row["participation"]
            if team and team.members:
                for idx, member in enumerate(team.members):
                    writer.writerow([
                        team.name if idx == 0 else "",
                        team.captain.name if (team.captain and idx == 0) else "",
                        member.name,
                        member.roll_number if member.roll_number else "",
                        member.department if member.department else "",
                        member.organization if member.organization else "",
                        member.email if member.email else "",
                        member.whatsapp_number if member.whatsapp_number else "",
                        participation.created_at.strftime("%Y-%m-%d %H:%M") if (participation.created_at and idx == 0) else "",
                    ])
            elif team:
                writer.writerow([
                    team.name,
                    team.captain.name if team.captain else "N/A",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    participation.created_at.strftime("%Y-%m-%d %H:%M") if participation.created_at else "",
                ])
    else:
        writer.writerow([
            "Participant Name",
            "Roll Number",
            "Department",
            "Email",
            "WhatsApp",
            "Year",
            "Organization",
            "Registered On",
            "External",
        ])
        for row in rows:
            participation = row["participation"]
            participant = row["participant"]
            writer.writerow([
                participant.name if participant else "N/A",
                participant.roll_number if participant and participant.roll_number else "",
                participant.department if participant and participant.department else "",
                participant.user.email if (participant and participant.user) else "",
                participant.whatsapp_number if participant and participant.whatsapp_number else "",
                participant.year if participant and participant.year is not None else "",
                participant.organization if participant and participant.organization else "",
                participation.created_at.strftime("%Y-%m-%d %H:%M") if participation.created_at else "",
                "Yes" if participation.is_external else "No",
            ])

    response = Response(out.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        f'attachment; filename="competition_{competition_id}_students.csv"'
    )
    return response


@bp.route("/events/<int:event_id>/participants/<int:participant_id>/edit", methods=["GET", "POST"])
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def edit_participant(event_id: int, participant_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    ep_query = EventParticipation.query.filter_by(event_id=event_id, participant_id=participant_id)
    if user.role == User.ROLE_COORDINATOR:
        _allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
        if allotted_competition_id:
            ep_query = ep_query.filter_by(competition_id=allotted_competition_id)
    ep = ep_query.first()
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
@roles_required(ROLE_COORDINATOR, ROLE_CONVENER, ROLE_ADMIN, ROLE_MANAGEMENT)
def event_results(event_id: int):
    user = _current_user()
    event = db.session.get(Event, event_id)
    if not event or not _can_manage_event(event, user.id):
        flash("Not allowed.", "error")
        return redirect(url_for("coordinator.dashboard"))

    competitions_query = Competition.query.filter_by(event_id=event_id)
    allotted_competition_id = None
    if user.role == User.ROLE_COORDINATOR:
        _allotted_event_id, allotted_competition_id = _coordinator_allotment(user)
        if allotted_competition_id:
            competitions_query = competitions_query.filter(Competition.id == allotted_competition_id)
    competitions = competitions_query.order_by(Competition.date.asc(), Competition.created_at.asc()).all()
    selected_competition_id = request.args.get("competition_id", type=int)
    if request.method == "POST":
        selected_competition_id = request.form.get("competition_id", type=int)

    if allotted_competition_id:
        selected_competition_id = allotted_competition_id

    if selected_competition_id is None and competitions:
        selected_competition_id = competitions[0].id

    selected_competition = None
    if selected_competition_id is not None:
        selected_competition = Competition.query.filter_by(id=selected_competition_id, event_id=event_id).first()

    regs = []
    existing_results = {}
    if selected_competition:
        regs = _competition_registration_rows(event_id, selected_competition)
        existing_results = {
            result.team_id if selected_competition.is_team_event else result.participant_id: result
            for result in Result.query.filter_by(event_id=event_id, competition_id=selected_competition.id).all()
        }

    if request.method == "POST":
        if not selected_competition:
            flash("Choose a valid competition.", "error")
            return redirect(url_for("coordinator.event_results", event_id=event_id))

        try:
            registration_id = int(request.form.get("registration_id", ""))
        except ValueError:
            flash("Choose a registration.", "error")
            return redirect(url_for("coordinator.event_results", event_id=event_id, competition_id=selected_competition.id))

        registration = next((row for row in regs if row["key_id"] == registration_id), None)
        if not registration:
            flash("Invalid registration for this competition.", "error")
            return redirect(url_for("coordinator.event_results", event_id=event_id, competition_id=selected_competition.id))

        rank_raw = request.form.get("rank", "").strip()
        prize = request.form.get("prize", "").strip() or None
        rank = None
        if rank_raw:
            try:
                rank = parse_int_in_range(rank_raw, min_value=1, max_value=10000, required=False)
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("coordinator.event_results", event_id=event_id, competition_id=selected_competition.id))

        participant_id = registration["participant"].id if registration["participant"] else None
        team_id = registration["team"].id if registration["team"] else None

        existing = Result.query.filter_by(
            event_id=event_id,
            participant_id=participant_id,
            competition_id=selected_competition.id,
            team_id=team_id,
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
                    competition_id=selected_competition.id,
                    team_id=team_id,
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
            details=f"event_id={event_id} competition_id={selected_competition.id} registration_id={registration_id} rank={rank}",
        )
        return redirect(url_for("coordinator.event_results", event_id=event_id, competition_id=selected_competition.id))

    return render_template(
        "coordinator/event_results.html",
        event=event,
        competitions=competitions,
        selected_competition=selected_competition,
        regs=regs,
        existing_results=existing_results,
    )
