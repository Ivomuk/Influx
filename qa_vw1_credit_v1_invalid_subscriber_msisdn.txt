CREATE OR REPLACE VIEW qa_vw1_credit_v1_invalid_subscriber_msisdn AS
WITH base AS (
    SELECT
        event_dt,
        event_family,
        service_name,
        sub_service_name,
        subscriber_msisdn,
        CAST(subscriber_msisdn AS varchar) AS subscriber_msisdn_str
    FROM vw1_credit_v1_normalized_events
),
aggregated AS (
    SELECT
        event_dt,
        event_family,
        service_name,
        sub_service_name,
        COUNT(*) AS row_cnt,
        COUNT_IF(subscriber_msisdn IS NULL) AS null_subscriber_msisdn_cnt,
        COUNT_IF(trim(subscriber_msisdn_str) = '') AS blank_subscriber_msisdn_cnt
    FROM base
    GROUP BY
        event_dt,
        event_family,
        service_name,
        sub_service_name
)
SELECT
    event_dt,
    event_family,
    service_name,
    sub_service_name,
    row_cnt,
    null_subscriber_msisdn_cnt,
    blank_subscriber_msisdn_cnt
FROM aggregated
;
