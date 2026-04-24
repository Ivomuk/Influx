CREATE OR REPLACE VIEW qa_vw1_credit_v1_duplicate_fid AS
WITH fid_counts AS (
    SELECT
        fid,
        COUNT(*) AS row_cnt,
        MIN(event_dt) AS min_event_dt,
        MAX(event_dt) AS max_event_dt,
        MIN(service_name) AS sample_service_name,
        MIN(sub_service_name) AS sample_sub_service_name
    FROM vw1_credit_v1_normalized_events
    GROUP BY fid
)
SELECT
    fid,
    row_cnt,
    min_event_dt,
    max_event_dt,
    sample_service_name,
    sample_sub_service_name
FROM fid_counts
WHERE row_cnt > 1
;
