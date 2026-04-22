"""
Authentication: login, logout, participant signup, access denied.

Passwords are hashed with werkzeug (pbkdf2). Sessions store user_id and display fields.
"""
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..auth_decorators import (
    ROLE_STUDENT,
    ROLE_PARTICIPANT,
    clear_session,
    login_required,
)
from ..models import EmailOTP, Participant, User, db
from ..services.activity_logger import log_action
from ..services.mailer import send_otp_email
from ..services.validation import clean_text, parse_int_in_range, parse_whatsapp_number
from ..scripts.data_processing import standardize_department

bp = Blueprint("auth", __name__, url_prefix="/auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_OTP_RE = re.compile(r"^\d{6}$")
_OTP_TTL_MINUTES = 10


def _valid_email(email: str) -> bool:
    return bool(email and _EMAIL_RE.match(email.strip()))


def _valid_university_mail(email: str) -> bool:
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

    sent = send_otp_email(email, purpose, code)
    return sent


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


def dashboard_url_for_role(role: str) -> str:
    """Used after login and from the home page to send users to the right hub."""
    if role in (User.ROLE_STUDENT, User.ROLE_PARTICIPANT):
        return url_for("participant.dashboard")
    if role in (User.ROLE_COORDINATOR, User.ROLE_CONVENER):
        return url_for("coordinator.dashboard")
    if role == User.ROLE_ADMIN:
        return url_for("management.dashboard")
    if role == User.ROLE_MANAGEMENT:
        return url_for("analytics.dashboard")
    return url_for("main.index")


def _dashboard_for_role(role: str) -> str:
    return dashboard_url_for_role(role)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(_dashboard_for_role(session.get("user_role", "")))

    if request.method == "POST":
        login_id = (request.form.get("email") or "").strip()
        email = login_id.lower()
        raw_password = request.form.get("password") or ""
        password = raw_password.strip()
        nxt = request.form.get("next") or request.args.get("next") or ""

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("auth/login.html", next=nxt)

        user = User.query.filter_by(email=email).first()
        if not user:
            user = User.query.filter(db.func.lower(User.name) == login_id.lower()).first()
        password_ok = False
        if user:
            # Compatibility path: support older accounts created with unintended
            # leading/trailing spaces before signup trimming was enforced.
            password_ok = check_password_hash(user.password_hash, password)
            if not password_ok and raw_password != password:
                password_ok = check_password_hash(user.password_hash, raw_password)
        if not user or not password_ok:
            log_action("login_failed", details=f"login_id={login_id}")
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html", next=nxt)

        if not user.is_active:
            log_action("login_failed_inactive", user_id=user.id, role=user.role, details=f"email={user.email}")
            flash("This account is deactivated. Contact admin.", "error")
            return render_template("auth/login.html", next=nxt)

        session["user_id"] = user.id
        session["user_role"] = ROLE_STUDENT if user.role == ROLE_PARTICIPANT else user.role
        session["user_name"] = user.name
        session["user_email"] = user.email
        session.permanent = True
        log_action("login", user_id=user.id, role=user.role, details=f"email={user.email}")

        flash(f"Welcome back, {user.name}!", "success")
        if nxt and nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(_dashboard_for_role(user.role))

    return render_template("auth/login.html", next=request.args.get("next") or "")


@bp.route("/logout")
@login_required
def logout():
    uid = session.get("user_id")
    role = session.get("user_role")
    clear_session()
    log_action("logout", user_id=uid, role=role)
    flash("You have been logged out.", "success")
    return redirect(url_for("main.index"))


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Only students may self-register (coordinators & management are created by admin)."""
    if session.get("user_id"):
        flash("You are already logged in.", "warning")
        return redirect(_dashboard_for_role(session.get("user_role", "")))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        university_mail = (request.form.get("university_mail") or "").strip().lower()
        password = (request.form.get("password") or "").strip()
        confirm = (request.form.get("confirm_password") or "").strip()
        roll = (request.form.get("roll_number") or "").strip() or None
        department = (request.form.get("department") or "").strip()
        organization = (request.form.get("organization") or "").strip() or None
        whatsapp_number_raw = (request.form.get("whatsapp_number") or "").strip()
        is_external = request.form.get("is_external_user") == "on"
        year_raw = (request.form.get("year") or "").strip()

        errors = False
        try:
            name = clean_text(name, min_len=2, max_len=120)
        except ValueError as exc:
            flash(f"Name error: {exc}", "error")
            errors = True
        if not _valid_email(email):
            flash("Please enter a valid email address.", "error")
            errors = True

        if not _valid_email(university_mail):
            flash("Please enter a valid university mail address.", "error")
            errors = True
        elif not is_external and not _valid_university_mail(university_mail):
            flash("For non-external students, university mail must end with @chanakyauniversity.edu.in.", "error")
            errors = True
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            errors = True
        if password != confirm:
            flash("Passwords do not match.", "error")
            errors = True

        try:
            whatsapp_number = parse_whatsapp_number(whatsapp_number_raw, required=True)
        except ValueError as exc:
            flash(str(exc), "error")
            whatsapp_number = None
            errors = True
        try:
            department = clean_text(department, min_len=2, max_len=120)
        except ValueError as exc:
            flash(f"Department error: {exc}", "error")
            errors = True
        department = standardize_department(department) if department else ""

        if not organization:
            flash("Organization/college is required.", "error")
            errors = True
        else:
            try:
                organization = clean_text(organization, min_len=2, max_len=180)
            except ValueError as exc:
                flash(f"Organization/college error: {exc}", "error")
                errors = True

        if is_external:
            roll = None
        elif not roll:
            flash("Roll number is required for non-external students.", "error")
            errors = True

        try:
            year = parse_int_in_range(year_raw, min_value=1, max_value=6, required=True)
        except ValueError as exc:
            flash(f"Year error: {exc}", "error")
            year = None
            errors = True

        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists. Try logging in.", "error")
            errors = True

        if Participant.query.filter_by(university_mail=university_mail).first():
            flash("This university mail is already linked to another account.", "error")
            errors = True

        if errors:
            return render_template("auth/signup.html")

        session["pending_signup"] = {
            "name": name,
            "email": email,
            "university_mail": university_mail,
            "password_hash": generate_password_hash(password),
            "roll": roll,
            "department": department,
            "organization": organization,
            "whatsapp_number": whatsapp_number,
            "year": year,
            "is_external": is_external,
        }
        sent = _issue_otp(email, EmailOTP.PURPOSE_SIGNUP)
        if sent:
            flash("OTP sent to your email. Enter it to complete signup.", "success")
        else:
            flash("OTP generated. Email not configured, check server output for OTP.", "warning")
        return redirect(url_for("auth.verify_signup_otp"))

    return render_template("auth/signup.html")


@bp.route("/verify-signup-otp", methods=["GET", "POST"])
def verify_signup_otp():
    pending = session.get("pending_signup")
    if not pending:
        flash("Start signup first.", "warning")
        return redirect(url_for("auth.signup"))

    email = pending.get("email")
    if request.method == "POST":
        otp_code = (request.form.get("otp_code") or "").strip()
        if not _OTP_RE.match(otp_code):
            flash("Enter a valid 6-digit OTP.", "error")
            return render_template("auth/verify_signup_otp.html", email=email)

        if not _consume_otp(email, EmailOTP.PURPOSE_SIGNUP, otp_code):
            flash("Invalid or expired OTP.", "error")
            return render_template("auth/verify_signup_otp.html", email=email)

        if User.query.filter_by(email=email).first():
            session.pop("pending_signup", None)
            flash("Account already exists. Please log in.", "warning")
            return redirect(url_for("auth.login"))

        user = User(
            name=pending["name"],
            email=email,
            password_hash=pending["password_hash"],
            role=User.ROLE_STUDENT,
            is_external_user=bool(pending.get("is_external")),
        )
        db.session.add(user)
        db.session.flush()

        profile = Participant(
            user_id=user.id,
            name=pending["name"],
            roll_number=pending.get("roll"),
            department=pending.get("department"),
            organization=pending.get("organization"),
            whatsapp_number=pending.get("whatsapp_number"),
            university_mail=pending.get("university_mail"),
            year=pending.get("year"),
        )
        db.session.add(profile)
        db.session.commit()

        session.pop("pending_signup", None)
        session["user_id"] = user.id
        session["user_role"] = ROLE_STUDENT
        session["user_name"] = user.name
        session["user_email"] = user.email
        session.permanent = True
        log_action("signup", user_id=user.id, role=user.role, details=f"email={user.email}")
        flash("Account verified and created successfully.", "success")
        return redirect(url_for("participant.dashboard"))

    return render_template("auth/verify_signup_otp.html", email=email)


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not _valid_email(email):
            flash("Please enter a valid email address.", "error")
            return render_template("auth/forgot_password.html")

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("This email is not registered. Please sign up first.", "error")
            return render_template("auth/forgot_password.html")

        sent = _issue_otp(email, EmailOTP.PURPOSE_PASSWORD_RESET)
        session["pending_reset_email"] = email
        if sent:
            flash("OTP sent to your email.", "success")
        else:
            flash("OTP generated, but email delivery failed. Please check server log or retry.", "warning")
        return redirect(url_for("auth.reset_password"))

    return render_template("auth/forgot_password.html")


@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    pending_email = session.get("pending_reset_email", "")
    if request.method == "POST":
        email = (request.form.get("email") or pending_email or "").strip().lower()
        otp_code = (request.form.get("otp_code") or "").strip()
        password = (request.form.get("password") or "").strip()
        confirm = (request.form.get("confirm_password") or "").strip()

        if not _valid_email(email):
            flash("Please enter a valid email address.", "error")
            return render_template("auth/reset_password.html", email=email)
        if not _OTP_RE.match(otp_code):
            flash("Enter a valid 6-digit OTP.", "error")
            return render_template("auth/reset_password.html", email=email)
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/reset_password.html", email=email)
        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("auth/reset_password.html", email=email)

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("Invalid email or OTP.", "error")
            return render_template("auth/reset_password.html", email=email)
        if not _consume_otp(email, EmailOTP.PURPOSE_PASSWORD_RESET, otp_code):
            flash("Invalid or expired OTP.", "error")
            return render_template("auth/reset_password.html", email=email)

        user.password_hash = generate_password_hash(password)
        db.session.commit()
        session.pop("pending_reset_email", None)
        log_action("password_reset", user_id=user.id, role=user.role, details=f"email={email}")
        flash("Password updated. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", email=pending_email)


@bp.route("/access-denied")
def access_denied():
    """Shown when a logged-in user hits a route their role cannot use."""
    return render_template("access_denied.html"), 403
