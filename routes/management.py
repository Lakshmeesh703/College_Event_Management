"""Management role: create staff accounts and view role-specific profile data."""
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from auth_decorators import ROLE_MANAGEMENT, roles_required
from models import CoordinatorProfile, EmailOTP, Event, ManagementProfile, User, db
from services.activity_logger import log_action
from services.mailer import send_otp_email
from services.validation import clean_text, parse_whatsapp_number

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


def _issue_otp(email: str, purpose: str) -> bool:
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
    return send_otp_email(email, purpose, code)


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


def _build_pending_staff_payload(role: str):
    name = (request.form.get("name") or "").strip()
    gmail_email = (request.form.get("gmail_email") or "").strip().lower()
    university_outlook_email = (request.form.get("university_outlook_email") or "").strip().lower()
    phone_number_raw = (request.form.get("phone_number") or "").strip()
    university = (request.form.get("university") or "").strip()
    position = (request.form.get("position") or "").strip()
    password = (request.form.get("password") or "").strip()
    confirm = (request.form.get("confirm_password") or "").strip()

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

    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        errors = True
    if password != confirm:
        flash("Passwords do not match.", "error")
        errors = True

    if User.query.filter_by(email=gmail_email).first():
        flash("A user with this Gmail already exists.", "error")
        errors = True

    if role == User.ROLE_COORDINATOR:
        if CoordinatorProfile.query.filter_by(university_outlook_email=university_outlook_email).first():
            flash("Coordinator profile with this university Outlook email already exists.", "error")
            errors = True
    if role == User.ROLE_MANAGEMENT:
        if ManagementProfile.query.filter_by(university_outlook_email=university_outlook_email).first():
            flash("Management profile with this university Outlook email already exists.", "error")
            errors = True

    if errors:
        return None

    return {
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


@bp.route("/dashboard")
@roles_required(ROLE_MANAGEMENT)
def dashboard():
    coordinator_rows = (
        db.session.query(User, CoordinatorProfile)
        .join(CoordinatorProfile, CoordinatorProfile.user_id == User.id)
        .order_by(CoordinatorProfile.created_at.desc())
        .all()
    )
    management_rows = (
        db.session.query(User, ManagementProfile)
        .join(ManagementProfile, ManagementProfile.user_id == User.id)
        .order_by(ManagementProfile.created_at.desc())
        .all()
    )
    return render_template(
        "management/dashboard.html",
        coordinator_rows=coordinator_rows,
        management_rows=management_rows,
    )


@bp.route("/accounts/coordinator/new", methods=["GET", "POST"])
@roles_required(ROLE_MANAGEMENT)
def create_coordinator_account():
    if request.method == "POST":
        pending = _build_pending_staff_payload(User.ROLE_COORDINATOR)
        if not pending:
            return render_template(
                "management/create_staff_account.html",
                role_label="Coordinator",
                submit_url=url_for("management.create_coordinator_account"),
            )

        session["pending_staff_account"] = pending
        sent = _issue_otp(pending["gmail_email"], EmailOTP.PURPOSE_STAFF_CREATE)
        if sent:
            flash("OTP sent to the coordinator Gmail account.", "success")
        else:
            flash("OTP created but email delivery failed. Check server logs.", "warning")
        return redirect(url_for("management.verify_staff_otp"))

    return render_template(
        "management/create_staff_account.html",
        role_label="Coordinator",
        submit_url=url_for("management.create_coordinator_account"),
    )


@bp.route("/accounts/management/new", methods=["GET", "POST"])
@roles_required(ROLE_MANAGEMENT)
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
        sent = _issue_otp(pending["gmail_email"], EmailOTP.PURPOSE_STAFF_CREATE)
        if sent:
            flash("OTP sent to the management Gmail account.", "success")
        else:
            flash("OTP created but email delivery failed. Check server logs.", "warning")
        return redirect(url_for("management.verify_staff_otp"))

    return render_template(
        "management/create_staff_account.html",
        role_label="Management",
        submit_url=url_for("management.create_management_account"),
    )


@bp.route("/accounts/verify-otp", methods=["GET", "POST"])
@roles_required(ROLE_MANAGEMENT)
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

        if pending["role"] == User.ROLE_COORDINATOR:
            profile = CoordinatorProfile(
                user_id=user.id,
                phone_number=pending["phone_number"],
                university=pending["university"],
                university_outlook_email=pending["university_outlook_email"],
                position=pending["position"],
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
            role=ROLE_MANAGEMENT,
            details=f"created_user_id={user.id} created_role={user.role} email={user.email}",
        )
        flash(f"{user.role.title()} account created successfully.", "success")
        return redirect(url_for("management.dashboard"))

    return render_template("management/verify_staff_otp.html", pending=pending)


@bp.route("/accounts/coordinator/<int:user_id>/delete", methods=["POST"])
@roles_required(ROLE_MANAGEMENT)
def delete_coordinator_account(user_id: int):
    actor_id = session.get("user_id")
    user = User.query.filter_by(id=user_id, role=User.ROLE_COORDINATOR).first()
    if not user:
        flash("Coordinator account not found.", "error")
        return redirect(url_for("management.dashboard"))

    # Keep historical events, but detach ownership from deleted account.
    Event.query.filter_by(created_by_id=user.id).update({"created_by_id": None}, synchronize_session=False)

    profile = CoordinatorProfile.query.filter_by(user_id=user.id).first()
    if profile:
        db.session.delete(profile)
    db.session.delete(user)
    db.session.commit()

    log_action(
        "staff_account_deleted",
        user_id=actor_id,
        role=ROLE_MANAGEMENT,
        details=f"deleted_user_id={user_id} deleted_role=coordinator",
    )
    flash("Coordinator account deleted.", "success")
    return redirect(url_for("management.dashboard"))


@bp.route("/accounts/management/<int:user_id>/delete", methods=["POST"])
@roles_required(ROLE_MANAGEMENT)
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

    profile = ManagementProfile.query.filter_by(user_id=user.id).first()
    if profile:
        db.session.delete(profile)
    db.session.delete(user)
    db.session.commit()

    log_action(
        "staff_account_deleted",
        user_id=actor_id,
        role=ROLE_MANAGEMENT,
        details=f"deleted_user_id={user_id} deleted_role=management",
    )
    flash("Management account deleted.", "success")
    return redirect(url_for("management.dashboard"))
