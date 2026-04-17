"""
Application configuration.

Database credentials are read only from environment variables — never hardcode secrets.
Supports DATABASE_URL (cloud hosts) or DB_USER / DB_PASSWORD / DB_HOST / DB_PORT / DB_NAME.

Local quick start without PostgreSQL: set USE_SQLITE_LOCAL=1 (course deploy should still use Postgres).
"""
import os
from urllib.parse import quote_plus

_BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _normalize_database_url(url: str) -> str:
    """Some hosts use postgres:// which SQLAlchemy 2.x expects as postgresql://."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-in-production")

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS: dict = {}

    _db_url = os.environ.get("DATABASE_URL")
    _use_sqlite = _truthy("USE_SQLITE_LOCAL")

    if _use_sqlite:
        # File-based DB for local UI testing only (no separate Postgres install).
        _instance = os.path.join(_BASE_DIR, "instance")
        os.makedirs(_instance, exist_ok=True)
        _sqlite_path = os.path.join(_instance, "college_events.db")
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + _sqlite_path
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False}}
    elif _db_url:
        SQLALCHEMY_DATABASE_URI = _normalize_database_url(_db_url.strip())
    else:
        _user = quote_plus(os.environ.get("DB_USER", "postgres"))
        # quote_plus so @ and : in passwords do not break the URL
        _pw = quote_plus(os.environ.get("DB_PASSWORD", ""))
        _host = os.environ.get("DB_HOST", "localhost")
        _port = os.environ.get("DB_PORT", "5432")
        _name = os.environ.get("DB_NAME", "college_events")
        SQLALCHEMY_DATABASE_URI = (
            f"postgresql://{_user}:{_pw}@{_host}:{_port}/{_name}"
        )
