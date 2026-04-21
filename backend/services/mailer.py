"""Small SMTP helper used for OTP emails."""
import os
import smtplib
from email.message import EmailMessage


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def send_otp_email(to_email: str, purpose: str, otp_code: str) -> bool:
    host = (os.environ.get("MAIL_HOST") or "").strip()
    port = int((os.environ.get("MAIL_PORT") or "587").strip())
    username = (os.environ.get("MAIL_USERNAME") or "").strip()
    password = os.environ.get("MAIL_PASSWORD") or ""
    from_email = (os.environ.get("MAIL_FROM") or username or "noreply@example.com").strip()
    use_tls = _truthy("MAIL_USE_TLS") if os.environ.get("MAIL_USE_TLS") else True

    subject = "Your OTP Code"
    if purpose == "signup":
        subject = "Verify your student account"
    elif purpose == "password_reset":
        subject = "Reset your password"

    body = (
        "Your one-time password (OTP) is: " + otp_code + "\n\n"
        "This OTP is valid for 10 minutes.\n"
        "If you did not request this, please ignore this email."
    )

    if not host:
        print(f"[OTP-DEV] {purpose} OTP for {to_email}: {otp_code}")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
        return True
    except Exception as exc:
        print(f"[OTP-EMAIL-ERROR] {exc}")
        return False
