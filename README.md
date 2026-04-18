# College Event Statistics Portal

## Production-ready upgrade highlights

This project now includes a professional upgrade layer while keeping existing workflows intact:

- Advanced management admin dashboard with OTP-based staff account creation.
- Search, filter, sort, and pagination for participant event discovery.
- Export capabilities: filtered analytics CSV and downloadable text summary report.
- REST API layer: /api/events, /api/participants, /api/analytics.
- Activity logging and monitoring for login, event creation, and registrations.
- Stronger input validation and user-friendly error feedback.
- Performance indexing improvements for high-traffic query paths.
- Separate coordinator/management profile sections with account delete actions.
- Compact analytics dashboard with school and student insights + lightweight charts.
- Dummy data generator for realistic demo loads.

Web portal for logging college events, participants, registrations, and competition results, with **Pandas** cleaning utilities, **analytical SQL** examples, and **Matplotlib/Seaborn** charts embedded in a simple **Flask + PostgreSQL** stack.

## How to run (simple)

### Easiest — quick local start

**Linux / macOS:** in the project folder run:

```bash
chmod +x run_local.sh   # first time only
./run_local.sh
```

The script creates **`.env`** (secret only) if missing, creates **`.venv`** and installs packages once, then starts the server. Set either `DATABASE_URL` or all `DB_*` values before running. Open **http://127.0.0.1:5000**. **Ctrl+C** to stop.

**Windows:** double-click **`run_local.bat`** (or run it from Command Prompt in the project folder). Same behaviour.

**Alternative (venv already set up):**

```bash
.venv/bin/python start.py
```

### Manual (any OS)

**1. First time only**

```bash
cd DAV_Mini_Project
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**2. Configure PostgreSQL / Supabase**. Create a file named `.env` next to `app.py` with:

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
SECRET_KEY=my-secret-key
```

