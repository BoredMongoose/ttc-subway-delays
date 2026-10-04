-- Analysis tables. "Before" = 2014-2016, "recent" = 2023-2025 (the last three full years).
-- 2026 runs January to August, so it appears only in monthly series.

-- Cause families: who or what caused the delay.
CREATE OR REPLACE MACRO cause_family(g) AS CASE
    WHEN g IN ('Disorderly behaviour & crime', 'Medical emergencies', 'People on the tracks', 'Alarms, doors & clean-ups')
        THEN 'Passengers & public'
    WHEN g IN ('Train equipment', 'Signals & communications', 'Track, power & stations', 'Door cameras (one-person trains)')
        THEN 'Trains, track & signals'
    WHEN g = 'Crew & operations' THEN 'Crew & operations'
    WHEN g IN ('Fire & smoke', 'Weather', 'Planned work & closures') THEN 'Fire, weather & works'
    ELSE 'Other & unclassified' END;

-- 1. Year by line, with an all-lines total -------------------------------------------------------
CREATE OR REPLACE TABLE mart_yearly AS
SELECT
    year,
    COALESCE(line_name, 'All lines')                          AS line_name,
    COUNT(*)                                                  AS incidents_logged,
    COUNT(*) FILTER (WHERE is_delay)                          AS delays,
    SUM(min_delay)                                            AS delay_minutes,
    COUNT(*) FILTER (WHERE is_long)                           AS disruptions_30min,
    ROUND(SUM(min_delay) / NULLIF(COUNT(*) FILTER (WHERE is_delay), 0), 2) AS avg_minutes_per_delay
FROM incidents
WHERE year <= 2025
GROUP BY GROUPING SETS ((year, line_name), (year))
ORDER BY year, line_name;

-- 2. Cause group by year --------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart_cause_year AS
SELECT year, cause_group, cause_family(cause_group) AS cause_family,
       SUM(min_delay) AS delay_minutes, COUNT(*) FILTER (WHERE is_delay) AS delays
FROM incidents
WHERE year <= 2025
GROUP BY ALL
ORDER BY year, delay_minutes DESC;

-- 3. What drove the increase: average year 2014-16 vs 2023-25 ------------------------------------
CREATE OR REPLACE TABLE mart_cause_change AS
WITH periods AS (
    SELECT cause_group,
           SUM(min_delay) FILTER (WHERE year BETWEEN 2014 AND 2016) / 3.0 AS minutes_2014_16,
           SUM(min_delay) FILTER (WHERE year BETWEEN 2023 AND 2025) / 3.0 AS minutes_2023_25
    FROM incidents
    GROUP BY cause_group
)
SELECT cause_group,
       cause_family(cause_group)                                   AS cause_family,
       ROUND(minutes_2014_16)                                      AS minutes_2014_16,
       ROUND(minutes_2023_25)                                      AS minutes_2023_25,
       ROUND(minutes_2023_25 - minutes_2014_16)                    AS change,
       ROUND(100 * (minutes_2023_25 / minutes_2014_16 - 1), 1)     AS change_pct,
       ROUND(100 * (minutes_2023_25 - minutes_2014_16)
             / SUM(minutes_2023_25 - minutes_2014_16) OVER (), 1)  AS share_of_net_increase_pct,
       ROUND(100 * minutes_2023_25 / SUM(minutes_2023_25) OVER (), 1) AS share_of_recent_minutes_pct
FROM periods
ORDER BY change DESC;

