-- Load the cleaned incident log and its lookup tables, and add the fields every model uses.

CREATE OR REPLACE TABLE incidents AS
SELECT
    i.*,
    year(i.date)                                    AS year,
    CAST(date_trunc('month', i.date) AS DATE)       AS month,
    i.min_delay > 0                                 AS is_delay,       -- 65% of logged incidents delay no train
    i.min_delay >= 30                               AS is_long,        -- a "disruption": 30+ minutes
    CASE WHEN i.line IN ('1', '2', '3', '4') THEN 'Line ' || i.line
         WHEN i.line = 'multiple' THEN 'Several lines'
         ELSE 'Unknown' END                         AS line_name,
    CASE WHEN i.date <  DATE '2017-01-01' THEN 'before'                -- before Line 1 re-signalling began
         WHEN i.date <  DATE '2022-10-01' THEN 'transition'            -- ATC rolled out section by section
         ELSE 'after' END                           AS atc_period      -- ATC on all of Line 1 from 24 Sep 2022
FROM read_csv('data/processed/incidents.csv.gz', header = true, auto_detect = true,
              types = {'date': 'DATE', 'line': 'VARCHAR', 'code': 'VARCHAR', 'vehicle': 'INTEGER'}) AS i;

CREATE OR REPLACE TABLE code_lookup AS
SELECT * FROM read_csv('data/processed/code_lookup.csv', header = true, auto_detect = true);

CREATE OR REPLACE TABLE station_mapping AS
SELECT * FROM read_csv('data/processed/station_mapping.csv', header = true, auto_detect = true);

CREATE OR REPLACE TABLE cleaning_log AS
SELECT * FROM read_csv('data/processed/cleaning_log.csv', header = true, auto_detect = true);
