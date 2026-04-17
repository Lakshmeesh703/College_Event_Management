"""
Role-based access control using Flask sessions.

- login_required: must be logged in (any role).
- roles_required("a", "b"): must be logged in AND role must be one of allowed values.

Unauthorized users are sent to the login page (with ?next=) or the access-denied page.
"""
from functools import wraps

from flask import redirect, request, session, url_for


# Keep in sync with User.ROLE_* and signup validation
ROLE_STUDENT = "student"
ROLE_PARTICIPANT = "participant"  # legacy alias
ROLE_COORDINATOR = "coordinator"
ROLE_MANAGEMENT = "management"
ALL_ROLES = (ROLE_STUDENT, ROLE_PARTICIPANT, ROLE_COORDINATOR, ROLE_MANAGEMENT)


def _normalized_role(role: str | None) -> str:
    if role == ROLE_PARTICIPANT:
        return ROLE_STUDENT
    return role or ""


def login_required(view_fn):
    """Require a valid session user_id."""

    @wraps(view_fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.url))
        return view_fn(*args, **kwargs)

    return wrapped


def roles_required(*allowed_roles):
    """
    Require login and one of the given roles (strings).
    Example: @roles_required(ROLE_COORDINATOR)
    """

    def decorator(view_fn):
        @wraps(view_fn)
        def wrapped(*args, **kwargs):
            uid = session.get("user_id")
            if not uid:
                return redirect(url_for("auth.login", next=request.url))
            from models import User, db

            user = db.session.get(User, uid)
            allowed = {_normalized_role(r) for r in allowed_roles}
            role = _normalized_role(user.role if user else None)
            if not user or role not in allowed:
                return redirect(url_for("auth.access_denied"))
            return view_fn(*args, **kwargs)

        return wrapped

    return decorator


def current_user_id() -> int | None:
    return session.get("user_id")


def clear_session():
    session.pop("user_id", None)
    session.pop("user_role", None)
    session.pop("user_name", None)
    session.pop("user_email", None)