-- 4. Monthly totals with a rolling 12-month sum --------------------------------------------------
CREATE OR REPLACE TABLE mart_monthly AS
WITH m AS (
    SELECT month, SUM(min_delay) AS delay_minutes, COUNT(*) FILTER (WHERE is_delay) AS delays
    FROM incidents GROUP BY month
)
SELECT month, delay_minutes, delays,
       SUM(delay_minutes) OVER (ORDER BY month ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS minutes_rolling_12m,
       COUNT(*)           OVER (ORDER BY month ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS months_in_window
FROM m
ORDER BY month;

-- 5. Signal failures by line and month (the Line 1 ATC comparison) -------------------------------
--    Every month x line is present, with zeros where nothing failed.
CREATE OR REPLACE TABLE mart_signal_monthly AS
WITH months AS (
    SELECT CAST(range AS DATE) AS month
    FROM range(DATE '2014-01-01', DATE '2026-09-01', INTERVAL 1 MONTH)
),
lines AS (SELECT * FROM (VALUES ('1'), ('2'), ('4')) AS t(line)),
sig AS (
    SELECT month, line,
           SUM(min_delay)                    AS signal_minutes,
           COUNT(*) FILTER (WHERE is_delay)  AS signal_delays,
           COUNT(*)                          AS signal_events
    FROM incidents WHERE is_signal_failure GROUP BY ALL
),
other AS (
    SELECT month, line, SUM(min_delay) AS other_minutes
    FROM incidents WHERE NOT is_signal_failure GROUP BY ALL
)
SELECT m.month, 'Line ' || l.line AS line_name,
       CASE WHEN m.month < DATE '2017-01-01' THEN 'before'
            WHEN m.month < DATE '2022-10-01' THEN 'transition' ELSE 'after' END AS atc_period,
       COALESCE(s.signal_minutes, 0) AS signal_minutes,
       COALESCE(s.signal_delays, 0)  AS signal_delays,
       COALESCE(s.signal_events, 0)  AS signal_events,
       COALESCE(o.other_minutes, 0)  AS other_minutes
FROM months m
CROSS JOIN lines l
LEFT JOIN sig s   ON s.month = m.month AND s.line = l.line
LEFT JOIN other o ON o.month = m.month AND o.line = l.line
ORDER BY m.month, line_name;

-- 6. Which kinds of signal failure, before vs after ATC ------------------------------------------
CREATE OR REPLACE TABLE mart_signal_codes AS
SELECT line_name, atc_period, code, description,
       COUNT(*)                          AS events,
       COUNT(*) FILTER (WHERE is_delay)  AS delays,
       SUM(min_delay)                    AS delay_minutes
FROM incidents
WHERE is_signal_failure AND line IN ('1', '2') AND atc_period IN ('before', 'after')
GROUP BY ALL
ORDER BY line_name, atc_period, events DESC;

-- 7. Door-monitoring (one-person train operation) delays -----------------------------------------
CREATE OR REPLACE TABLE mart_door_monitoring AS
SELECT year, line_name,
       SUM(min_delay)                    AS delay_minutes,
       COUNT(*) FILTER (WHERE is_delay)  AS delays
FROM incidents
WHERE code IN ('PUOPO', 'TUOPO', 'EUOPO') AND line IN ('1', '4')
GROUP BY ALL
ORDER BY year, line_name;

-- 8. When: average delay minutes per weekday-hour, 2023-2025 --------------------------------------
CREATE OR REPLACE TABLE mart_hour_weekday AS
WITH days AS (
    SELECT dayname(CAST(range AS DATE)) AS weekday, COUNT(*) AS n_days
    FROM range(DATE '2023-01-01', DATE '2026-01-01', INTERVAL 1 DAY)
    GROUP BY 1
)
SELECT i.weekday, i.hour,
       ROUND(SUM(i.min_delay) / d.n_days, 2)                  AS minutes_per_day,
       ROUND(COUNT(*) FILTER (WHERE i.is_delay) / d.n_days, 3) AS delays_per_day
FROM incidents i
JOIN days d USING (weekday)
WHERE i.year BETWEEN 2023 AND 2025
GROUP BY i.weekday, i.hour, d.n_days
ORDER BY i.weekday, i.hour;

-- 9. Where: stations, 2023-2025 (between-station incidents count at the first station named) ----
CREATE OR REPLACE TABLE mart_station AS
WITH s AS (
    SELECT station, cause_group, SUM(min_delay) AS minutes, COUNT(*) FILTER (WHERE is_delay) AS delays
    FROM incidents
    WHERE year BETWEEN 2023 AND 2025 AND location_type IN ('station', 'between stations')
    GROUP BY ALL
),
tot AS (
    SELECT station, SUM(minutes) AS delay_minutes, SUM(delays) AS delays FROM s GROUP BY station
),
top_cause AS (
    SELECT station, cause_group AS top_cause, minutes AS top_cause_minutes,
           ROW_NUMBER() OVER (PARTITION BY station ORDER BY minutes DESC) AS rn
    FROM s
)
SELECT t.station, t.delay_minutes, t.delays,
       ROUND(t.delay_minutes / 3.0)                        AS minutes_per_year,
       c.top_cause,
       ROUND(100 * c.top_cause_minutes / t.delay_minutes, 1) AS top_cause_share_pct,
       RANK() OVER (ORDER BY t.delay_minutes DESC)         AS rank
FROM tot t JOIN top_cause c ON c.station = t.station AND c.rn = 1
ORDER BY rank;

-- 10. Individual delay codes, 2023-2025 ----------------------------------------------------------
CREATE OR REPLACE TABLE mart_codes AS
SELECT code, description, cause_group,
       SUM(min_delay)                                             AS delay_minutes,
       COUNT(*) FILTER (WHERE is_delay)                           AS delays,
       COUNT(*)                                                   AS incidents_logged,
       ROUND(100 * SUM(min_delay) / SUM(SUM(min_delay)) OVER (), 1) AS share_of_minutes_pct,
       RANK() OVER (ORDER BY SUM(min_delay) DESC)                 AS rank
FROM incidents
WHERE year BETWEEN 2023 AND 2025
GROUP BY ALL
ORDER BY rank;

-- 11. How long delays last, 2023-2025 ------------------------------------------------------------
CREATE OR REPLACE TABLE mart_severity AS
WITH b AS (
    SELECT CASE WHEN min_delay = 0 THEN '0 (no delay)'
                WHEN min_delay < 5 THEN '1-4 min'
                WHEN min_delay < 10 THEN '5-9 min'
                WHEN min_delay < 30 THEN '10-29 min'
                WHEN min_delay < 60 THEN '30-59 min'
                ELSE '60+ min' END AS bucket,
           CASE WHEN min_delay = 0 THEN 0 WHEN min_delay < 5 THEN 1 WHEN min_delay < 10 THEN 2
                WHEN min_delay < 30 THEN 3 WHEN min_delay < 60 THEN 4 ELSE 5 END AS bucket_order,
           min_delay
    FROM incidents WHERE year BETWEEN 2023 AND 2025
)
SELECT bucket, bucket_order, COUNT(*) AS incidents, SUM(min_delay) AS delay_minutes,
       ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)           AS share_of_incidents_pct,
       ROUND(100 * SUM(min_delay) / SUM(SUM(min_delay)) OVER (), 1) AS share_of_minutes_pct
FROM b GROUP BY bucket, bucket_order ORDER BY bucket_order;
