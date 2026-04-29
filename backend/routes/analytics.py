"""
Analytics dashboard, filtered metrics, and Matplotlib chart endpoints.

Charts are rendered server-side (PNG) for a simple HTML/CSS frontend.
Query parameters: date_from, date_to, school, category (all optional).
"""
import io
import csv
import re
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from sqlalchemy import and_
from flask import Blueprint, Response, jsonify, render_template, request, session

from ..auth_decorators import (
    ROLE_ADMIN,
    ROLE_CONVENER,
    ROLE_COORDINATOR,
    ROLE_MANAGEMENT,
    roles_required,
)
from ..models import (
    Competition,
    CoordinatorProfile,
    Event,
    EventParticipation,
    Participant,
    Result,
    User,
    db,
)
from ..scripts.data_processing import standardize_category

bp = Blueprint("analytics", __name__)

# Dropdown labels (keep in sync with routes/main.py)
FILTER_CATEGORIES = [
    ("", "All categories"),
    ("technical", "Technical"),
    ("cultural", "Cultural"),
    ("sports", "Sports"),
    ("workshop", "Workshop"),
    ("other", "Other"),
]

sns.set_theme(style="whitegrid", font_scale=1.05)


def _parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _analytics_user() -> User | None:
    uid = session.get("user_id")
    if not uid:
        return None
    return db.session.get(User, uid)


def _convener_school(user: User | None) -> str:
    if not user or user.role != User.ROLE_CONVENER:
        return ""
    profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
    return (profile.school or "") if profile else ""


def _role_access_context(role: str | None) -> dict:
    role = role or ""
    is_management = role == User.ROLE_MANAGEMENT
    is_admin = role == User.ROLE_ADMIN
    is_convener = role == User.ROLE_CONVENER
    is_coordinator = role == User.ROLE_COORDINATOR
    return {
        "role": role,
        "is_management": is_management,
        "is_admin": is_admin,
        "is_convener": is_convener,
        "is_coordinator": is_coordinator,
        "show_summary_only": False,
        "show_detailed": True,
        "can_export_csv": role in {
            User.ROLE_COORDINATOR,
            User.ROLE_CONVENER,
            User.ROLE_ADMIN,
            User.ROLE_MANAGEMENT,
        },
        "can_export_event_wise": role in {User.ROLE_CONVENER, User.ROLE_ADMIN, User.ROLE_MANAGEMENT},
    }


def _base_event_query(user: User | None):
    q = Event.query
    convener_school = _convener_school(user)
    if user and user.role == User.ROLE_COORDINATOR:
        profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
        if profile and profile.allotted_event_id:
            q = q.filter(Event.id == profile.allotted_event_id)
        elif profile and profile.allotted_competition_id:
            q = q.filter(Event.competitions.any(Competition.id == profile.allotted_competition_id))
        else:
            q = q.filter(Event.created_by_id == user.id)
    elif user and user.role == User.ROLE_CONVENER and convener_school:
        q = q.filter(Event.school == convener_school)

    df_from = _parse_date(request.args.get("date_from"))
    df_to = _parse_date(request.args.get("date_to"))
    if df_from:
        q = q.filter(Event.date >= df_from)
    if df_to:
        q = q.filter(Event.date <= df_to)

    school = convener_school if (user and user.role == User.ROLE_CONVENER) else (request.args.get("school") or "").strip()
    if school:
        q = q.filter(Event.school == school)

    cat = (request.args.get("category") or "").strip()
    if cat:
        q = q.filter(Event.category == standardize_category(cat))

    return q


def _events_dataframe(user: User | None):
    """Load filtered events into Pandas (repeatable pipeline input)."""
    rows = _base_event_query(user).order_by(Event.date.asc()).all()
    if rows:
        for ev in rows:
            ev.refresh_status()
        db.session.commit()
    if not rows:
        return pd.DataFrame(
            columns=[
                "id",
                "name",
                "school",
                "department",
                "category",
                "date",
                "venue",
                "organizer",
            ]
        )
    data = [
        {
            "id": e.id,
            "name": e.name,
            "school": e.school_or_department,
            "department": e.department,
            "category": e.category,
            "date": e.date,
            "venue": e.venue,
            "organizer": e.organizer,
        }
        for e in rows
    ]
    return pd.DataFrame(data)


