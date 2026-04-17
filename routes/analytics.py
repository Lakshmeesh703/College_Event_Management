"""
Analytics dashboard, filtered metrics, and Matplotlib chart endpoints.

Charts are rendered server-side (PNG) for a simple HTML/CSS frontend.
Query parameters: date_from, date_to, school, category (all optional).
"""
import io
import csv
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from flask import Blueprint, Response, jsonify, render_template, request

from auth_decorators import ROLE_MANAGEMENT, roles_required
from models import Event, EventParticipation, Participant, Result, db
from scripts.data_processing import standardize_category

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


def _base_event_query():
    q = Event.query
    df_from = _parse_date(request.args.get("date_from"))
    df_to = _parse_date(request.args.get("date_to"))
    if df_from:
        q = q.filter(Event.date >= df_from)
    if df_to:
        q = q.filter(Event.date <= df_to)

    school = (request.args.get("school") or "").strip()
    if school:
        q = q.filter(Event.school == school)

    cat = (request.args.get("category") or "").strip()
    if cat:
        q = q.filter(Event.category == standardize_category(cat))

    return q


def _events_dataframe():
    """Load filtered events into Pandas (repeatable pipeline input)."""
    rows = _base_event_query().order_by(Event.date.asc()).all()
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
@roles_required(ROLE_MANAGEMENT)
def dashboard():
    """Stakeholder dashboard with tables + chart image URLs (respects filters)."""
    ev_df = _events_dataframe()
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
            "school": request.args.get("school", ""),
            "category": request.args.get("category", ""),
        },
        chart_q=chart_q,
    )


@bp.route("/data")
@roles_required(ROLE_MANAGEMENT)
def dashboard_data():
    """Interactive dashboard JSON feed with active filters applied."""
    ev_df = _events_dataframe()
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
@roles_required(ROLE_MANAGEMENT)
def export_csv():
    """Export filtered event and participation summary to CSV."""
    ev_df = _events_dataframe()
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


@bp.route("/export-report")
@roles_required(ROLE_MANAGEMENT)
def export_report():
    """Download a text analytics report suitable for submission and sharing."""
    ev_df = _events_dataframe()
    event_ids = ev_df["id"].tolist() if not ev_df.empty else []
    part_df = _participation_stats_for_events(event_ids)
    total_events = len(event_ids)
    total_regs = int(part_df["participant_count"].sum()) if not part_df.empty else 0
    avg_participation = round(total_regs / total_events, 2) if total_events else 0.0

    lines = [
        "College Event Statistics Report",
        f"Generated at: {datetime.utcnow().isoformat()}Z",
        "",
        f"Total events: {total_events}",
        f"Total registrations: {total_regs}",
        f"Average participation per event: {avg_participation}",
    ]

    response = Response("\n".join(lines), mimetype="text/plain")
    response.headers["Content-Disposition"] = "attachment; filename=analytics_report.txt"
    return response


def _png_response(fig) -> Response:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")


@bp.route("/chart/bar")
@roles_required(ROLE_MANAGEMENT)
def chart_bar():
    """Bar chart: events per department."""
    ev_df = _events_dataframe()
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
@roles_required(ROLE_MANAGEMENT)
def chart_line():
    """Line chart: monthly event counts."""
    ev_df = _events_dataframe()
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
@roles_required(ROLE_MANAGEMENT)
def chart_pie():
    """Pie chart: category distribution."""
    ev_df = _events_dataframe()
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
