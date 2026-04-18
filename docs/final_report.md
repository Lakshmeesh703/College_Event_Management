# Final Report

## Project Title
College Event Statistics Portal

## Objective
Build a web portal for managing college events, participant registrations, result entry, and analytics for coursework submission.

## What the system does

- Supports role-based access for participants, coordinators, and management.
- Allows event creation, editing, registration, and result publishing.
- Provides public pages for browsing events and viewing results.
- Includes a management analytics dashboard with filters and charts.
- Exposes JSON APIs for events, participants, and analytics.
- Includes sample data generation and Pandas-based cleaning utilities.

## Data Model

The core entities are:

- `users` for authentication and role management
- `events` for event metadata and lifecycle status
- `participants` for participant profiles
- `event_participation` for registrations
- `results` for event outcomes
- `activity_logs` for audit tracking

The model is normalized so registrations and results are stored separately from event and participant records.

## Analytical Features

The repository includes:

- SQL analysis scripts in `database/analytics_queries.sql`
- Dashboard KPIs and charts for management users
- Event and participation filtering
- Exportable analytics output
- A data-processing script that standardizes repeated values and prepares summary outputs

## UI Highlights

- Single login-first landing flow
- Public access links for event browsing and results lookup
- Separate event partitions for Upcoming, Ongoing, and Completed events
- Responsive dashboard and card-based layout

## Deployment Readiness

The project includes:

- `Procfile` for Gunicorn startup
- `render.yaml` for Render deployment
- `.env.example` for environment variable setup
- PostgreSQL/Supabase-first configuration for local and cloud runs

## Notes

A live public URL must still be added after deployment for final submission.
