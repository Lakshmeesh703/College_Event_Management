"""Small SMTP helper used for OTP emails."""
import logging
import os
import smtplib
from email.message import EmailMessage


logger = logging.getLogger(__name__)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return default


def _otp_fallback_logging_enabled() -> bool:
    # Manual override always wins when explicitly set.
    raw = (os.environ.get("OTP_LOG_ON_EMAIL_FAILURE") or "").strip().lower()
    if raw:
        return raw in ("1", "true", "yes", "on")

    # Dev-mode default fallback; keep strict behavior in production/hosting.
    env_name = (
        _env_first("APP_ENV", "ENV", "FLASK_ENV", default="development")
        .strip()
        .lower()
    )
    is_render = (os.environ.get("RENDER") or "").strip().lower() in ("1", "true", "yes", "on")
    return env_name not in ("production", "prod") and not is_render


def _log_otp_fallback(to_email: str, purpose: str, otp_code: str, reason: str) -> None:
    if not _otp_fallback_logging_enabled():
        return
    logger.warning(
        "[OTP-DEV-FALLBACK] purpose=%s to=%s otp=%s reason=%s",
        purpose,
        to_email,
        otp_code,
        reason,
    )


def _log_smtp_resolution_hint(exc: Exception, host: str, username: str) -> None:
    host_l = (host or "").lower()
    if not isinstance(exc, smtplib.SMTPAuthenticationError):
        return

    smtp_code = getattr(exc, "smtp_code", None)
    smtp_error = getattr(exc, "smtp_error", b"")
    err_text = ""
    if isinstance(smtp_error, bytes):
        err_text = smtp_error.decode("utf-8", errors="ignore").lower()
    else:
        err_text = str(smtp_error).lower()

    if "gmail" in host_l:
        logger.warning(
            "[OTP-EMAIL-HINT] Gmail SMTP auth failed. Use a fresh 16-char App Password "
            "from the same account as EMAIL_USER/MAIL_USERNAME, and keep MAIL_DEFAULT_SENDER equal to that Gmail."
        )
        logger.warning(
            "[OTP-EMAIL-HINT] Verify Google account settings: 2-Step Verification ON, no security alerts, "
            "and app passwords allowed (Workspace admin may disable this)."
        )
        if smtp_code in (534, 535) or "badcredentials" in err_text:
            logger.warning(
                "[OTP-EMAIL-HINT] Received BadCredentials from Gmail. This is provider-side authentication "
                "rejection, not a Flask SMTP transport bug."
            )
        if username and not username.lower().endswith("@gmail.com"):
            logger.warning(
                "[OTP-EMAIL-HINT] EMAIL_USER/MAIL_USERNAME is not a @gmail.com account. For smtp.gmail.com, "
                "authenticate with a Gmail/Google Workspace mailbox and its app password."
            )


def _normalize_gmail_app_password(password: str, host: str) -> str:
    # Gmail app passwords are often shown as 4 groups; SMTP expects them without spaces.
    if "gmail" in (host or "").lower() and " " in password:
        compact = password.replace(" ", "")
        if len(compact) == 16 and compact.isalnum():
            return compact
    return password


def _resolve_mail_security(port: int) -> tuple[bool, bool]:
    tls_raw = _env_first("MAIL_USE_TLS", "SMTP_USE_TLS", "EMAIL_USE_TLS")
    ssl_raw = _env_first("MAIL_USE_SSL", "SMTP_USE_SSL", "EMAIL_USE_SSL")

    tls_set = bool(tls_raw)
    ssl_set = bool(ssl_raw)

    use_tls = tls_raw.strip().lower() in ("1", "true", "yes", "on") if tls_set else False
    use_ssl = ssl_raw.strip().lower() in ("1", "true", "yes", "on") if ssl_set else False

    # Smart defaults for common SMTP ports when flags are not provided.
    if not tls_set and not ssl_set:
        if port == 465:
            return False, True
        return True, False

    # TLS and SSL should not both be enabled.
    if use_ssl and use_tls:
        use_tls = False

    # If SSL is requested, STARTTLS is not needed.
    if use_ssl:
        use_tls = False

    return use_tls, use_ssl


