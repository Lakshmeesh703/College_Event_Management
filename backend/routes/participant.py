"""
Participant role: browse events, register for events, view own history.
"""
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import and_, func, or_

from ..auth_decorators import ROLE_STUDENT, ROLE_PARTICIPANT, roles_required
from ..models import Competition, Event, EventParticipation, Participant, Result, Team, TeamMember, db
from ..services.activity_logger import log_action
from ..services.validation import clean_text, parse_int_in_range, parse_whatsapp_number

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
            db.session.query(EventParticipation.event_id, func.count(func.distinct(EventParticipation.participant_id)))
            .group_by(EventParticipation.event_id)
            .all()
        )
    }


def _competition_counts_map(event_ids: list[int]) -> dict[int, int]:
    if not event_ids:
        return {}
    return {
        cid: cnt
        for cid, cnt in (
            db.session.query(EventParticipation.competition_id, func.count(EventParticipation.id))
            .filter(
                EventParticipation.event_id.in_(event_ids),
                EventParticipation.competition_id.isnot(None),
            )
            .group_by(EventParticipation.competition_id)
            .all()
        )
        if cid is not None
    }


def _event_competitions_map(event_ids: list[int]) -> dict[int, list[Competition]]:
    out = {eid: [] for eid in event_ids}
    if not event_ids:
        return out
    rows = (
        Competition.query.filter(Competition.event_id.in_(event_ids))
        .order_by(Competition.date.asc(), Competition.created_at.asc())
        .all()
    )
    for c in rows:
        out.setdefault(c.event_id, []).append(c)
    return out


def _team_member_rows_from_form(profile: Participant, form) -> list[dict[str, str | None]]:
    names = form.getlist("team_member_name[]")
    rolls = form.getlist("team_member_roll_number[]")
    departments = form.getlist("team_member_department[]")
    organizations = form.getlist("team_member_organization[]")
    emails = form.getlist("team_member_email[]")
    whatsapp_numbers = form.getlist("team_member_whatsapp_number[]")

    members = [
        {
            "participant_id": profile.id,
            "name": profile.name,
            "roll_number": profile.roll_number,
            "department": profile.department,
            "organization": profile.organization,
            "email": profile.user.email if profile.user else profile.university_mail,
            "whatsapp_number": profile.whatsapp_number,
            "is_leader": True,
        }
    ]

    for index, raw_name in enumerate(names):
        raw_name = (raw_name or "").strip()
        raw_roll = (rolls[index] if index < len(rolls) else "").strip()
        raw_department = (departments[index] if index < len(departments) else "").strip()
        raw_organization = (organizations[index] if index < len(organizations) else "").strip()
        raw_email = (emails[index] if index < len(emails) else "").strip()
        raw_whatsapp = (whatsapp_numbers[index] if index < len(whatsapp_numbers) else "").strip()

        if not any([raw_name, raw_roll, raw_department, raw_organization, raw_email, raw_whatsapp]):
            continue

        if not raw_name:
            raise ValueError(f"Member {index + 1}: Name is required.")
        if not raw_roll:
            raise ValueError(f"Member {index + 1}: Roll number is required.")
        if not raw_department:
            raise ValueError(f"Member {index + 1}: Department is required.")
        if not raw_organization:
            raise ValueError(f"Member {index + 1}: University is required.")
        if not raw_email:
            raise ValueError(f"Member {index + 1}: Email is required.")
        if not raw_whatsapp:
            raise ValueError(f"Member {index + 1}: WhatsApp number is required.")

        name = clean_text(raw_name, min_len=2, max_len=120)
        email = raw_email if "@" in raw_email else None
        if not email or len(email) > 255:
            raise ValueError(f"Member {index + 1}: Valid email is required.")
        whatsapp_number = parse_whatsapp_number(raw_whatsapp, required=True)

        members.append(
            {
                "participant_id": None,
                "name": name,
                "roll_number": raw_roll,
                "department": raw_department,
                "organization": raw_organization,
                "email": email,
                "whatsapp_number": whatsapp_number,
                "is_leader": False,
            }
        )

    return members


