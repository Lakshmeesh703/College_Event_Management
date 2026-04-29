"""Management role: create staff accounts and view role-specific profile data."""
import csv
import io
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_
from werkzeug.security import generate_password_hash

from ..auth_decorators import ROLE_ADMIN, ROLE_CONVENER, roles_required
from ..models import (
    ActivityLog,
    Competition,
    CoordinatorProfile,
    EmailOTP,
    Event,
    EventParticipation,
    ManagementProfile,
    Participant,
    Result,
    User,
    db,
)
from ..services.activity_logger import log_action
from ..services.mailer import send_otp_email
from ..services.validation import clean_text, parse_whatsapp_number

bp = Blueprint("management", __name__, url_prefix="/management")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_OTP_RE = re.compile(r"^\d{6}$")
_OTP_TTL_MINUTES = 10


def _valid_email(email: str) -> bool:
    return bool(email and _EMAIL_RE.match(email.strip()))


def _valid_gmail(email: str) -> bool:
    return email.lower().endswith("@gmail.com")


def _valid_outlook(email: str) -> bool:
    return email.lower().endswith("@chanakyauniversity.edu.in")


def _generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _issue_otp(email: str, purpose: str) -> tuple[bool, str]:
    now = datetime.utcnow()
    EmailOTP.query.filter(
        EmailOTP.email == email,
        EmailOTP.purpose == purpose,
        EmailOTP.used_at.is_(None),
    ).update({"used_at": now}, synchronize_session=False)

    code = _generate_otp()
    row = EmailOTP(
        email=email,
        purpose=purpose,
        otp_code=code,
        expires_at=now + timedelta(minutes=_OTP_TTL_MINUTES),
    )
    db.session.add(row)
    db.session.commit()
    return send_otp_email(email, purpose, code), code


def _consume_otp(email: str, purpose: str, otp_code: str) -> bool:
    now = datetime.utcnow()
    row = (
        EmailOTP.query.filter(
            EmailOTP.email == email,
            EmailOTP.purpose == purpose,
            EmailOTP.otp_code == otp_code,
            EmailOTP.used_at.is_(None),
            EmailOTP.expires_at >= now,
        )
        .order_by(EmailOTP.id.desc())
        .first()
    )
    if not row:
        return False
    row.used_at = now
    db.session.commit()
    return True