Or set all `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, and `DB_NAME`.

**3. Every time you open the project**

```bash
cd DAV_Mini_Project
source .venv/bin/activate
python app.py
```

Open **http://127.0.0.1:5000**.

For your **final submission / cloud deploy**, use **Supabase/PostgreSQL** with `DATABASE_URL` or full `DB_*` values (see below).

---

## Role-based authentication (RBAC)

Three roles control what each logged-in user can do. Passwords are stored with **Werkzeug** hashing (`generate_password_hash` / `check_password_hash`). Sessions keep `user_id`, `user_role`, `user_name`, and `user_email`.

| Role | What they can do |
|------|------------------|
| **participant** | Self **sign up** (`/auth/signup`). Browse events, **register** for events, view **own** participation history. |
| **coordinator** | **Log in** only (no public signup). **Create / edit / delete** events they own; view **participants** on those events; **enter results**. Legacy events with no owner appear under “Legacy events” until a coordinator saves them (claims). |
| **management** | **Admin + analytics**. Can create coordinator/management accounts via OTP verification, view separate staff data sections, delete staff accounts safely, and use `/analytics/dashboard`. Cannot change participant registrations/results directly. |

**Main routes**

- `/auth/login`, `/auth/logout`, `/auth/signup` (participants only), `/auth/access-denied` (403 page).
- `/participant/dashboard`, `/participant/events`, `/participant/history`, POST `/participant/events/<id>/register`.
- `/coordinator/dashboard`, `/coordinator/events/add`, edit/delete, participants list, results per event.
- `/management/dashboard` (admin panel), `/management/accounts/coordinator/new`, `/management/accounts/management/new`, `/management/accounts/verify-otp`, delete routes.
- `/analytics/*` (compact dashboard + charts): **management** role only.

**Code layout**

- `auth_decorators.py` — `login_required`, `roles_required(...)`.
- `routes/auth.py` — login, logout, signup, access denied.
- `routes/participant.py`, `routes/coordinator.py`, `routes/management.py` — role dashboards and actions.
- `models.py` — `User`; `Participant`; `CoordinatorProfile`; `ManagementProfile`; `Event.created_by_id`; `Participant.user_id`.

### Management admin bootstrap account

On startup, the app ensures a default management login exists (unless overridden by env vars):

- Email: `cumanagement522@gmail.com`
- Password: `Chinnu@08418`

Optional overrides:

```bash
export ADMIN_EMAIL=your_admin@gmail.com
export ADMIN_PASSWORD=your_admin_password
export ADMIN_NAME="CU Management Admin"
```

### Staff account creation rules

- Staff creation is done by management via OTP.
- OTP is sent to the entered Gmail account.
- Required fields for coordinator/management profile:
    - Full name
    - Gmail
    - Phone number
    - University
    - Position
    - University mail
- University mail must end with: `@chanakyauniversity.edu.in`

**Demo accounts** (after seeding — see below)

| Email | Password | Role |
|--------|----------|------|
| `coordinator@demo.local` | `coord12345` | coordinator |
| `management@demo.local` | `admin12345` | management |
| `rahul@demo.local` | `student12345` | participant |
| `sneha@demo.local` | `student12345` | participant |

```bash
python scripts/seed_data.py
```

**Upgrading an old database:** `db.create_all()` does not add new columns to existing tables. Run manual `ALTER TABLE` statements (or use `database/schema.sql` as a reference) when upgrading an existing PostgreSQL/Supabase database.

**Adding staff without the seed script** (Flask shell example):

```python
from app import app
from models import db, User
from werkzeug.security import generate_password_hash

with app.app_context():
    u = User(
        name="Dept Head",
        email="head@college.edu",
        password_hash=generate_password_hash("a-strong-password"),
        role=User.ROLE_COORDINATOR,  # or ROLE_MANAGEMENT
    )
    db.session.add(u)
    db.session.commit()
```

---

## Public deployment (course requirement)

1. Host the app on a cloud platform (e.g. [Render](https://render.com), [Railway](https://railway.app), [Fly.io](https://fly.io)).
2. Provision **cloud PostgreSQL** (e.g. [Neon](https://neon.tech), [Supabase](https://supabase.com), Railway, Render Postgres).
3. Set environment variables on the host (see below). **Do not commit secrets.**
4. Add your **live URL** and **public GitHub repo link** in this README when you submit.

The project pins Python 3.12 through `runtime.txt` so Railway and Render do not fall back to Python 3.13, which can force source builds of `pandas` and fail deployment.

**App URL (fill in after deploy):** add your live public URL here after deployment

## Quick setup (local)

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 14+ (local or cloud)

### 2. Clone and virtualenv

```bash
cd DAV_Mini_Project
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Ubuntu / Debian:** If you see *“ensurepip is not available”* when creating the venv, install the venv module first, then retry:

```bash
sudo apt update
sudo apt install python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Always run the app **with the venv activated** (or use `.venv/bin/python` directly). If your system has no `python` command, use `python3` only **after** `source .venv/bin/activate` — inside the venv, `python` works.

### 3. Environment variables

Copy `.env.example` to `.env` and set values.

Either provide a full URL:

```bash
export DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
export SECRET_KEY=use-a-long-random-string
```

Or use separate variables:

```bash
export DB_USER=postgres
export DB_PASSWORD=secret
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=college_events
export SECRET_KEY=use-a-long-random-string
```

### 4. Create tables and run

Flask-SQLAlchemy creates tables on startup (`db.create_all()` in `app.py`).

```bash
source .venv/bin/activate   # if not already active
export FLASK_APP=app.py
flask run
```

Or:

```bash
source .venv/bin/activate
python app.py
```

Open `http://127.0.0.1:5000`.

`run_local.sh` auto-cleans old local `python app.py` processes on port 5000 before starting, so repeated launches are predictable.

### Troubleshooting

| Error | What to do |
|--------|------------|
| `ModuleNotFoundError: No module named 'flask'` | You did not install dependencies or you used `python3` **outside** the venv. Run `source .venv/bin/activate`, then `pip install -r requirements.txt`, then `python app.py`. |
| `ensurepip is not available` | Install `python3-venv` (see Ubuntu steps in section 2). |
| `externally-managed-environment` (pip) | Do not use `pip install` on system Python on newer Ubuntu. Use a **virtual environment** as in section 2. |
| `fe_sendauth: no password supplied` | PostgreSQL is reachable but the app sent an **empty password**. Set **`DB_PASSWORD`** in `.env` to your PostgreSQL user’s password, or use a full **`DATABASE_URL`** that includes the password. Create the DB if needed: `createdb college_events`. |
| Missing database configuration | Set `DATABASE_URL`, or set all `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, and `DB_NAME` values in `.env`. |
| Database connection refused / could not connect | PostgreSQL must be running and `.env` / `DATABASE_URL` must be correct. Create the database (`createdb college_events`) if needed. |

### 5. Optional sample data

```bash
python scripts/seed_data.py
```

Re-running on the same database will try to insert duplicate rows; use a fresh database or truncate tables for a clean demo.

### 6. Data processing report (CLI)

```bash
python scripts/data_processing.py
```

Loads tables with Pandas, applies the same standardization helpers used on form submit, and prints aggregates.

## Production-style run (Gunicorn)

```bash
gunicorn --bind 0.0.0.0:5000 app:app
```

`Procfile` is included for platforms that detect it (e.g. Render).

## Project layout (what each file does)

| Path | Purpose |
|------|--------|
| `app.py` | Creates the Flask app, loads `.env`, registers blueprints, runs `db.create_all()`, injects `current_user` into templates. |
| `auth_decorators.py` | `login_required`, `roles_required(...)` for route protection. |
| `config.py` | Reads `DATABASE_URL` or `DB_*` from the environment; normalizes `postgres://` URLs. |
| `models.py` | `User` (RBAC), `Event` (+ `created_by_id`), `Participant` (+ `user_id`), `EventParticipation`, `Result`. |
| `routes/auth.py` | Login, logout, participant signup, access denied page. |
| `routes/participant.py` | Participant dashboard, event list, register, history. |
| `routes/coordinator.py` | Event CRUD, participants list, results (own / legacy events). |
| `routes/management.py` | Management admin dashboard, OTP staff account creation, staff profile sections, safe account deletion. |
| `routes/main.py` | Public home, calendar, results lookup, legacy URL redirects, optional roll lookup. |
| `routes/analytics.py` | **Management-only** compact dashboard with KPI cards, school/student analytics tables, and chart endpoints. |
| `scripts/data_processing.py` | Pandas cleaning, standardization maps, derived metrics (repeatable). |
| `scripts/seed_data.py` | Demo users (all roles) + sample events / registrations / results. |
| `database/schema.sql` | Plain SQL DDL for submissions / DBA review. |
| `database/analytics_queries.sql` | 5+ analytical queries required for the course. |
| `templates/` | Jinja HTML including `auth/`, `participant/`, `coordinator/`. |
| `static/css/style.css` | Layout, colour theme, nav user chip, danger button. |
| `render.yaml` | Render deployment manifest with Gunicorn startup and managed PostgreSQL. |
| `docs/deployment.md` | Deployment checklist and environment variable setup. |
| `docs/final_report.md` | Coursework-style summary of the system, data model, and delivery. |

## Architecture (high level)

```mermaid
flowchart LR
  Browser --> Flask
  Flask --> SQLAlchemy
  SQLAlchemy --> PostgreSQL
  Flask --> Pandas
  Pandas --> Matplotlib
  Matplotlib --> Browser
```

**ER diagram (logical):**

```mermaid
erDiagram
    users ||--o{ events : creates
    users ||--o| participants : profile
    events ||--o{ event_participation : ""
    participants ||--o{ event_participation : ""
    events ||--o{ results : ""
    participants ||--o{ results : ""
```

- **Normalization:** Event and participant facts are separate; `event_participation` is the associative entity with `is_external`. `results` stores optional `rank`/`prize` for competitive events. **Users** authenticate participants (linked `participants.user_id`) and coordinators (**events.created_by_id**).
- **Timestamps:** Each table has `created_at` for auditability.

## Features checklist (assignment alignment)

- Data entry: events, participants, participation, results (competitive or not).
- Cleaning: duplicates / nulls / department & category aliases in `scripts/data_processing.py` (also applied on write).
- Analytics: events per school, participation per event, monthly trends, top performers, internal vs external, composite **intensity** (participations per event by school), registrations by department/year, and top active students.
- Filters: date range, department, category on the dashboard and charts.
- Stakeholder views: management tables, faculty-oriented stats, student results/history, calendar.

## Analytical SQL

See `database/analytics_queries.sql` for department counts, participation stats, top performers, monthly trends, internal/external ratio, and a bonus composite query.

## License / academic use

Built for coursework; adapt as needed for your institution’s policies.

## API reference

All API responses are JSON.

- GET /api/events
    - Query params: search, department, category, date_from, date_to
    - Returns filtered events list (max 200 rows).
- GET /api/participants
    - Returns participant directory snapshot (max 500 rows).
- GET /api/analytics
    - Returns KPI values and chart-ready series for dashboard consumers.

## Dummy data generator

Use this for heavier demo data and analytics verification:

```bash
python scripts/generate_dummy_data.py
```

Default output target: at least 50 events and 200 participants.

## Screenshots placeholders

- docs/screenshots/dashboard-overview.png
- docs/screenshots/participant-search.png
- docs/screenshots/export-actions.png
- docs/screenshots/api-sample.png

## Deployment suggestions

1. Use Gunicorn behind Nginx or a managed platform like Render/Railway/Fly.
2. Keep DEBUG off, set a strong SECRET_KEY, and never commit secrets.
3. Use managed PostgreSQL with automated backups.
4. Forward logs from logs/app.log to a central logging system.
5. Add CI checks for linting, tests, and API smoke checks.
