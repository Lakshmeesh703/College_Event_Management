"""
Data cleaning & standardization using Pandas.

Used by the Flask app on write operations and runnable standalone for batch jobs.
Goals (assignment):
  - Remove / merge duplicate logical rows
  - Standardize department and category strings
  - Handle missing values consistently

Running this file directly loads events/participants from the DB (via app context),
cleans in-memory DataFrames, and prints summaries — idempotent when DB is already clean.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

# Project root on path when executed as script
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# --- Standardization maps (extend as your college uses more aliases) ---
DEPARTMENT_ALIASES = {
    "cse": "Computer Science",
    "computer science": "Computer Science",
    "cs": "Computer Science",
    "ece": "Electronics & Communication",
    "electronic": "Electronics & Communication",
    "mech": "Mechanical",
    "mechanical engg": "Mechanical",
    "civil": "Civil",
    "ise": "Information Science",
    "information science": "Information Science",
    "aiml": "AI & ML",
    "ai & ml": "AI & ML",
}

CATEGORY_ALIASES = {
    "tech": "technical",
    "technical event": "technical",
    "culture": "cultural",
    "cultural event": "cultural",
    "sport": "sports",
    "work shops": "workshop",
    "ws": "workshop",
    "misc": "other",
    "others": "other",
}

VALID_CATEGORIES = frozenset(
    {"technical", "cultural", "sports", "workshop", "other"}
)


def standardize_department(value: str | None) -> str:
    """Normalize department labels for analytics (title case + alias map)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "Unknown"
    s = str(value).strip()
    if not s:
        return "Unknown"
    key = s.lower()
    return DEPARTMENT_ALIASES.get(key, s.title())


def standardize_category(value: str | None) -> str:
    """Map free text to one of the allowed category values."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "other"
    s = str(value).strip().lower()
    if not s:
        return "other"
    mapped = CATEGORY_ALIASES.get(s, s)
    return mapped if mapped in VALID_CATEGORIES else "other"


def fill_missing_roll(df: pd.DataFrame, col: str = "roll_number") -> pd.DataFrame:
    """Use empty string for missing roll numbers (externals / guests)."""
    out = df.copy()
    if col in out.columns:
        out[col] = out[col].fillna("").astype(str).str.strip()
    return out


def drop_duplicate_participants(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop duplicate participants by (roll_number, name) when roll is non-empty,
    else by (name, department) only.
    """
    if df.empty:
        return df
    work = df.copy()
    work["_roll"] = work.get("roll_number", pd.Series(dtype=str)).fillna("").str.upper()
    dup_mask = work["_roll"] != ""
    # For rows with roll: dedupe on roll
    with_roll = work[dup_mask].drop_duplicates(subset=["_roll"], keep="first")
    without_roll = work[~dup_mask].drop_duplicates(
        subset=["name", "department"], keep="first"
    )
    out = pd.concat([with_roll, without_roll], ignore_index=True)
    return out.drop(columns=["_roll"], errors="ignore")


def clean_events_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Apply standardization and drop exact duplicate event rows."""
    if df.empty:
        return df
    out = df.copy()
    out["department"] = out["department"].map(standardize_department)
    out["category"] = out["category"].map(standardize_category)
    out["name"] = out["name"].fillna("").astype(str).str.strip()
    out = out.drop_duplicates(
        subset=["name", "department", "date", "venue"], keep="first"
    )
    return out


def clean_participants_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["department"] = out["department"].map(standardize_department)
    out = fill_missing_roll(out)
    out["name"] = out["name"].fillna("").astype(str).str.strip()
    out = drop_duplicate_participants(out)
    return out


def derived_metrics(events_df: pd.DataFrame, participation_df: pd.DataFrame) -> dict:
    """
    Compute repeatable derived metrics (same input -> same output).

    Returns dict with:
      events_per_department, participations_per_event, monthly_counts, top_performers placeholder
    """
    e = events_df.copy()
    p = participation_df.copy()
    if e.empty:
        return {
            "events_per_department": pd.Series(dtype=int),
            "participations_per_event": pd.Series(dtype=int),
            "monthly_event_counts": pd.Series(dtype=int),
        }
    e["date"] = pd.to_datetime(e["date"])
    events_per_dept = e.groupby("department").size()
    monthly = e.groupby(e["date"].dt.to_period("M")).size()
    out = {
        "events_per_department": events_per_dept,
        "monthly_event_counts": monthly,
    }
    if not p.empty and "event_id" in p.columns:
        out["participations_per_event"] = p.groupby("event_id").size()
    else:
        out["participations_per_event"] = pd.Series(dtype=int)
    return out


def run_demo_report() -> None:
    """Load DB tables into Pandas and print cleaning / metrics (requires DATABASE_URL or DB_*)."""
    from ..app import app
    from ..models import Event, EventParticipation, Participant, db

    with app.app_context():
        bind = db.session.get_bind()
        ev = pd.read_sql_query("SELECT * FROM events", bind)
        pr = pd.read_sql_query("SELECT * FROM participants", bind)
        ep = pd.read_sql_query("SELECT * FROM event_participation", bind)

    print("--- Raw row counts ---")
    print(f"events: {len(ev)}, participants: {len(pr)}, participations: {len(ep)}")

    ev_c = clean_events_dataframe(ev) if not ev.empty else ev
    pr_c = clean_participants_dataframe(pr) if not pr.empty else pr

    print("\n--- After cleaning (in-memory) ---")
    print(f"events: {len(ev_c)}, participants: {len(pr_c)}")

    m = derived_metrics(ev_c, ep)
    print("\n--- Events per department ---")
    print(m["events_per_department"])


if __name__ == "__main__":
    run_demo_report()
