"""
REST API endpoints for frontend integrations and external consumers.
"""
from flask import Blueprint, jsonify, request
from sqlalchemy import func

from models import Event, EventParticipation, Participant, db
from scripts.data_processing import standardize_category

bp = Blueprint("api", __name__, url_prefix="/api")


def _parse_date(value: str | None):
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@bp.route("/events")
def events():
    query = Event.query

    search = (request.args.get("search") or "").strip()
    school = (request.args.get("school") or "").strip()
    category = (request.args.get("category") or "").strip()
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"))

    if search:
        query = query.filter(Event.name.ilike(f"%{search}%"))
    if school:
        query = query.filter(Event.school == school)
    if category:
        query = query.filter(Event.category == standardize_category(category))
    if date_from:
        query = query.filter(Event.date >= date_from)
    if date_to:
        query = query.filter(Event.date <= date_to)

    rows = query.order_by(Event.date.desc()).limit(200).all()
    payload = [
        {
            "id": e.id,
            "name": e.name,
            "school": e.school_or_department,
            "department": e.department,
            "category": e.category,
            "date": e.date.isoformat(),
            "registration_deadline": e.registration_deadline.isoformat() if e.registration_deadline else None,
            "max_participants": e.max_participants,
            "allow_external": e.allow_external,
            "status": e.refresh_status(),
            "venue": e.venue,
            "organizer": e.organizer,
        }
        for e in rows
    ]
    return jsonify({"count": len(payload), "events": payload})


@bp.route("/participants")
def participants():
    rows = Participant.query.order_by(Participant.created_at.desc()).limit(500).all()
    payload = [
        {
            "id": p.id,
            "name": p.name,
            "roll_number": p.roll_number,
            "department": p.department,
            "organization": p.organization,
            "is_external_user": bool(p.user.is_external_user) if p.user else False,
            "year": p.year,
        }
        for p in rows
    ]
    return jsonify({"count": len(payload), "participants": payload})


@bp.route("/analytics")
def analytics():
    total_events = db.session.query(func.count(Event.id)).scalar() or 0
    total_participants = db.session.query(func.count(Participant.id)).scalar() or 0
    total_regs = db.session.query(func.count(EventParticipation.id)).scalar() or 0
    avg_participation = round(total_regs / total_events, 2) if total_events else 0.0

    monthly_map = {}
    for row in db.session.query(Event.date).all():
        if not row.date:
            continue
        key = row.date.strftime("%Y-%m")
        monthly_map[key] = monthly_map.get(key, 0) + 1
    monthly_rows = [
        {"month": month, "event_count": monthly_map[month]}
        for month in sorted(monthly_map.keys())
    ]

    school_rows = (
        db.session.query(Event.school, func.count(Event.id).label("event_count"))
        .group_by(Event.school)
        .order_by(func.count(Event.id).desc())
        .all()
    )

    top_events = (
        db.session.query(Event.name, func.count(EventParticipation.id).label("registrations"))
        .outerjoin(EventParticipation, EventParticipation.event_id == Event.id)
        .group_by(Event.id, Event.name)
        .order_by(func.count(EventParticipation.id).desc(), Event.name.asc())
        .limit(10)
        .all()
    )

    return jsonify(
        {
            "kpis": {
                "total_events": total_events,
                "total_participants": total_participants,
                "avg_participation_per_event": avg_participation,
            },
            "monthly_trends": monthly_rows,
            "school_comparison": [
                {"school": r.school, "event_count": r.event_count} for r in school_rows
            ],
            "top_events": [
                {"name": r.name, "registrations": r.registrations} for r in top_events
            ],
        }
    )
