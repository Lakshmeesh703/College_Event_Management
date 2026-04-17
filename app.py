"""
College Event Statistics Portal — Flask application entry point.

Run locally:
  export FLASK_APP=app.py
  flask run

Or: python app.py
"""
import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, session
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from models import User, db


def _column_exists(table: str, column: str) -> bool:
    q = text(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = :table_name AND column_name = :column_name
        """
    )
    try:
        return db.session.execute(q, {"table_name": table, "column_name": column}).first() is not None
    except Exception:
        # SQLite fallback
        rows = db.session.execute(text(f"PRAGMA table_info({table})")).all()
        return any(r[1] == column for r in rows)


def _ensure_schema_updates():
    """Simple additive migration helper so existing DBs can run upgraded features."""
    dialect = db.engine.dialect.name
    statements = []

    if not _column_exists("users", "is_external_user"):
        if dialect == "postgresql":
            statements.append("ALTER TABLE users ADD COLUMN is_external_user BOOLEAN NOT NULL DEFAULT FALSE")
        else:
            statements.append("ALTER TABLE users ADD COLUMN is_external_user BOOLEAN NOT NULL DEFAULT 0")

    if not _column_exists("participants", "organization"):
        statements.append("ALTER TABLE participants ADD COLUMN organization VARCHAR(180)")
    if not _column_exists("participants", "whatsapp_number"):
        statements.append("ALTER TABLE participants ADD COLUMN whatsapp_number VARCHAR(10)")

    event_columns = {
        "school": "VARCHAR(160)",
        "registration_deadline": "DATE",
        "max_participants": "INTEGER",
        "allow_external": "BOOLEAN NOT NULL DEFAULT FALSE" if dialect == "postgresql" else "BOOLEAN NOT NULL DEFAULT 0",
        "registration_closed_manually": "BOOLEAN NOT NULL DEFAULT FALSE" if dialect == "postgresql" else "BOOLEAN NOT NULL DEFAULT 0",
        "status": "VARCHAR(32) NOT NULL DEFAULT 'Upcoming'",
    }
    for col, ddl in event_columns.items():
        if not _column_exists("events", col):
            statements.append(f"ALTER TABLE events ADD COLUMN {col} {ddl}")

    for stmt in statements:
        db.session.execute(text(stmt))
    if statements:
        db.session.commit()


def _ensure_default_admin_account():
    """Guarantee one known management login for administration tasks."""
    admin_email = os.environ.get("ADMIN_EMAIL", "cumanagement522@gmail.com").strip().lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "Chinnu@08418")
    admin_name = os.environ.get("ADMIN_NAME", "CU Management Admin").strip() or "CU Management Admin"

    if not admin_email or not admin_password:
        return

    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(
            name=admin_name,
            email=admin_email,
            password_hash=generate_password_hash(admin_password),
            role=User.ROLE_MANAGEMENT,
        )
        db.session.add(admin)
        db.session.commit()
        return

    changed = False
    if admin.role != User.ROLE_MANAGEMENT:
        admin.role = User.ROLE_MANAGEMENT
        changed = True
    if not check_password_hash(admin.password_hash, admin_password):
        admin.password_hash = generate_password_hash(admin_password)
        changed = True
    if not admin.name:
        admin.name = admin_name
        changed = True
    if changed:
        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.permanent_session_lifetime = timedelta(minutes=90)

    os.makedirs("logs", exist_ok=True)
    file_handler = RotatingFileHandler("logs/app.log", maxBytes=1_000_000, backupCount=5)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)

    db.init_app(app)

    # Register blueprints (auth first so login routes exist for redirects)
    from routes.auth import bp as auth_bp
    from routes.coordinator import bp as coordinator_bp
    from routes.main import bp as main_bp
    from routes.management import bp as management_bp
    from routes.participant import bp as participant_bp
    from routes.analytics import bp as analytics_bp
    from routes.api import bp as api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(participant_bp)
    app.register_blueprint(coordinator_bp)
    app.register_blueprint(management_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(analytics_bp, url_prefix="/analytics")
    app.register_blueprint(api_bp)

    @app.context_processor
    def inject_current_user():
        uid = session.get("user_id")
        if not uid:
            return dict(current_user=None)
        return dict(current_user=db.session.get(User, uid))

    with app.app_context():
        db.create_all()
        _ensure_schema_updates()
        _ensure_default_admin_account()

    @app.errorhandler(404)
    def not_found(_error):
        return "Page not found.", 404

    @app.errorhandler(500)
    def server_error(_error):
        return "Unexpected server error. Please try again.", 500

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
