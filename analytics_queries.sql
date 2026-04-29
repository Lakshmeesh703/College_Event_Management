-- At least 5 analytical SQL queries for the mini-project deliverable.
-- Run against PostgreSQL after loading sample data.

-- ---------------------------------------------------------------------------
-- 1) Department-wise event count (management view)
-- ---------------------------------------------------------------------------
SELECT department, COUNT(*) AS event_count
FROM events
GROUP BY department
ORDER BY event_count DESC;

-- ---------------------------------------------------------------------------
-- 2) Participation statistics per event (volume + internal share)
-- ---------------------------------------------------------------------------
SELECT
    e.id AS event_id,
    e.name AS event_name,
    COUNT(ep.id) AS registration_count,
    SUM(CASE WHEN ep.is_external = FALSE THEN 1 ELSE 0 END) AS internal_count,
    SUM(CASE WHEN ep.is_external = TRUE THEN 1 ELSE 0 END) AS external_count
FROM events e
LEFT JOIN event_participation ep ON ep.event_id = e.id
GROUP BY e.id, e.name
ORDER BY registration_count DESC;

-- ---------------------------------------------------------------------------
-- 3) Top performers (ranked results only)
-- ---------------------------------------------------------------------------
SELECT
    r.rank,
    p.name AS participant_name,
    p.roll_number,
    p.department,
    e.name AS event_name,
    r.prize
FROM results r
JOIN participants p ON p.id = r.participant_id
JOIN events e ON e.id = r.event_id
WHERE r.rank IS NOT NULL
ORDER BY r.rank ASC, e.date DESC
LIMIT 50;

-- ---------------------------------------------------------------------------
-- 4) Monthly event trends (time-series style aggregation)
-- ---------------------------------------------------------------------------
SELECT
    date_trunc('month', date)::date AS month_start,
    COUNT(*) AS events_in_month
FROM events
GROUP BY date_trunc('month', date)
ORDER BY month_start;

-- ---------------------------------------------------------------------------
-- 5) Internal vs external ratio (overall and optional per department of event)
-- ---------------------------------------------------------------------------
SELECT
    SUM(CASE WHEN ep.is_external = FALSE THEN 1 ELSE 0 END) AS internal_regs,
    SUM(CASE WHEN ep.is_external = TRUE THEN 1 ELSE 0 END) AS external_regs,
    ROUND(
        100.0 * SUM(CASE WHEN ep.is_external = FALSE THEN 1 ELSE 0 END)
        / NULLIF(COUNT(ep.id), 0),
        2
    ) AS internal_pct
FROM event_participation ep;

-- ---------------------------------------------------------------------------
-- Bonus: composite-style metric — participations per event by department
--        (derived insight for rubric / report)
-- ---------------------------------------------------------------------------
SELECT
    e.department,
    COUNT(DISTINCT e.id) AS num_events,
    COUNT(ep.id) AS total_registrations,
    ROUND(
        COUNT(ep.id)::numeric / NULLIF(COUNT(DISTINCT e.id), 0),
        2
    ) AS avg_registrations_per_event
FROM events e
LEFT JOIN event_participation ep ON ep.event_id = e.id
GROUP BY e.department
ORDER BY avg_registrations_per_event DESC NULLS LAST;
