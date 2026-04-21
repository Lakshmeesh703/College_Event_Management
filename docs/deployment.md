# Deployment Guide

This project is ready to deploy on Render or a similar Python hosting platform.

## Required files

- `render.yaml` for one-click deployment on Render
- `Procfile` for platform detection and Gunicorn startup
- `.env.example` for local and cloud environment variable setup

## Recommended deployment flow

1. Push the repository to a public GitHub repo.
2. Create a new Render Web Service from the repository.
3. Use the provided `render.yaml` or set:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn --bind 0.0.0.0:$PORT backend.app:app`
4. Provision PostgreSQL through Render or another managed database provider.
5. Set the following environment variables:
   - `SECRET_KEY`
   - `DATABASE_URL`
6. Deploy and verify the login page, dashboard, analytics, and API endpoints.

## Local testing mode

Use the same PostgreSQL/Supabase setup locally as production by setting `DATABASE_URL`
or all `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, and `DB_NAME` values.
