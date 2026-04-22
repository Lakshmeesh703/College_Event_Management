from flask import current_app

from ..models import ActivityLog, db


def log_action(action: str, *, user_id=None, role=None, details: str | None = None):
    """Persist critical user actions for auditing and observability."""
    try:
        row = ActivityLog(
            action=action,
            user_id=user_id,
            role=role,
            details=(details or "")[:500],
        )
        db.session.add(row)
        db.session.commit()
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        current_app.logger.exception("Failed to persist activity log: %s", exc)

    current_app.logger.info("AUDIT action=%s user_id=%s role=%s details=%s", action, user_id, role, details)