def _has_export_access(user: User | None) -> bool:
    return bool(user and user.role in {User.ROLE_COORDINATOR, User.ROLE_CONVENER, User.ROLE_ADMIN, User.ROLE_MANAGEMENT})


def _has_full_export_access(user: User | None) -> bool:
    return bool(user and user.role in {User.ROLE_CONVENER, User.ROLE_ADMIN, User.ROLE_MANAGEMENT})


def _safe_csv_filename(prefix: str, event_id: int, event_name: str) -> str:
    """Return ASCII-safe CSV filename to avoid header encoding failures."""
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", (event_name or "")).strip("_")
    if not safe_name:
        safe_name = "event"
    return f"{prefix}_{event_id}_{safe_name}.csv"


def _coordinator_can_access_event(user: User | None, event: Event) -> bool:
    if not user or user.role != User.ROLE_COORDINATOR:
        return True
    profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
    if profile and profile.allotted_event_id:
        return event.id == profile.allotted_event_id
    if profile and profile.allotted_competition_id:
        return Competition.query.filter_by(id=profile.allotted_competition_id, event_id=event.id).first() is not None
    return event.created_by_id == user.id


def _coordinator_allotted_competition_id(user: User | None) -> int | None:
    if not user or user.role != User.ROLE_COORDINATOR:
        return None
    profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
    return profile.allotted_competition_id if profile else None


def _participation_stats_for_events(event_ids: list[int]) -> pd.DataFrame:
    if not event_ids:
        return pd.DataFrame(columns=["id", "name", "participant_count"])
    q = (
        db.session.query(
            Event.id,
            Event.name,
            db.func.count(EventParticipation.id).label("participant_count"),
        )
        .outerjoin(EventParticipation, EventParticipation.event_id == Event.id)
        .filter(Event.id.in_(event_ids))
        .group_by(Event.id, Event.name)
    )
    rows = q.all()
    return pd.DataFrame(
        [
            {"id": r.id, "name": r.name, "participant_count": r.participant_count}
            for r in rows
        ]
    )


def _internal_external_ratio(event_ids: list[int]) -> tuple[int, int]:
    if not event_ids:
        return 0, 0
    base = EventParticipation.query.filter(
        EventParticipation.event_id.in_(event_ids)
    )
    internal = base.filter(EventParticipation.is_external.is_(False)).count()
    external = base.filter(EventParticipation.is_external.is_(True)).count()
    return internal, external


