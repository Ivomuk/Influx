CREATE OR REPLACE VIEW qa_vw1_credit_v1_service_family_mapping_coverage AS
WITH aggregated AS (
    SELECT
        service_name,
        event_family,
        COUNT(*) AS row_cnt
    FROM vw1_credit_v1_normalized_events
    GROUP BY
        service_name,
        event_family
)
SELECT
    service_name,
    event_family,
    row_cnt
FROM aggregated
ORDER BY
    service_name,
    event_family
;