def _registration_meta_for_events(
    profile: Participant,
    events: list[Event],
    event_counts: dict[int, int],
    competition_counts: dict[int, int],
    competitions_map: dict[int, list[Competition]],
):
    registrations = EventParticipation.query.filter_by(participant_id=profile.id).all()
    joined_event_ids = {ep.event_id for ep in registrations}
    joined_competition_ids = {ep.competition_id for ep in registrations if ep.competition_id is not None}

    can_register_event_map = {}
    can_register_competition_map = {}
    for ev in events:
        can_reg, reason = ev.can_accept_registration(event_counts.get(ev.id, 0))
        if profile.user and profile.user.is_external_user and not ev.allow_external:
            can_reg = False
            reason = "Registration Closed"
        can_register_event_map[ev.id] = {
            "allowed": can_reg,
            "reason": reason,
            "count": event_counts.get(ev.id, 0),
        }

        for comp in competitions_map.get(ev.id, []):
            comp_allowed = can_reg
            comp_reason = reason
            comp_count = competition_counts.get(comp.id, 0)
            cap = comp.max_participants or ev.max_participants
            if comp_allowed and cap and comp_count >= cap:
                comp_allowed = False
                comp_reason = "Competition Full"
            can_register_competition_map[comp.id] = {
                "allowed": comp_allowed,
                "reason": comp_reason,
                "count": comp_count,
                "cap": cap,
            }

    return joined_event_ids, joined_competition_ids, can_register_event_map, can_register_competition_map


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
    event_ids = [e.id for e in all_events]
    competition_counts = _competition_counts_map(event_ids)
    competitions_map = _event_competitions_map(event_ids)
    joined_event_ids, joined_competition_ids, can_register_map, can_register_comp_map = _registration_meta_for_events(
        profile,
        all_events,
        event_counts,
        competition_counts,
        competitions_map,
    )

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
        joined_ids=joined_event_ids,
        joined_competition_ids=joined_competition_ids,
        competitions_map=competitions_map,
        can_register_map=can_register_map,
        can_register_comp_map=can_register_comp_map,
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
    else:
        query = query.filter(Event.status.in_([Event.STATUS_UPCOMING, Event.STATUS_ONGOING]))

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
    event_ids = [e.id for e in all_events]
    competition_counts = _competition_counts_map(event_ids)
    competitions_map = _event_competitions_map(event_ids)
    joined_event_ids, joined_competition_ids, can_register_map, can_register_comp_map = _registration_meta_for_events(
        profile,
        all_events,
        event_counts,
        competition_counts,
        competitions_map,
    )

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
    ]

    return render_template(
        "participant/events.html",
        events=all_events,
        joined_ids=joined_event_ids,
        joined_competition_ids=joined_competition_ids,
        competitions_map=competitions_map,
        can_register_map=can_register_map,
        can_register_comp_map=can_register_comp_map,
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
    flash("Please register for a competition under this event.", "warning")
    return redirect(url_for("participant.events_list"))


@bp.route("/events/<int:event_id>/competitions/<int:competition_id>/register", methods=["POST"])
@roles_required(ROLE_STUDENT, ROLE_PARTICIPANT)
def register_for_competition(event_id: int, competition_id: int):
    from flask import session

    profile = _profile_for_session(session["user_id"])
    if not profile:
        flash("Profile missing.", "error")
        return redirect(url_for("participant.dashboard"))

    event = db.session.get(Event, event_id)
    if not event:
        flash("Event not found.", "error")
        return redirect(url_for("participant.events_list"))

    competition = Competition.query.filter_by(id=competition_id, event_id=event_id).first()
    if not competition:
        flash("Competition not found for this event.", "error")
        return redirect(url_for("participant.events_list"))

    exists = EventParticipation.query.filter_by(
        event_id=event_id, participant_id=profile.id, competition_id=competition_id
    ).first()
    if exists:
        flash("You are already registered for this competition.", "warning")
        return redirect(url_for("participant.events_list"))

    event_count = (
        db.session.query(func.count(func.distinct(EventParticipation.participant_id)))
        .filter(EventParticipation.event_id == event_id)
        .scalar()
        or 0
    )
    can_reg, reason = event.can_accept_registration(event_count)
    if profile.user and profile.user.is_external_user and not event.allow_external:
        can_reg = False
        reason = "Registration Closed"
    if not can_reg:
        flash("Registration Closed or Results Announced", "warning")
        db.session.commit()
        return redirect(url_for("participant.events_list"))

    competition_count = EventParticipation.query.filter_by(
        event_id=event_id,
        competition_id=competition_id,
    ).count()
    competition_cap = competition.max_participants or event.max_participants
    if competition_cap and competition_count >= competition_cap:
        flash("Competition is full.", "warning")
        return redirect(url_for("participant.events_list"))

    if not competition.is_team_event:
        row = EventParticipation(
            event_id=event_id,
            participant_id=profile.id,
            competition_id=competition_id,
            is_external=bool(profile.user and profile.user.is_external_user),
        )
        db.session.add(row)
        db.session.commit()
        log_action(
            "competition_registration",
            user_id=session.get("user_id"),
            role=ROLE_STUDENT,
            details=f"event_id={event_id} competition_id={competition_id} participant_id={profile.id}",
        )
        log_action(
            "notification_simulated",
            user_id=session.get("user_id"),
            role=ROLE_STUDENT,
            details=f"registration_confirmation event_id={event_id} competition_id={competition_id}",
        )
        flash("Competition registered successfully.", "success")
        return redirect(url_for("participant.events_list"))

    try:
        team_name = clean_text(request.form.get("team_name"), min_len=2, max_len=160)
        min_team_size = competition.min_team_size or 2
        max_team_size = competition.max_team_size or 100
        team_members = _team_member_rows_from_form(profile, request.form)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("participant.events_list"))

    if len(team_members) < min_team_size:
        flash(f"This competition needs at least {min_team_size} team members.", "error")
        return redirect(url_for("participant.events_list"))
    if len(team_members) > max_team_size:
        flash(f"This competition allows at most {max_team_size} team members.", "error")
        return redirect(url_for("participant.events_list"))

    if Team.query.filter_by(competition_id=competition_id, name=team_name).first():
        flash("A team with this name already exists for the competition.", "warning")
        return redirect(url_for("participant.events_list"))

    team = Team(
        event_id=event_id,
        competition_id=competition_id,
        captain_participant_id=profile.id,
        name=team_name,
    )
    db.session.add(team)
    db.session.flush()

    for index, member in enumerate(team_members, start=1):
        db.session.add(
            TeamMember(
                team_id=team.id,
                participant_id=member["participant_id"],
                member_order=index,
                is_leader=bool(member["is_leader"]),
                name=member["name"],
                roll_number=member["roll_number"],
                department=member["department"],
                organization=member["organization"],
                email=member["email"],
                whatsapp_number=member["whatsapp_number"],
            )
        )

    row = EventParticipation(
        event_id=event_id,
        participant_id=profile.id,
        competition_id=competition_id,
        team_id=team.id,
        is_external=bool(profile.user and profile.user.is_external_user),
    )
    db.session.add(row)
    db.session.commit()
    log_action(
        "competition_registration",
        user_id=session.get("user_id"),
        role=ROLE_STUDENT,
        details=f"event_id={event_id} competition_id={competition_id} team_id={team.id} team_name={team.name}",
    )
    log_action(
        "notification_simulated",
        user_id=session.get("user_id"),
        role=ROLE_STUDENT,
        details=f"registration_confirmation event_id={event_id} competition_id={competition_id} team_id={team.id}",
    )
    flash("Team registered successfully.", "success")
    return redirect(url_for("participant.events_list"))