@bp.route("/dashboard")
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def dashboard():
    """Stakeholder dashboard with tables + chart image URLs (respects filters)."""
    user = _analytics_user()
    access_context = _role_access_context(user.role if user else "")
    convener_school = _convener_school(user)
    ev_df = _events_dataframe(user)
    event_ids = ev_df["id"].tolist() if not ev_df.empty else []

    # --- Management-style aggregates ---
    events_per_dept = (
        ev_df.groupby("school").size().reset_index(name="event_count")
        if not ev_df.empty
        else pd.DataFrame(columns=["school", "event_count"])
    )

    part_df = _participation_stats_for_events(event_ids)
    internal, external = _internal_external_ratio(event_ids)
    total_events = len(event_ids)
    total_participants = (
        db.session.query(db.func.count(db.func.distinct(EventParticipation.participant_id)))
        .filter(EventParticipation.event_id.in_(event_ids))
        .scalar()
        if event_ids
        else 0
    )
    total_registrations = int(part_df["participant_count"].sum()) if not part_df.empty else 0
    avg_participation = round(total_registrations / total_events, 2) if total_events else 0.0
    total_schools = int(ev_df["school"].nunique()) if not ev_df.empty else 0

    # Monthly trends (semester-style: by month)
    monthly = pd.DataFrame(columns=["month", "event_count"])
    if not ev_df.empty:
        ev_df = ev_df.copy()
        ev_df["month"] = pd.to_datetime(ev_df["date"]).dt.to_period("M").astype(str)
        monthly = (
            ev_df.groupby("month").size().reset_index(name="event_count")
        )

    # Top performers (rank not null), joined names
    top_rows = []
    if event_ids:
        q = (
            db.session.query(
                Result.rank,
                Result.prize,
                Event.name.label("event_name"),
                Participant.name.label("participant_name"),
                Participant.roll_number,
                Participant.department,
            )
            .join(Event, Result.event_id == Event.id)
            .join(Participant, Result.participant_id == Participant.id)
            .filter(Result.event_id.in_(event_ids), Result.rank.isnot(None))
            .order_by(Result.rank.asc())
            .limit(25)
        )
        top_rows = q.all()

    # Composite metric: participation intensity = total participations / event count per department
    composite = pd.DataFrame(columns=["department", "events", "participations", "intensity"])
    if not ev_df.empty and not part_df.empty:
        merged = part_df.merge(ev_df[["id", "school"]], on="id", how="left")
        g = merged.groupby("school").agg(
            events=("id", "nunique"),
            participations=("participant_count", "sum"),
        )
        g["intensity"] = (g["participations"] / g["events"].replace(0, float("nan"))).round(2)
        composite = g.reset_index()

    # Active organizers
    organizers = []
    if not ev_df.empty:
        organizers = (
            ev_df.groupby("organizer")
            .size()
            .reset_index(name="events_led")
            .sort_values("events_led", ascending=False)
            .head(15)
            .to_dict("records")
        )

    top_events = []
    if not part_df.empty:
        top_events = (
            part_df.sort_values("participant_count", ascending=False)
            .head(10)
            .to_dict("records")
        )

    school_registration_rows = []
    if not part_df.empty and not ev_df.empty:
        school_regs_df = part_df.merge(ev_df[["id", "school"]], on="id", how="left")
        school_registration_rows = (
            school_regs_df.groupby("school", dropna=False)["participant_count"]
            .sum()
            .reset_index(name="registrations")
            .sort_values("registrations", ascending=False)
            .to_dict("records")
        )

    student_department_rows = []
    student_year_rows = []
    active_students_rows = []
    if event_ids:
        dept_q = (
            db.session.query(
                Participant.department,
                db.func.count(EventParticipation.id).label("registrations"),
            )
            .join(EventParticipation, EventParticipation.participant_id == Participant.id)
            .filter(EventParticipation.event_id.in_(event_ids))
            .group_by(Participant.department)
            .order_by(db.func.count(EventParticipation.id).desc())
            .limit(10)
            .all()
        )
        student_department_rows = [
            {"department": (row.department or "Unknown"), "registrations": int(row.registrations)}
            for row in dept_q
        ]

        year_q = (
            db.session.query(
                Participant.year,
                db.func.count(EventParticipation.id).label("registrations"),
            )
            .join(EventParticipation, EventParticipation.participant_id == Participant.id)
            .filter(EventParticipation.event_id.in_(event_ids))
            .group_by(Participant.year)
            .order_by(Participant.year.asc())
            .all()
        )
        student_year_rows = [
            {"year": (row.year if row.year is not None else "NA"), "registrations": int(row.registrations)}
            for row in year_q
        ]

        active_q = (
            db.session.query(
                Participant.name,
                Participant.department,
                db.func.count(EventParticipation.id).label("events_joined"),
            )
            .join(EventParticipation, EventParticipation.participant_id == Participant.id)
            .filter(EventParticipation.event_id.in_(event_ids))
            .group_by(Participant.id, Participant.name, Participant.department)
            .order_by(db.func.count(EventParticipation.id).desc(), Participant.name.asc())
            .limit(10)
            .all()
        )
        active_students_rows = [
            {
                "name": row.name,
                "department": (row.department or "Unknown"),
                "events_joined": int(row.events_joined),
            }
            for row in active_q
        ]

    # Registrations by weekday (Mon..Sun)
    weekday_counts = []
    if event_ids:
        parts = (
            db.session.query(EventParticipation.created_at)
            .filter(EventParticipation.event_id.in_(event_ids))
            .all()
        )
        if parts:
            from collections import Counter

            dow = Counter()
            for (created_at,) in parts:
                if created_at:
                    dow[created_at.strftime("%A")] += 1
            order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            weekday_counts = [
                {"weekday": d, "count": int(dow.get(d, 0))} for d in order if dow.get(d, 0) > 0
            ]

    # Top competitions by registrations across visible events
    top_competitions = []
    if event_ids:
        q = (
            db.session.query(Competition.name, db.func.count(EventParticipation.id).label("regs"))
            .join(EventParticipation, EventParticipation.competition_id == Competition.id)
            .filter(EventParticipation.event_id.in_(event_ids))
            .group_by(Competition.id, Competition.name)
            .order_by(db.func.count(EventParticipation.id).desc())
            .limit(10)
        )
        top_competitions = [
            {"name": r[0], "registrations": int(r[1])} for r in q.all()
        ]

    top_school_name = "-"
    top_school_events = 0
    if not events_per_dept.empty:
        top_school_row = events_per_dept.sort_values("event_count", ascending=False).iloc[0]
        top_school_name = top_school_row["school"]
        top_school_events = int(top_school_row["event_count"])

    top_event_name = "-"
    top_event_regs = 0
    if top_events:
        top_event_name = top_events[0]["name"]
        top_event_regs = int(top_events[0]["participant_count"])

    total_people = internal + external
    internal_share = round((internal * 100.0 / total_people), 1) if total_people else 0.0
    external_share = round((external * 100.0 / total_people), 1) if total_people else 0.0

    chart_q = request.query_string.decode() if request.query_string else ""

    return render_template(
        "dashboard.html",
        events_per_dept=events_per_dept.to_dict("records"),
        part_df=part_df.to_dict("records"),
        monthly=monthly.to_dict("records"),
        top_rows=top_rows,
        top_events=top_events,
        internal=internal,
        external=external,
        kpis={
            "total_events": total_events,
            "total_participants": total_participants,
            "avg_participation": avg_participation,
            "total_registrations": total_registrations,
            "total_schools": total_schools,
        },
        insights={
            "top_school_name": top_school_name,
            "top_school_events": top_school_events,
            "top_event_name": top_event_name,
            "top_event_regs": top_event_regs,
            "internal_share": internal_share,
            "external_share": external_share,
        },
        composite=composite.to_dict("records"),
        school_registration_rows=school_registration_rows,
        student_department_rows=student_department_rows,
        student_year_rows=student_year_rows,
        active_students_rows=active_students_rows,
        organizers=organizers,
        filter_categories=FILTER_CATEGORIES,
        filters={
            "date_from": request.args.get("date_from", ""),
            "date_to": request.args.get("date_to", ""),
            "school": convener_school if access_context["is_convener"] else request.args.get("school", ""),
            "category": request.args.get("category", ""),
        },
        school_options=Event.ALLOWED_SCHOOLS,
        show_school_filter=not access_context["is_convener"],
        convener_school=convener_school,
        chart_q=chart_q,
        access=access_context,
        weekday_counts=weekday_counts,
        top_competitions=top_competitions,
    )


