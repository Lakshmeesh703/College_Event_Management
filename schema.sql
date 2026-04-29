-- College Event Statistics Portal — PostgreSQL schema (normalized, 2NF+)
-- Includes users (RBAC) and links to events / participants.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(32) NOT NULL,
    is_external_user BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_role ON users (role);

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    department VARCHAR(120) NOT NULL,
    school VARCHAR(160),
    category VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    registration_deadline DATE,
    max_participants INTEGER,
    allow_external BOOLEAN NOT NULL DEFAULT FALSE,
    registration_closed_manually BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL DEFAULT 'Upcoming',
        brochure_path VARCHAR(500),
    venue VARCHAR(200) NOT NULL,
    organizer VARCHAR(120) NOT NULL,
    created_by_id INTEGER REFERENCES users (id),
    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS ix_events_department ON events (department);
CREATE INDEX IF NOT EXISTS ix_events_school ON events (school);
CREATE INDEX IF NOT EXISTS ix_events_category ON events (category);
CREATE INDEX IF NOT EXISTS ix_events_date ON events (date);
CREATE INDEX IF NOT EXISTS ix_events_registration_deadline ON events (registration_deadline);
CREATE INDEX IF NOT EXISTS ix_events_status ON events (status);
CREATE INDEX IF NOT EXISTS ix_events_created_by_id ON events (created_by_id);

CREATE TABLE IF NOT EXISTS participants (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES users (id),
    name VARCHAR(120) NOT NULL,
    roll_number VARCHAR(40),
    department VARCHAR(120),
    organization VARCHAR(180),
    whatsapp_number VARCHAR(10),
    university_mail VARCHAR(255) UNIQUE,
    year SMALLINT,
    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS ix_participants_user_id ON participants (user_id);
CREATE INDEX IF NOT EXISTS ix_participants_roll_number ON participants (roll_number);
CREATE INDEX IF NOT EXISTS ix_participants_department ON participants (department);
CREATE INDEX IF NOT EXISTS ix_participants_whatsapp_number ON participants (whatsapp_number);
CREATE INDEX IF NOT EXISTS ix_participants_university_mail ON participants (university_mail);

CREATE TABLE IF NOT EXISTS event_participation (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participants (id) ON DELETE CASCADE,
    competition_id INTEGER REFERENCES competitions (id) ON DELETE CASCADE,
    is_external BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    CONSTRAINT uq_event_participant_competition UNIQUE (event_id, participant_id, competition_id)
);

CREATE INDEX IF NOT EXISTS ix_event_participation_event_id ON event_participation (event_id);
CREATE INDEX IF NOT EXISTS ix_event_participation_participant_id ON event_participation (participant_id);
CREATE INDEX IF NOT EXISTS ix_event_participation_competition_id ON event_participation (competition_id);

CREATE TABLE IF NOT EXISTS results (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participants (id) ON DELETE CASCADE,
    competition_id INTEGER REFERENCES competitions (id) ON DELETE CASCADE,
    rank INTEGER,
    prize VARCHAR(200),
    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    CONSTRAINT uq_result_event_participant_competition UNIQUE (event_id, participant_id, competition_id)
);

CREATE INDEX IF NOT EXISTS ix_results_event_id ON results (event_id);
CREATE INDEX IF NOT EXISTS ix_results_participant_id ON results (participant_id);
CREATE INDEX IF NOT EXISTS ix_results_competition_id ON results (competition_id);
CREATE INDEX IF NOT EXISTS ix_results_rank ON results (rank);

CREATE TABLE IF NOT EXISTS competitions (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    rules TEXT,
    max_participants INTEGER,
    date DATE NOT NULL,
        brochure_path VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS ix_competitions_event_id ON competitions (event_id);
CREATE INDEX IF NOT EXISTS ix_competitions_date ON competitions (date);

CREATE TABLE IF NOT EXISTS activity_logs (
    id SERIAL PRIMARY KEY,
    action VARCHAR(80) NOT NULL,
    user_id INTEGER REFERENCES users (id),
    role VARCHAR(32),
    details VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS ix_activity_logs_created_at ON activity_logs (created_at);
CREATE INDEX IF NOT EXISTS ix_activity_logs_user_id ON activity_logs (user_id);
CREATE INDEX IF NOT EXISTS ix_activity_logs_action ON activity_logs (action);
