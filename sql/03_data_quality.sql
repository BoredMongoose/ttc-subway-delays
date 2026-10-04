-- Data-quality checks. src/run_sql.py stops the pipeline if any check fails.

CREATE OR REPLACE TABLE dq_results AS
WITH log AS (SELECT step, rows FROM cleaning_log),
expected_months AS (
    SELECT CAST(range AS DATE) AS month FROM range(DATE '2014-01-01', DATE '2026-09-01', INTERVAL 1 MONTH)
),
monthly AS (SELECT month, COUNT(*) AS n FROM incidents GROUP BY month)

-- every raw row is accounted for: clean rows + removed duplicates = raw rows
SELECT 'row reconciliation' AS check_name,
       format('raw {} = clean {} + duplicates {}',
              (SELECT rows FROM log WHERE step = 'raw rows'), (SELECT COUNT(*) FROM incidents),
              (SELECT rows FROM log WHERE step = 'exact duplicates removed')) AS detail,
       (SELECT rows FROM log WHERE step = 'raw rows')
         = (SELECT COUNT(*) FROM incidents) + (SELECT rows FROM log WHERE step = 'exact duplicates removed') AS passed

UNION ALL  -- no month is missing and none is suspiciously thin
SELECT 'no missing months',
       format('{} of {} months present, smallest has {} incidents',
              (SELECT COUNT(*) FROM monthly), (SELECT COUNT(*) FROM expected_months), (SELECT MIN(n) FROM monthly)),
       (SELECT COUNT(*) FROM expected_months e LEFT JOIN monthly m USING (month) WHERE COALESCE(m.n, 0) < 500) = 0

UNION ALL
SELECT 'dates in range', format('{} to {}', MIN(date), MAX(date)),
       MIN(date) >= DATE '2014-01-01' AND MAX(date) <= DATE '2026-08-31' FROM incidents

UNION ALL
SELECT 'no negative minutes', format('{} rows', COUNT(*) FILTER (WHERE min_delay < 0 OR min_gap < 0)),
       COUNT(*) FILTER (WHERE min_delay < 0 OR min_gap < 0) = 0 FROM incidents

UNION ALL
SELECT 'no duplicate rows left',
       format('{} duplicates', COUNT(*) - COUNT(DISTINCT (date, time, location_raw, code, min_delay, min_gap, bound, line_raw, vehicle))),
       COUNT(*) = COUNT(DISTINCT (date, time, location_raw, code, min_delay, min_gap, bound, line_raw, vehicle))
FROM incidents

UNION ALL  -- the line field had 111 spellings; after cleaning only these values may remain
SELECT 'line values cleaned', string_agg(DISTINCT line, ', ' ORDER BY line),
       bool_and(line IN ('1', '2', '3', '4', 'multiple', 'unknown')) FROM incidents

UNION ALL
SELECT 'line known for 99.5%+ of rows',
       format('{}% unknown', ROUND(100 * AVG(CASE WHEN line = 'unknown' THEN 1 ELSE 0 END), 2)),
       AVG(CASE WHEN line = 'unknown' THEN 1 ELSE 0 END) <= 0.005 FROM incidents

UNION ALL
SELECT 'location matched for 99.5%+ of rows',
       format('{}% unmatched', ROUND(100 * AVG(CASE WHEN location_type = 'unknown' THEN 1 ELSE 0 END), 2)),
       AVG(CASE WHEN location_type = 'unknown' THEN 1 ELSE 0 END) <= 0.005 FROM incidents

UNION ALL
SELECT 'station names resolve to 75 stations', format('{} distinct stations', COUNT(DISTINCT station)),
       COUNT(DISTINCT station) = 75 FROM incidents

UNION ALL
SELECT 'delay codes documented for 99%+ of rows',
       format('{} rows use codes missing from both published code lists', COUNT(*) FILTER (WHERE c.source = 'not in published lists')),
       AVG(CASE WHEN c.source = 'not in published lists' THEN 1 ELSE 0 END) <= 0.01
FROM incidents i LEFT JOIN code_lookup c USING (code)

UNION ALL
SELECT 'every row has a cause group', format('{} missing', COUNT(*) FILTER (WHERE cause_group IS NULL)),
       COUNT(*) FILTER (WHERE cause_group IS NULL) = 0 FROM incidents

UNION ALL  -- the log's own weekday column must agree with the date
SELECT 'weekday matches date (99.9%+)', format('{} mismatches', COUNT(*) FILTER (WHERE NOT day_matches_date)),
       AVG(CASE WHEN day_matches_date THEN 1 ELSE 0 END) >= 0.999 FROM incidents

UNION ALL  -- the gap between trains should be at least the delay it caused
SELECT 'gap >= delay for 95%+ of delays',
       format('{}% of delays', ROUND(100 * AVG(CASE WHEN min_gap >= min_delay THEN 1 ELSE 0 END), 1)),
       AVG(CASE WHEN min_gap >= min_delay THEN 1 ELSE 0 END) >= 0.95 FROM incidents WHERE is_delay

UNION ALL  -- Line 3 closed on 24 July 2023. Later rows log incidents at its old stations and yard, so they
           -- must not carry any delay minutes.
SELECT 'Line 3 rows after closure delay nothing',
       format('{} rows, {} delay minutes', COUNT(*), COALESCE(SUM(min_delay), 0)),
       COALESCE(SUM(min_delay), 0) = 0 FROM incidents WHERE line = '3' AND date > DATE '2023-07-24'

UNION ALL  -- the marts must add back up to the incident table
SELECT 'yearly mart reconciles',
       format('{} vs {}', (SELECT SUM(delay_minutes) FROM mart_yearly WHERE line_name = 'All lines'),
              (SELECT SUM(min_delay) FROM incidents WHERE year <= 2025)),
       (SELECT SUM(delay_minutes) FROM mart_yearly WHERE line_name = 'All lines')
         = (SELECT SUM(min_delay) FROM incidents WHERE year <= 2025)

UNION ALL
SELECT 'line rows add up to all-lines rows',
       format('{} vs {}', (SELECT SUM(delay_minutes) FROM mart_yearly WHERE line_name <> 'All lines'),
              (SELECT SUM(delay_minutes) FROM mart_yearly WHERE line_name = 'All lines')),
       (SELECT SUM(delay_minutes) FROM mart_yearly WHERE line_name <> 'All lines')
         = (SELECT SUM(delay_minutes) FROM mart_yearly WHERE line_name = 'All lines')

UNION ALL
SELECT 'cause mart reconciles',
       format('{} vs {}', (SELECT SUM(delay_minutes) FROM mart_cause_year), (SELECT SUM(min_delay) FROM incidents WHERE year <= 2025)),
       (SELECT SUM(delay_minutes) FROM mart_cause_year) = (SELECT SUM(min_delay) FROM incidents WHERE year <= 2025)

UNION ALL
SELECT 'signal mart covers every month x line', format('{} rows', COUNT(*)),
       COUNT(*) = 3 * (SELECT COUNT(*) FROM expected_months) FROM mart_signal_monthly;
