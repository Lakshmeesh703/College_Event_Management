from datetime import datetime
import re


_WHATSAPP_RE = re.compile(r"^\d{10}$")


def clean_text(value: str | None, *, min_len: int = 1, max_len: int = 200) -> str:
    text = (value or "").strip()
    if len(text) < min_len:
        raise ValueError(f"Must be at least {min_len} characters.")
    if len(text) > max_len:
        raise ValueError(f"Must be at most {max_len} characters.")
    return text


def parse_iso_date(value: str | None):
    if not value:
        raise ValueError("Date is required.")
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Date must be in YYYY-MM-DD format.") from exc


def parse_int_in_range(value: str | None, *, min_value: int, max_value: int, required: bool = False):
    raw = (value or "").strip()
    if not raw:
        if required:
            raise ValueError("Value is required.")
        return None
    try:
        out = int(raw)
    except ValueError as exc:
        raise ValueError("Value must be a number.") from exc

    if out < min_value or out > max_value:
        raise ValueError(f"Value must be between {min_value} and {max_value}.")
    return out


def parse_whatsapp_number(value: str | None, *, required: bool = True) -> str | None:
    raw = (value or "").strip()
    if not raw:
        if required:
            raise ValueError("WhatsApp number is required.")
        return None
    if not _WHATSAPP_RE.match(raw):
        raise ValueError("WhatsApp number must be a 10-digit number.")
    return raw