@bp.route("/history")
@roles_required(ROLE_STUDENT, ROLE_PARTICIPANT)
def history():
    from flask import session

    _refresh_all_event_statuses()
    profile = _profile_for_session(session["user_id"])
    if not profile:
        return redirect(url_for("participant.dashboard"))

    registrations = (
        db.session.query(EventParticipation, Event, Competition, Team)
        .join(Event, EventParticipation.event_id == Event.id)
        .outerjoin(Competition, EventParticipation.competition_id == Competition.id)
        .outerjoin(Team, EventParticipation.team_id == Team.id)
        .filter(EventParticipation.participant_id == profile.id)
        .order_by(Event.date.desc())
        .all()
    )

    team_ids = [team.id for _, _, _, team in registrations if team]
    results = Result.query.filter(Result.participant_id == profile.id).all()
    if team_ids:
        results.extend(Result.query.filter(Result.team_id.in_(team_ids)).all())
    result_map = {}
    for result in results:
        if result.team_id is not None:
            result_map[("team", result.team_id)] = result
        else:
            result_map[("participant", result.participant_id)] = result

    rows = []
    for participation, event, competition, team in registrations:
        key = ("team", team.id) if team else ("participant", participation.participant_id)
        rows.append((participation, event, competition, team, result_map.get(key)))

    return render_template(
        "participant/history.html",
        profile=profile,
        rows=rows,
    )
