"""Small SMTP helper used for OTP emails."""
import os
import smtplib
from email.message import EmailMessage


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return default


def send_otp_email(to_email: str, purpose: str, otp_code: str) -> bool:
    # Accept common naming variants used by different hosting guides.
    host = _env_first("MAIL_HOST", "MAIL_SERVER", "SMTP_HOST")

    port_raw = _env_first("MAIL_PORT", "SMTP_PORT", default="587")
    try:
        port = int(port_raw)
    except ValueError:
        print(f"[OTP-EMAIL-ERROR] Invalid MAIL_PORT/SMTP_PORT: {port_raw!r}")
        return False

    username = _env_first("MAIL_USERNAME", "MAIL_USER", "SMTP_USERNAME", "SMTP_USER")
    password = _env_first("MAIL_PASSWORD", "SMTP_PASSWORD", "SMTP_PASS")
    from_email = _env_first("MAIL_FROM", "SMTP_FROM", default=username or "noreply@example.com")
    if "@" not in from_email and "@" in username:
        from_email = username

    use_tls = _truthy("MAIL_USE_TLS") if os.environ.get("MAIL_USE_TLS") else True
    use_ssl = _truthy("MAIL_USE_SSL") if os.environ.get("MAIL_USE_SSL") else False

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
        print(f"[OTP-DEV] {purpose} OTP for {to_email}: {otp_code}")
        return False

    # If host is set, treat missing auth as misconfiguration (common on Render env setup).
    if not username or not password:
        print(
            "[OTP-EMAIL-ERROR] SMTP auth missing: "
            f"host={host} user_set={bool(username)} pass_set={bool(password)}"
        )
        return False

    if "@" not in from_email:
        print(f"[OTP-EMAIL-ERROR] MAIL_FROM appears invalid: {from_email!r}")
        return False

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
            server.login(username, password)
            refused = server.send_message(msg)
            if refused:
                print(f"[OTP-EMAIL-ERROR] Recipients refused: {refused}")
                return False
        print(
            "[OTP-EMAIL-SENT] "
            f"purpose={purpose} to={to_email} via={host}:{port} from={from_email}"
        )
        return True
    except Exception as exc:
        print(
            "[OTP-EMAIL-ERROR] "
            f"host={host} port={port} tls={use_tls} ssl={use_ssl} "
            f"user_set={bool(username)} from={from_email} err={exc}"
        )
        return False