def _filtered_student_query(args):
    query = (
        db.session.query(User, Participant)
        .join(Participant, Participant.user_id == User.id)
        .filter(User.role.in_([User.ROLE_STUDENT, User.ROLE_PARTICIPANT]))
    )

    q = (args.get("q") or "").strip()
    department = (args.get("department") or "").strip()
    year = (args.get("year") or "").strip()
    external = (args.get("external") or "").strip().lower()
    status = (args.get("status") or "active").strip().lower()

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                User.name.ilike(like),
                User.email.ilike(like),
                Participant.roll_number.ilike(like),
                Participant.department.ilike(like),
            )
        )
    if department:
        query = query.filter(Participant.department == department)
    if year.isdigit():
        query = query.filter(Participant.year == int(year))
    if external in {"yes", "no"}:
        query = query.filter(User.is_external_user.is_(external == "yes"))
    if status == "active":
        query = query.filter(User.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(User.is_active.is_(False))

    filters = {
        "q": q,
        "department": department,
        "year": year,
        "external": external,
        "status": status,
    }
    return query, filters


def _delete_student_user(user: User):
    profile = Participant.query.filter_by(user_id=user.id).first()
    if profile:
        EventParticipation.query.filter_by(participant_id=profile.id).delete(synchronize_session=False)
        Result.query.filter_by(participant_id=profile.id).delete(synchronize_session=False)
        db.session.delete(profile)
    ActivityLog.query.filter_by(user_id=user.id).update({"user_id": None}, synchronize_session=False)
    db.session.delete(user)


def _convener_school(user: User | None) -> str | None:
    if not user or user.role != User.ROLE_CONVENER:
        return None
    profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
    return profile.school if profile and profile.school else None


def _coordinator_allotment_options(actor: User | None, forced_school: str | None = None):
    events_q = Event.query
    if actor and actor.role == User.ROLE_CONVENER and forced_school:
        events_q = events_q.filter(Event.school == forced_school)

    # Keep allotment choices focused on upcoming events only for easier scheduling.
    raw_events = events_q.order_by(Event.date.asc(), Event.name.asc()).all()
    events = []
    for event in raw_events:
        event.refresh_status()
        if event.status in {Event.STATUS_ONGOING, Event.STATUS_COMPLETED}:
            continue
        events.append(event)

    if raw_events:
        db.session.commit()

    event_ids = [e.id for e in events]
    competitions = []
    if event_ids:
        competitions = (
            Competition.query.filter(Competition.event_id.in_(event_ids))
            .order_by(Competition.date.desc(), Competition.name.asc())
            .all()
        )
    return events, competitions


def _build_pending_staff_payload(role: str, forced_school: str | None = None):
    name = (request.form.get("name") or "").strip()
    gmail_email = (request.form.get("gmail_email") or "").strip().lower()
    university_outlook_email = (request.form.get("university_outlook_email") or "").strip().lower()
    phone_number_raw = (request.form.get("phone_number") or "").strip()
    university = (request.form.get("university") or "").strip()
    position = (request.form.get("position") or "").strip()
    password = (request.form.get("password") or "").strip()
    confirm = (request.form.get("confirm_password") or "").strip()
    school = None
    allotted_event_id = None
    allotted_competition_id = None
    if role in (User.ROLE_CONVENER, User.ROLE_COORDINATOR):
        school = (forced_school or request.form.get("school") or "").strip()
    if role == User.ROLE_COORDINATOR:
        allotted_event_raw = (request.form.get("allotted_event_id") or "").strip()
        allotted_competition_raw = (request.form.get("allotted_competition_id") or "").strip()
        try:
            allotted_event_id = int(allotted_event_raw)
            allotted_competition_id = int(allotted_competition_raw)
        except ValueError:
            flash("Please select allotted event and competition for coordinator.", "error")
            return None

    errors = False
    try:
        name = clean_text(name, min_len=2, max_len=120)
    except ValueError as exc:
        flash(f"Name error: {exc}", "error")
        errors = True

    if not _valid_email(gmail_email) or not _valid_gmail(gmail_email):
        flash("A valid Gmail address is required for OTP verification.", "error")
        errors = True

    if not _valid_email(university_outlook_email) or not _valid_outlook(university_outlook_email):
        flash("University mail must end with @chanakyauniversity.edu.in.", "error")
        errors = True

    try:
        phone_number = parse_whatsapp_number(phone_number_raw, required=True)
    except ValueError as exc:
        flash(str(exc), "error")
        phone_number = ""
        errors = True

    try:
        university = clean_text(university, min_len=2, max_len=180)
    except ValueError as exc:
        flash(f"University error: {exc}", "error")
        errors = True

    try:
        position = clean_text(position, min_len=2, max_len=120)
    except ValueError as exc:
        flash(f"Position error: {exc}", "error")
        errors = True

    if role in (User.ROLE_CONVENER, User.ROLE_COORDINATOR):
        if not school or school not in Event.ALLOWED_SCHOOLS:
            flash("Please select a valid school.", "error")
            errors = True

    if role == User.ROLE_COORDINATOR and allotted_event_id and allotted_competition_id:
        event = Event.query.filter_by(id=allotted_event_id).first()
        competition = Competition.query.filter_by(id=allotted_competition_id).first()
        if not event:
            flash("Selected allotted event is invalid.", "error")
            errors = True
        if not competition:
            flash("Selected allotted competition is invalid.", "error")
            errors = True
        if event and competition and competition.event_id != event.id:
            flash("Selected competition does not belong to selected event.", "error")
            errors = True
        if event and school and event.school != school:
            flash("Allotted event must belong to the selected school.", "error")
            errors = True

    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        errors = True
    if password != confirm:
        flash("Passwords do not match.", "error")
        errors = True

    if User.query.filter_by(email=gmail_email).first():
        flash("A user with this Gmail already exists.", "error")
        errors = True

    if role in (User.ROLE_COORDINATOR, User.ROLE_CONVENER):
        if CoordinatorProfile.query.filter_by(university_outlook_email=university_outlook_email).first():
            flash("Coordinator/convener profile with this university Outlook email already exists.", "error")
            errors = True
    if role == User.ROLE_MANAGEMENT:
        if ManagementProfile.query.filter_by(university_outlook_email=university_outlook_email).first():
            flash("Management profile with this university Outlook email already exists.", "error")
            errors = True

    if errors:
        return None

    payload = {
        "role": role,
        "name": name,
        "gmail_email": gmail_email,
        "university_outlook_email": university_outlook_email,
        "phone_number": phone_number,
        "university": university,
        "position": position,
        "password_hash": generate_password_hash(password),
        "created_by_user_id": session.get("user_id"),
    }
    if school:
        payload["school"] = school
    if role == User.ROLE_COORDINATOR:
        payload["allotted_event_id"] = allotted_event_id
        payload["allotted_competition_id"] = allotted_competition_id
    return payload


@bp.route("/dashboard")
@roles_required(ROLE_ADMIN)
def dashboard():
    total_events = db.session.query(func.count(Event.id)).scalar() or 0
    total_registrations = db.session.query(func.count(EventParticipation.id)).scalar() or 0
    total_results = db.session.query(func.count(Result.id)).scalar() or 0
    active_students = (
        db.session.query(func.count(User.id))
        .filter(
            User.role.in_([User.ROLE_STUDENT, User.ROLE_PARTICIPANT]),
            User.is_active.is_(True),
        )
        .scalar()
        or 0
    )

    top_events = (
        db.session.query(
            Event.id,
            Event.name,
            Event.school,
            func.count(EventParticipation.id).label("registrations"),
        )
        .outerjoin(EventParticipation, EventParticipation.event_id == Event.id)
        .group_by(Event.id, Event.name, Event.school)
        .order_by(func.count(EventParticipation.id).desc(), Event.name.asc())
        .limit(5)
        .all()
    )

    school_rows = (
        db.session.query(
            Event.school,
            func.count(Event.id).label("event_count"),
        )
        .group_by(Event.school)
        .order_by(func.count(Event.id).desc())
        .all()
    )

    coordinator_rows = (
        db.session.query(User, CoordinatorProfile)
        .join(CoordinatorProfile, CoordinatorProfile.user_id == User.id)
        .filter(User.role == User.ROLE_COORDINATOR)
        .order_by(CoordinatorProfile.created_at.desc())
        .all()
    )
    management_rows = (
        db.session.query(User, ManagementProfile)
        .join(ManagementProfile, ManagementProfile.user_id == User.id)
        .order_by(ManagementProfile.created_at.desc())
        .all()
    )
    convener_rows = (
        db.session.query(User, CoordinatorProfile)
        .join(CoordinatorProfile, CoordinatorProfile.user_id == User.id)
        .filter(User.role == User.ROLE_CONVENER)
        .order_by(CoordinatorProfile.created_at.desc())
        .all()
    )
    return render_template(
        "management/dashboard.html",
        analytics_kpis={
            "total_events": total_events,
            "total_registrations": total_registrations,
            "total_results": total_results,
            "active_students": active_students,
        },
        top_events=top_events,
        school_rows=school_rows,
        coordinator_rows=coordinator_rows,
        management_rows=management_rows,
        convener_rows=convener_rows,
    )


@bp.route("/event-data")
@roles_required(ROLE_ADMIN)
def event_data():
    schools = list(Event.ALLOWED_SCHOOLS)
    selected_school = (request.args.get("school") or "").strip()
    events = []

    if selected_school:
        if selected_school not in Event.ALLOWED_SCHOOLS:
            flash("Please select a valid school.", "error")
            return redirect(url_for("management.event_data"))
        events = (
            Event.query.filter(Event.school == selected_school)
            .order_by(Event.date.desc(), Event.name.asc())
            .all()
        )
        for ev in events:
            ev.refresh_status()
        if events:
            db.session.commit()

    return render_template(
        "management/event_data.html",
        schools=schools,
        selected_school=selected_school,
        events=events,
    )


@bp.route("/accounts/students")
@roles_required(ROLE_ADMIN)
def student_accounts():
    query, filters = _filtered_student_query(request.args)
    student_rows = query.order_by(User.created_at.desc()).all()
    departments = [
        row[0]
        for row in db.session.query(Participant.department)
        .filter(Participant.department.isnot(None), Participant.department != "")
        .distinct()
        .order_by(Participant.department.asc())
        .all()
    ]
    return render_template(
        "management/student_accounts.html",
        student_rows=student_rows,
        filters=filters,
        departments=departments,
    )


@bp.route("/accounts/students/export.csv")
@roles_required(ROLE_ADMIN)
def export_student_accounts_csv():
    query, _filters = _filtered_student_query(request.args)
    student_rows = query.order_by(User.created_at.desc()).all()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "user_id",
        "name",
        "email",
        "roll_number",
        "department",
        "organization",
        "whatsapp",
        "university_mail",
        "year",
        "external",
        "status",
        "created_at",
    ])

    for user, profile in student_rows:
        writer.writerow([
            user.id,
            user.name,
            user.email,
            profile.roll_number or "",
            profile.department or "",
            profile.organization or "",
            profile.whatsapp_number or "",
            profile.university_mail or "",
            profile.year if profile.year is not None else "",
            "Yes" if user.is_external_user else "No",
            "Active" if user.is_active else "Inactive",
            user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else "",
        ])

    response = Response(out.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=student_accounts.csv"
    return response


@bp.route("/accounts/students/bulk-action", methods=["POST"])
@roles_required(ROLE_ADMIN)
def bulk_student_accounts_action():
    actor_id = session.get("user_id")
    action = (request.form.get("bulk_action") or "").strip().lower()
    selected_ids = []
    for raw in request.form.getlist("selected_user_ids"):
        try:
            selected_ids.append(int(raw))
        except (TypeError, ValueError):
            continue

    if not selected_ids:
        flash("Select at least one student account.", "warning")
        return redirect(url_for("management.student_accounts"))

    users = (
        User.query.filter(
            User.id.in_(selected_ids),
            User.role.in_([User.ROLE_STUDENT, User.ROLE_PARTICIPANT]),
        )
        .order_by(User.id.asc())
        .all()
    )
    if not users:
        flash("No valid student accounts selected.", "error")
        return redirect(url_for("management.student_accounts"))

    if action == "delete":
        count = 0
        for user in users:
            _delete_student_user(user)
            count += 1
        db.session.commit()
        log_action(
            "student_accounts_bulk_deleted",
            user_id=actor_id,
            role=ROLE_ADMIN,
            details=f"count={count}",
        )
        flash(f"Deleted {count} student account(s).", "success")
        return redirect(url_for("management.student_accounts"))

    if action == "deactivate":
        updated = User.query.filter(User.id.in_([u.id for u in users])).update(
            {"is_active": False}, synchronize_session=False
        )
        db.session.commit()
        log_action(
            "student_accounts_bulk_deactivated",
            user_id=actor_id,
            role=ROLE_ADMIN,
            details=f"count={updated}",
        )
        flash(f"Deactivated {updated} student account(s).", "success")
        return redirect(url_for("management.student_accounts"))

    if action == "activate":
        updated = User.query.filter(User.id.in_([u.id for u in users])).update(
            {"is_active": True}, synchronize_session=False
        )
        db.session.commit()
        log_action(
            "student_accounts_bulk_activated",
            user_id=actor_id,
            role=ROLE_ADMIN,
            details=f"count={updated}",
        )
        flash(f"Activated {updated} student account(s).", "success")
        return redirect(url_for("management.student_accounts"))

    flash("Invalid bulk action selected.", "error")
    return redirect(url_for("management.student_accounts"))


@bp.route("/accounts/coordinator/new", methods=["GET", "POST"])
@roles_required(ROLE_ADMIN, ROLE_CONVENER)
def create_coordinator_account():
    user = db.session.get(User, session.get("user_id"))
    convener_school = _convener_school(user) if user and user.role == User.ROLE_CONVENER else None
    allot_events, allot_competitions = _coordinator_allotment_options(user, convener_school)
    allotment_ready = bool(allot_events and allot_competitions)
    if user and user.role == User.ROLE_CONVENER and not convener_school:
        flash("Convener account is missing assigned school. Contact admin.", "error")
        return redirect(url_for("management.dashboard"))

    if request.method == "POST":
        if not allotment_ready:
            flash("Create at least one event and one competition before creating coordinator accounts.", "warning")
            return render_template(
                "management/create_staff_account.html",
                role_label="Coordinator",
                submit_url=url_for("management.create_coordinator_account"),
                schools=Event.ALLOWED_SCHOOLS,
                show_school=not convener_school,
                forced_school=convener_school,
                show_allotment=True,
                allot_events=allot_events,
                allot_competitions=allot_competitions,
                allotment_ready=allotment_ready,
            )

        pending = _build_pending_staff_payload(User.ROLE_COORDINATOR, forced_school=convener_school)
        if not pending:
            return render_template(
                "management/create_staff_account.html",
                role_label="Coordinator",
                submit_url=url_for("management.create_coordinator_account"),
                schools=Event.ALLOWED_SCHOOLS,
                show_school=not convener_school,
                forced_school=convener_school,
                show_allotment=True,
                allot_events=allot_events,
                allot_competitions=allot_competitions,
                allotment_ready=allotment_ready,
            )

        session["pending_staff_account"] = pending
        sent, otp_code = _issue_otp(pending["gmail_email"], EmailOTP.PURPOSE_STAFF_CREATE)
        if sent:
            flash("OTP sent to the coordinator Gmail account.", "success")
        else:
            flash(f"Email failed. OTP for testing: {otp_code}", "warning")
        return redirect(url_for("management.verify_staff_otp"))

    return render_template(
        "management/create_staff_account.html",
        role_label="Coordinator",
        submit_url=url_for("management.create_coordinator_account"),
        schools=Event.ALLOWED_SCHOOLS,
        show_school=not convener_school,
        forced_school=convener_school,
        show_allotment=True,
        allot_events=allot_events,
        allot_competitions=allot_competitions,
        allotment_ready=allotment_ready,
    )


@bp.route("/accounts/management/new", methods=["GET", "POST"])
@roles_required(ROLE_ADMIN)
def create_management_account():
    if request.method == "POST":
        pending = _build_pending_staff_payload(User.ROLE_MANAGEMENT)
        if not pending:
            return render_template(
                "management/create_staff_account.html",
                role_label="Management",
                submit_url=url_for("management.create_management_account"),
            )

        session["pending_staff_account"] = pending
        sent, otp_code = _issue_otp(pending["gmail_email"], EmailOTP.PURPOSE_STAFF_CREATE)
        if sent:
            flash("OTP sent to the management Gmail account.", "success")
        else:
            flash(f"Email failed. OTP for testing: {otp_code}", "warning")
        return redirect(url_for("management.verify_staff_otp"))

    return render_template(
        "management/create_staff_account.html",
        role_label="Management",
        submit_url=url_for("management.create_management_account"),
    )


@bp.route("/accounts/convener/new", methods=["GET", "POST"])
@roles_required(ROLE_ADMIN)
def create_convener_account():
    if request.method == "POST":
        pending = _build_pending_staff_payload(User.ROLE_CONVENER)
        if not pending:
            return render_template(
                "management/create_staff_account.html",
                role_label="Convener",
                submit_url=url_for("management.create_convener_account"),
                schools=Event.ALLOWED_SCHOOLS,
                show_school=True,
            )

        session["pending_staff_account"] = pending
        sent, otp_code = _issue_otp(pending["gmail_email"], EmailOTP.PURPOSE_STAFF_CREATE)
        if sent:
            flash("OTP sent to the convener Gmail account.", "success")
        else:
            flash(f"Email failed. OTP for testing: {otp_code}", "warning")
        return redirect(url_for("management.verify_staff_otp"))

    return render_template(
        "management/create_staff_account.html",
        role_label="Convener",
        submit_url=url_for("management.create_convener_account"),
        schools=Event.ALLOWED_SCHOOLS,
        show_school=True,
    )


@bp.route("/accounts/verify-otp", methods=["GET", "POST"])
@roles_required(ROLE_ADMIN, ROLE_CONVENER)
def verify_staff_otp():
    pending = session.get("pending_staff_account")
    if not pending:
        flash("Start account creation first.", "warning")
        return redirect(url_for("management.dashboard"))

    if request.method == "POST":
        otp_code = (request.form.get("otp_code") or "").strip()
        if not _OTP_RE.match(otp_code):
            flash("Enter a valid 6-digit OTP.", "error")
            return render_template("management/verify_staff_otp.html", pending=pending)

        email = pending["gmail_email"]
        if not _consume_otp(email, EmailOTP.PURPOSE_STAFF_CREATE, otp_code):
            flash("Invalid or expired OTP.", "error")
            return render_template("management/verify_staff_otp.html", pending=pending)

        if User.query.filter_by(email=email).first():
            session.pop("pending_staff_account", None)
            flash("Account already exists for this Gmail.", "error")
            return redirect(url_for("management.dashboard"))

        user = User(
            name=pending["name"],
            email=email,
            password_hash=pending["password_hash"],
            role=pending["role"],
        )
        db.session.add(user)
        db.session.flush()

        if pending["role"] in (User.ROLE_COORDINATOR, User.ROLE_CONVENER):
            profile = CoordinatorProfile(
                user_id=user.id,
                phone_number=pending["phone_number"],
                university=pending["university"],
                university_outlook_email=pending["university_outlook_email"],
                position=pending["position"],
                school=pending.get("school"),
                allotted_event_id=pending.get("allotted_event_id") if pending["role"] == User.ROLE_COORDINATOR else None,
                allotted_competition_id=pending.get("allotted_competition_id") if pending["role"] == User.ROLE_COORDINATOR else None,
                created_by_user_id=pending.get("created_by_user_id"),
            )
            db.session.add(profile)
        else:
            profile = ManagementProfile(
                user_id=user.id,
                phone_number=pending["phone_number"],
                university=pending["university"],
                university_outlook_email=pending["university_outlook_email"],
                position=pending["position"],
                created_by_user_id=pending.get("created_by_user_id"),
            )
            db.session.add(profile)

        db.session.commit()
        session.pop("pending_staff_account", None)
        log_action(
            "staff_account_created",
            user_id=session.get("user_id"),
            role=session.get("user_role") or ROLE_ADMIN,
            details=f"created_user_id={user.id} created_role={user.role} email={user.email}",
        )
        flash(f"{user.role.title()} account created successfully.", "success")
        return redirect(url_for("management.dashboard"))

    return render_template("management/verify_staff_otp.html", pending=pending)


@bp.route("/accounts/coordinator/<int:user_id>/delete", methods=["POST"])
@roles_required(ROLE_ADMIN)
def delete_coordinator_account(user_id: int):
    actor_id = session.get("user_id")
    user = User.query.filter_by(id=user_id, role=User.ROLE_COORDINATOR).first()
    if not user:
        flash("Coordinator account not found.", "error")
        return redirect(url_for("management.dashboard"))

    # Keep historical events, but detach ownership from deleted account.
    Event.query.filter_by(created_by_id=user.id).update({"created_by_id": None}, synchronize_session=False)
    # Keep referential integrity for staff profiles/activity created by this coordinator.
    CoordinatorProfile.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None}, synchronize_session=False)
    ManagementProfile.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None}, synchronize_session=False)
    ActivityLog.query.filter_by(user_id=user.id).update({"user_id": None}, synchronize_session=False)

    profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
    if profile:
        db.session.delete(profile)
    db.session.delete(user)
    db.session.commit()

    log_action(
        "staff_account_deleted",
        user_id=actor_id,
        role=ROLE_ADMIN,
        details=f"deleted_user_id={user_id} deleted_role=coordinator",
    )
    flash("Coordinator account deleted.", "success")
    return redirect(url_for("management.dashboard"))