@bp.route("/events-by-school")
@roles_required(ROLE_ADMIN)
def events_by_school():
    """Admin-only: all events organized by school with full data and participant access."""
    schools_data = []
    for school in Event.ALLOWED_SCHOOLS:
        events = Event.query.filter(Event.school == school).order_by(Event.date.desc()).all()
        events_list = []
        for event in events:
            event.refresh_status()
            participation_count = EventParticipation.query.filter_by(event_id=event.id).count()
            result_count = Result.query.filter_by(event_id=event.id).count()
            creator_name = event.creator.name if event.creator else "N/A"
            events_list.append({
                "id": event.id,
                "name": event.name,
                "date": event.date,
                "status": event.status,
                "participants": participation_count,
                "results": result_count,
                "category": event.category,
                "venue": event.venue,
                "organizer": event.organizer,
                "department": event.department,
                "registration_deadline": event.registration_deadline,
                "max_participants": event.max_participants,
                "creator_name": creator_name,
                "allow_external": event.allow_external,
            })
        if events_list:
            schools_data.append({
                "school": school,
                "events": events_list,
                "total_events": len(events_list),
            })
    db.session.commit()
    return render_template("admin/events_by_school.html", schools_data=schools_data)


@bp.route("/export/event/<int:event_id>", methods=["GET"])
@roles_required(ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def export_event_data(event_id):
    """Export event participants and results as CSV."""
    event = Event.query.get(event_id)
    if not event:
        return "Event not found", 404

    # Permission check: coordinator can only export own events, convener can export school events, admin can export all
    user = _analytics_user()
    if user.role == User.ROLE_COORDINATOR and not _coordinator_can_access_event(user, event):
        return "Unauthorized", 403
    elif user.role == User.ROLE_CONVENER:
        profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
        if not profile or profile.school != event.school:
            return "Unauthorized", 403

    # Fetch participants
    competition_scope_id = _coordinator_allotted_competition_id(user)
    participations_query = EventParticipation.query.filter_by(event_id=event_id)
    results_query = Result.query.filter_by(event_id=event_id)
    if user.role == User.ROLE_COORDINATOR and competition_scope_id:
        participations_query = participations_query.filter_by(competition_id=competition_scope_id)
        results_query = results_query.filter_by(competition_id=competition_scope_id)
    participations = participations_query.all()
    results = results_query.all()

    # Build CSV output
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["Event Participants & Results Report"])
    writer.writerow(["Event Name", event.name])
    writer.writerow(["School", event.school or event.department])
    writer.writerow(["Date", event.date])
    writer.writerow(["Venue", event.venue])
    writer.writerow(["Organizer", event.organizer])
    writer.writerow(["Registration Deadline", event.registration_deadline or "N/A"])
    writer.writerow(["Max Participants", event.max_participants or "Unlimited"])
    writer.writerow([])
    
    # Participants section
    writer.writerow(["PARTICIPANTS"])
    writer.writerow(["Participant Name", "Roll Number", "Department", "Email", "WhatsApp", "Registered On", "External"])
    for part in participations:
        participant = part.participant
        participant_name = participant.name if participant else "N/A"
        participant_roll = participant.roll_number if participant else None
        participant_department = participant.department if participant else None
        participant_email = participant.user.email if (participant and participant.user) else "N/A"
        participant_whatsapp = participant.whatsapp_number if participant else None
        writer.writerow([
            participant_name,
            participant_roll or "N/A",
            participant_department or "N/A",
            participant_email,
            participant_whatsapp or "N/A",
            part.created_at.strftime("%Y-%m-%d %H:%M") if part.created_at else "N/A",
            "Yes" if part.is_external else "No",
        ])
    
    writer.writerow([])
    
    # Results section
    writer.writerow(["RESULTS"])
    writer.writerow(["Rank", "Participant Name", "Roll Number", "Department", "Prize", "Score"])
    for result in results:
        participant = result.participant
        participant_name = participant.name if participant else "N/A"
        participant_roll = participant.roll_number if participant else None
        participant_dept = participant.department if participant else None
        writer.writerow([
            result.rank or "N/A",
            participant_name,
            participant_roll or "N/A",
            participant_dept or "N/A",
            result.prize or "N/A",
            getattr(result, "score", None) or "N/A",
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    filename = _safe_csv_filename("event", event_id, event.name)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@bp.route("/export/registered-students/<int:event_id>", methods=["GET"])
@roles_required(ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def export_registered_students(event_id):
    """Export only registered students for an event as CSV."""
    event = Event.query.get(event_id)
    if not event:
        return "Event not found", 404

    # Permission check: coordinator can only export own events, convener can export school events, admin can export all
    user = _analytics_user()
    if user.role == User.ROLE_COORDINATOR and not _coordinator_can_access_event(user, event):
        return "Unauthorized", 403
    elif user.role == User.ROLE_CONVENER:
        profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
        if not profile or profile.school != event.school:
            return "Unauthorized", 403

    # Fetch only participants
    competition_scope_id = _coordinator_allotted_competition_id(user)
    participations_query = EventParticipation.query.filter_by(event_id=event_id)
    if user.role == User.ROLE_COORDINATOR and competition_scope_id:
        participations_query = participations_query.filter_by(competition_id=competition_scope_id)
    participations = participations_query.all()

    # Build CSV output
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["Registered Students Report"])
    writer.writerow(["Event Name", event.name])
    writer.writerow(["School", event.school or event.department])
    writer.writerow(["Date", event.date])
    writer.writerow(["Total Registered", len(participations)])
    writer.writerow([])
    
    # Participants section
    writer.writerow(["Participant Name", "Competition", "Roll Number", "Department", "Email", "WhatsApp", "Year", "Registered On", "External"])
    for part in participations:
        participant = part.participant
        competition_name = part.competition.name if part.competition else "N/A"
        participant_name = participant.name if participant else "N/A"
        participant_roll = participant.roll_number if participant else None
        participant_department = participant.department if participant else None
        participant_email = participant.user.email if (participant and participant.user) else "N/A"
        participant_whatsapp = participant.whatsapp_number if participant else None
        participant_year = participant.year if participant else None
        writer.writerow([
            participant_name,
            competition_name,
            participant_roll or "N/A",
            participant_department or "N/A",
            participant_email,
            participant_whatsapp or "N/A",
            participant_year or "N/A",
            part.created_at.strftime("%Y-%m-%d %H:%M") if part.created_at else "N/A",
            "Yes" if part.is_external else "No",
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    filename = _safe_csv_filename("registered_students", event_id, event.name)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@bp.route("/data")
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def dashboard_data():
    """Interactive dashboard JSON feed with active filters applied."""
    user = _analytics_user()
    ev_df = _events_dataframe(user)
    event_ids = ev_df["id"].tolist() if not ev_df.empty else []
    part_df = _participation_stats_for_events(event_ids)

    monthly = []
    if not ev_df.empty:
        df = ev_df.copy()
        df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
        monthly = (
            df.groupby("month")
            .size()
            .reset_index(name="event_count")
            .to_dict("records")
        )

    dept = []
    if not ev_df.empty:
        dept = (
            ev_df.groupby("school")
            .size()
            .reset_index(name="event_count")
            .to_dict("records")
        )

    top_events = []
    if not part_df.empty:
        top_events = (
            part_df.sort_values("participant_count", ascending=False)
            .head(10)
            .rename(columns={"name": "event_name", "participant_count": "registrations"})
            .to_dict("records")
        )

    return jsonify(
        {
            "monthly_trends": monthly,
            "department_comparison": dept,
            "top_events": top_events,
        }
    )


@bp.route("/export.csv")
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def export_csv():
    """Export filtered event and participation summary to CSV."""
    user = _analytics_user()
    if not _has_export_access(user):
        return Response("Export is not available for this role.", status=403, mimetype="text/plain")
    ev_df = _events_dataframe(user)
    event_ids = ev_df["id"].tolist() if not ev_df.empty else []
    part_df = _participation_stats_for_events(event_ids)

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["event_id", "event_name", "department", "category", "date", "participant_count"])

    counts = {int(row["id"]): int(row["participant_count"]) for _, row in part_df.iterrows()} if not part_df.empty else {}
    for _, row in ev_df.iterrows():
        writer.writerow([
            row["id"],
            row["name"],
            row["department"],
            row["category"],
            row["date"],
            counts.get(int(row["id"]), 0),
        ])
    resp = Response(out.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=analytics_summary.csv"
    return resp


@bp.route("/export-events.csv")
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def export_events_csv():
    """Export all visible events with participant details grouped under each event name."""
    user = _analytics_user()
    if not _has_full_export_access(user):
        return Response("Event-wise export is available only for admin/convener.", status=403, mimetype="text/plain")

    events = _base_event_query(user).order_by(Event.name.asc(), Event.date.asc()).all()
    out = io.StringIO()
    writer = csv.writer(out)

    if not events:
        writer.writerow(["No events found for selected filters"])
    else:
        for event in events:
            writer.writerow(["Event", event.name])
            writer.writerow(["School", event.school_or_department])
            writer.writerow(["Category", event.category])
            writer.writerow(["Date", event.date])
            writer.writerow(["Participant Name", "Competition", "Department", "Year", "Organization", "External", "Rank", "Prize"])

            rows = (
                db.session.query(Participant, EventParticipation, Result)
                .join(EventParticipation, EventParticipation.participant_id == Participant.id)
                .outerjoin(
                    Result,
                    and_(
                        Result.event_id == event.id,
                        Result.participant_id == Participant.id,
                        Result.competition_id == EventParticipation.competition_id,
                    ),
                )
                .filter(EventParticipation.event_id == event.id)
                .order_by(Participant.name.asc())
                .all()
            )

            for participant, participation, result in rows:
                writer.writerow(
                    [
                        participant.name,
                        participation.competition.name if participation.competition else "",
                        participant.department or "",
                        participant.year if participant.year is not None else "",
                        participant.organization or "",
                        "Yes" if participation.is_external else "No",
                        result.rank if result and result.rank is not None else "",
                        result.prize if result and result.prize else "",
                    ]
                )
            writer.writerow([])

    resp = Response(out.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=events_detailed_export.csv"
    return resp


@bp.route("/export-registered-students-all.csv")
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def export_registered_students_all_csv():
    """Export registered students across all visible events as CSV."""
    user = _analytics_user()
    events = _base_event_query(user).order_by(Event.date.asc(), Event.name.asc()).all()

    out = io.StringIO()
    writer = csv.writer(out)

    if not events:
        writer.writerow(["No events found for selected filters"])
    else:
        writer.writerow([
            "event_id",
            "event_name",
            "competition_name",
            "school",
            "event_date",
            "participant_name",
            "roll_number",
            "department",
            "email",
            "whatsapp",
            "year",
            "organization",
            "registered_on",
            "external",
        ])
        for event in events:
            rows = (
                db.session.query(EventParticipation, Participant)
                .join(Participant, EventParticipation.participant_id == Participant.id)
                .filter(EventParticipation.event_id == event.id)
                .order_by(Participant.name.asc())
                .all()
            )
            for participation, participant in rows:
                writer.writerow([
                    event.id,
                    event.name,
                    participation.competition.name if participation.competition else "",
                    event.school or event.department or "",
                    event.date,
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
    response.headers["Content-Disposition"] = "attachment; filename=registered_students_all_events.csv"
    return response


@bp.route("/export-report")
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def export_report():
    """Export analytics summary as CSV (CSV-only download policy)."""
    user = _analytics_user()
    if not _has_export_access(user):
        return Response("Export is not available for this role.", status=403, mimetype="text/plain")
    ev_df = _events_dataframe(user)
    event_ids = ev_df["id"].tolist() if not ev_df.empty else []
    part_df = _participation_stats_for_events(event_ids)
    total_events = len(event_ids)
    total_regs = int(part_df["participant_count"].sum()) if not part_df.empty else 0
    avg_participation = round(total_regs / total_events, 2) if total_events else 0.0

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["metric", "value"])
    writer.writerow(["generated_at_utc", f"{datetime.utcnow().isoformat()}Z"])
    writer.writerow(["total_events", total_events])
    writer.writerow(["total_registrations", total_regs])
    writer.writerow(["average_participation_per_event", avg_participation])

    response = Response(out.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=analytics_report.csv"
    return response


def _png_response(fig) -> Response:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")


@bp.route("/chart/bar")
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def chart_bar():
    """Bar chart: events per department."""
    user = _analytics_user()
    ev_df = _events_dataframe(user)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if ev_df.empty:
        ax.text(0.5, 0.5, "No data for filters", ha="center", va="center")
        ax.axis("off")
        return _png_response(fig)
    counts = ev_df.groupby("school").size()
    counts.plot(kind="bar", ax=ax, color=sns.color_palette("husl", n_colors=len(counts)))
    ax.set_title("Events per school")
    ax.set_xlabel("School")
    ax.set_ylabel("Count")
    plt.xticks(rotation=35, ha="right")
    fig.tight_layout()
    return _png_response(fig)


@bp.route("/chart/line")
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def chart_line():
    """Line chart: monthly event counts."""
    user = _analytics_user()
    ev_df = _events_dataframe(user)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if ev_df.empty:
        ax.text(0.5, 0.5, "No data for filters", ha="center", va="center")
        ax.axis("off")
        return _png_response(fig)
    ev_df = ev_df.copy()
    ev_df["month"] = pd.to_datetime(ev_df["date"]).dt.to_period("M").astype(str)
    series = ev_df.groupby("month").size()
    series.plot(kind="line", ax=ax, marker="o", color="#2c7fb8")
    ax.set_title("Monthly event trend")
    ax.set_xlabel("Month")
    ax.set_ylabel("Events")
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    return _png_response(fig)


@bp.route("/chart/pie")
@roles_required(ROLE_MANAGEMENT, ROLE_ADMIN, ROLE_CONVENER, ROLE_COORDINATOR)
def chart_pie():
    """Pie chart: category distribution."""
    user = _analytics_user()
    ev_df = _events_dataframe(user)
    fig, ax = plt.subplots(figsize=(6, 6))
    if ev_df.empty:
        ax.text(0.5, 0.5, "No data for filters", ha="center", va="center")
        ax.axis("off")
        return _png_response(fig)
    counts = ev_df.groupby("category").size()
    colors = sns.color_palette("pastel", n_colors=len(counts))
    ax.pie(
        counts.values,
        labels=counts.index,
        autopct="%1.1f%%",
        colors=colors,
        startangle=90,
    )
    ax.set_title("Events by category")
    fig.tight_layout()
    return _png_response(fig)