def _login_with_gmail_fallback(server: smtplib.SMTP, username: str, password: str, host: str) -> None:
    try:
        server.login(username, password)
        return
    except smtplib.SMTPAuthenticationError:
        # Gmail app passwords are often copied with spaces; retry once without spaces.
        if "gmail" in host.lower() and " " in password:
            compact = password.replace(" ", "")
            if compact != password:
                server.login(username, compact)
                return
        raise


def send_otp_email(to_email: str, purpose: str, otp_code: str) -> bool:
    # Accept common naming variants used by different hosting guides.
    host = _env_first(
        "MAIL_HOST",
        "MAIL_SERVER",
        "SMTP_HOST",
        "SMTP_SERVER",
        "EMAIL_HOST",
        default="smtp.gmail.com",
    )

    port_raw = _env_first("MAIL_PORT", "SMTP_PORT", "EMAIL_PORT", default="587")
    try:
        port = int(port_raw)
    except ValueError:
        print(f"[OTP-EMAIL-ERROR] Invalid MAIL_PORT/SMTP_PORT: {port_raw!r}")
        return False

    username = _env_first(
        "EMAIL_USER",
        "MAIL_USERNAME",
        "MAIL_USER",
        "SMTP_USERNAME",
        "SMTP_USER",
        "EMAIL_HOST_USER",
        "GMAIL_USER",
    )
    password = _env_first(
        "EMAIL_PASS",
        "MAIL_PASSWORD",
        "SMTP_PASSWORD",
        "SMTP_PASS",
        "MAIL_APP_PASSWORD",
        "GMAIL_APP_PASSWORD",
        "EMAIL_HOST_PASSWORD",
    )
    from_email = _env_first(
        "MAIL_DEFAULT_SENDER",
        "MAIL_FROM",
        "SMTP_FROM",
        "EMAIL_FROM",
        default=username or "noreply@example.com",
    )
    if "@" not in from_email and "@" in username:
        from_email = username

    use_tls, use_ssl = _resolve_mail_security(port)

    subject = "Your OTP Code"
    if purpose == "signup":
        subject = "Verify your student account"
    elif purpose == "password_reset":
        subject = "Reset your password"
    elif purpose == "staff_create":
        subject = "Verify staff account creation"

    body = (
        "Your one-time password (OTP) is: " + otp_code + "\n\n"
        "This OTP is valid for 10 minutes.\n"
        "If you did not request this, please ignore this email."
    )

    if not host:
        logger.info("[OTP-DEV] %s OTP for %s: %s", purpose, to_email, otp_code)
        return False

    # If host is set, treat missing auth as misconfiguration.
    if not username or not password:
        logger.error(
            "[OTP-EMAIL-ERROR] SMTP auth missing: host=%s user_set=%s pass_set=%s",
            host,
            bool(username),
            bool(password),
        )
        _log_otp_fallback(to_email, purpose, otp_code, reason="smtp_auth_missing")
        return False

    if "@" not in from_email:
        logger.error("[OTP-EMAIL-ERROR] MAIL_DEFAULT_SENDER appears invalid: %r", from_email)
        _log_otp_fallback(to_email, purpose, otp_code, reason="invalid_sender")
        return False

    password = _normalize_gmail_app_password(password, host)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    try:
        if use_ssl:
            smtp_client = smtplib.SMTP_SSL
        else:
            smtp_client = smtplib.SMTP

        with smtp_client(host, port, timeout=20) as server:
            server.ehlo()
            if use_tls and not use_ssl:
                server.starttls()
                server.ehlo()
            _login_with_gmail_fallback(server, username, password, host)
            refused = server.send_message(msg)
            if refused:
                logger.error("[OTP-EMAIL-ERROR] Recipients refused: %s", refused)
                _log_otp_fallback(to_email, purpose, otp_code, reason="recipient_refused")
                return False
        logger.info(
            "[OTP-EMAIL-SENT] purpose=%s to=%s via=%s:%s from=%s",
            purpose,
            to_email,
            host,
            port,
            from_email,
        )
        return True
    except Exception as exc:
        logger.error(
            "[OTP-EMAIL-ERROR] host=%s port=%s tls=%s ssl=%s user_set=%s from=%s err=%s",
            host,
            port,
            use_tls,
            use_ssl,
            bool(username),
            from_email,
            exc,
        )
        _log_smtp_resolution_hint(exc, host, username)
        _log_otp_fallback(to_email, purpose, otp_code, reason="smtp_exception")
        return False