@bp.route("/accounts/convener/<int:user_id>/delete", methods=["POST"])
@roles_required(ROLE_ADMIN)
def delete_convener_account(user_id: int):
    actor_id = session.get("user_id")
    user = User.query.filter_by(id=user_id, role=User.ROLE_CONVENER).first()
    if not user:
        flash("Convener account not found.", "error")
        return redirect(url_for("management.dashboard"))

    # Keep referential integrity for profiles/activity created by this convener.
    CoordinatorProfile.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None}, synchronize_session=False)
    ManagementProfile.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None}, synchronize_session=False)
    ActivityLog.query.filter_by(user_id=user.id).update({"user_id": None}, synchronize_session=False)

    profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
    if profile:
        db.session.delete(profile)
    db.session.delete(user)
    db.session.commit()

    log_action(
        "staff_account_deleted",
        user_id=actor_id,
        role=ROLE_ADMIN,
        details=f"deleted_user_id={user_id} deleted_role=convener",
    )
    flash("Convener account deleted.", "success")
    return redirect(url_for("management.dashboard"))


@bp.route("/accounts/management/<int:user_id>/delete", methods=["POST"])
@roles_required(ROLE_ADMIN)
def delete_management_account(user_id: int):
    actor_id = session.get("user_id")
    actor_email = (session.get("user_email") or "").lower()
    user = User.query.filter_by(id=user_id, role=User.ROLE_MANAGEMENT).first()
    if not user:
        flash("Management account not found.", "error")
        return redirect(url_for("management.dashboard"))

    if user.email.lower() == "cumanagement522@gmail.com":
        flash("Default admin account cannot be deleted.", "warning")
        return redirect(url_for("management.dashboard"))
    if user.email.lower() == actor_email:
        flash("You cannot delete your currently logged-in management account.", "warning")
        return redirect(url_for("management.dashboard"))

    # Keep referential integrity for staff profiles created by this management user.
    CoordinatorProfile.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None}, synchronize_session=False)
    ManagementProfile.query.filter_by(created_by_user_id=user.id).update({"created_by_user_id": None}, synchronize_session=False)
    ActivityLog.query.filter_by(user_id=user.id).update({"user_id": None}, synchronize_session=False)

    profile = ManagementProfile.query.filter_by(user_id=user.id).first()
    if profile:
        db.session.delete(profile)
    db.session.delete(user)
    db.session.commit()

    log_action(
        "staff_account_deleted",
        user_id=actor_id,
        role=ROLE_ADMIN,
        details=f"deleted_user_id={user_id} deleted_role=management",
    )
    flash("Management account deleted.", "success")
    return redirect(url_for("management.dashboard"))


@bp.route("/accounts/student/<int:user_id>/delete", methods=["POST"])
@roles_required(ROLE_ADMIN)
def delete_student_account(user_id: int):
    actor_id = session.get("user_id")
    user = User.query.filter(
        User.id == user_id,
        User.role.in_([User.ROLE_STUDENT, User.ROLE_PARTICIPANT]),
    ).first()
    if not user:
        flash("Student account not found.", "error")
        return redirect(url_for("management.student_accounts"))

    _delete_student_user(user)
    db.session.commit()

    log_action(
        "student_account_deleted",
        user_id=actor_id,
        role=ROLE_ADMIN,
        details=f"deleted_user_id={user_id} deleted_role=student",
    )
    flash("Student account deleted.", "success")
    return redirect(url_for("management.student_accounts"))
