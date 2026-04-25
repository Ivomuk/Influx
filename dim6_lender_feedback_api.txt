# Dimension 6 — Lender Feedback API
# Handles ingestion, validation, reconciliation, near-realtime exposure updates,
# and lender non-compliance detection.
# All functions are pure DataFrame-in / DataFrame-out for testability.

import pandas as pd
import numpy as np
from datetime import datetime, timezone


def validate_lender_report(report_df, config):
    """
    Validates an incoming lender report DataFrame against schema and business rules.
    Returns the input rows with two appended columns:
      is_valid_flag              : 1 if row passes all checks, else 0
      validation_error_summary   : semicolon-separated list of error codes, empty if valid
    Raises ValueError if any required column is entirely absent.
    """
    required_fields = config['required_report_fields']
    valid_event_types = config['valid_event_types']
    valid_statuses = config['valid_loan_statuses']
    max_amount = config['max_single_disbursement_amount']

    missing_cols = [col for col in required_fields if col not in report_df.columns]
    if missing_cols:
        raise ValueError('Lender report missing required columns: ' + ', '.join(missing_cols))

    val_df = report_df.copy()
    errors = pd.Series([''] * len(val_df), index=val_df.index, dtype=str)

    # Null or blank MSISDN
    null_msisdn = val_df['msisdn'].isnull() | (val_df['msisdn'].astype(str).str.strip() == '')
    errors += np.where(null_msisdn, 'NULL_MSISDN;', '')

    # Null or blank loan_id
    null_loan_id = val_df['loan_id'].isnull() | (val_df['loan_id'].astype(str).str.strip() == '')
    errors += np.where(null_loan_id, 'NULL_LOAN_ID;', '')

    # Invalid event_type
    invalid_event = ~val_df['event_type'].isin(valid_event_types)
    errors += np.where(invalid_event, 'INVALID_EVENT_TYPE;', '')

    # Invalid loan_status when column is present
    if 'loan_status' in val_df.columns:
        invalid_status = val_df['loan_status'].notna() & ~val_df['loan_status'].isin(valid_statuses)
        errors += np.where(invalid_status, 'INVALID_LOAN_STATUS;', '')

    # Amount parsing and range checks
    val_df['event_amount_num'] = pd.to_numeric(val_df['event_amount'], errors='coerce')
    non_positive = val_df['event_amount_num'].isnull() | (val_df['event_amount_num'] <= 0)
    errors += np.where(non_positive, 'NON_POSITIVE_AMOUNT;', '')
    exceeds_max = val_df['event_amount_num'].fillna(0) > max_amount
    errors += np.where(exceeds_max, 'AMOUNT_EXCEEDS_MAX;', '')

    # Date parsing
    val_df['event_date_parsed'] = pd.to_datetime(val_df['event_date'], errors='coerce')
    bad_date = val_df['event_date_parsed'].isnull()
    errors += np.where(bad_date, 'UNPARSEABLE_DATE;', '')

    val_df['validation_error_summary'] = errors.str.rstrip(';')
    val_df['is_valid_flag'] = (val_df['validation_error_summary'] == '').astype(int)

    output_cols = required_fields + ['event_amount_num', 'event_date_parsed', 'is_valid_flag', 'validation_error_summary']
    optional_cols = [c for c in config.get('optional_report_fields', []) if c in val_df.columns]
    return val_df[output_cols + optional_cols].copy()


def ingest_lender_report(validated_report_df, config):
    """
    Normalises a validated lender report into a standard internal event format.
    Only rows where is_valid_flag == 1 are processed.
    Returns a clean events DataFrame with typed columns and signed_amount.
    """
    valid_df = validated_report_df[validated_report_df['is_valid_flag'] == 1].copy()

    if valid_df.empty:
        return pd.DataFrame(columns=[
            'msisdn', 'loan_id', 'lender_id', 'event_type', 'event_date',
            'event_amount', 'signed_amount',
            'is_disbursement', 'is_repayment', 'is_default', 'is_status_change',
            'ingested_at', 'report_version'
        ])

    valid_df['is_disbursement'] = (valid_df['event_type'] == 'DISBURSEMENT').astype(int)
    valid_df['is_repayment'] = (valid_df['event_type'] == 'REPAYMENT').astype(int)
    valid_df['is_default'] = (valid_df['event_type'] == 'DEFAULT').astype(int)
    valid_df['is_status_change'] = (valid_df['event_type'] == 'STATUS_CHANGE').astype(int)

    # Disbursements increase exposure (positive); repayments reduce it (negative).
    valid_df['signed_amount'] = np.select(
        [valid_df['event_type'] == 'DISBURSEMENT', valid_df['event_type'] == 'REPAYMENT'],
        [valid_df['event_amount_num'], -1.0 * valid_df['event_amount_num']],
        default=0.0
    )

    valid_df['ingested_at'] = datetime.now(timezone.utc).replace(microsecond=0)
    valid_df['report_version'] = config['output_version']

    return valid_df.rename(columns={
        'event_date_parsed': 'event_date',
        'event_amount_num': 'event_amount'
    })[[
        'msisdn', 'loan_id', 'lender_id', 'event_type', 'event_date',
        'event_amount', 'signed_amount',
        'is_disbursement', 'is_repayment', 'is_default', 'is_status_change',
        'ingested_at', 'report_version'
    ]].copy()


def reconcile_lender_vs_observed(ingested_report_df, observed_events_df, config):
    """
    Matches lender-reported disbursements to observed transaction events from vw1.
    Returns:
      detail_df   — one row per lender-reported disbursement with reconciliation_status:
                    MATCHED          — amount matches within tolerance on same MSISDN + date
                    LENDER_ONLY      — reported by lender, not seen in observed events
                    OBSERVED_ONLY    — seen in observed events, not reported by lender
                    UNMATCHED_AMOUNT — MSISDN + date match but amount outside tolerance
      summary_df  — count per reconciliation_status with run timestamp
    """
    tolerance = config['reconciliation_amount_tolerance_pct']

    lender_disb = ingested_report_df[ingested_report_df['is_disbursement'] == 1].copy()
    lender_disb['event_date_d'] = pd.to_datetime(lender_disb['event_date']).dt.date

    obs_disb = observed_events_df[observed_events_df['event_family'] == 'loan_disbursement'][[
        'subscriber_msisdn', 'event_dt', 'disbursement_amt'
    ]].copy()
    obs_disb['event_date_d'] = pd.to_datetime(obs_disb['event_dt']).dt.date

    merged = lender_disb.merge(
        obs_disb,
        left_on=['msisdn', 'event_date_d'],
        right_on=['subscriber_msisdn', 'event_date_d'],
        how='outer',
        indicator=True
    )

    both_mask = merged['_merge'] == 'both'
    amount_match = (
        np.abs(merged['event_amount'] - merged['disbursement_amt'])
        / merged['event_amount'].clip(lower=1.0)
    ) <= tolerance

    merged['reconciliation_status'] = np.select(
        [both_mask & amount_match, merged['_merge'] == 'left_only',
         merged['_merge'] == 'right_only', both_mask & ~amount_match],
        ['MATCHED', 'LENDER_ONLY', 'OBSERVED_ONLY', 'UNMATCHED_AMOUNT'],
        default='UNKNOWN'
    )

    detail_df = merged[[
        'msisdn', 'lender_id', 'event_date_d', 'event_amount',
        'disbursement_amt', 'reconciliation_status'
    ]].copy()

    summary_df = (
        detail_df.groupby('reconciliation_status')
        .size()
        .reset_index(name='row_count')
    )
    summary_df['run_at'] = datetime.now(timezone.utc).replace(microsecond=0)
    total = max(summary_df['row_count'].sum(), 1)
    summary_df['pct_of_total'] = (summary_df['row_count'] / total * 100).round(2)

    return detail_df, summary_df


def compute_near_realtime_exposure_update(ingested_report_df, current_exposure_df, config):
    """
    Immediately updates outstanding_exposure_amt when a lender reports a new event.
    Prevents over-lending between a disbursement and the next batch run.

    current_exposure_df must contain: msisdn, outstanding_exposure_amt, last_updated_at
    Returns updated exposure DataFrame with staleness flag.
    """
    delta_df = (
        ingested_report_df
        .groupby('msisdn', as_index=False)['signed_amount']
        .sum()
        .rename(columns={'signed_amount': 'exposure_delta'})
    )

    updated_df = current_exposure_df.merge(delta_df, on='msisdn', how='left')
    updated_df['exposure_delta'] = updated_df['exposure_delta'].fillna(0.0)
    updated_df['outstanding_exposure_amt'] = (
        updated_df['outstanding_exposure_amt'] + updated_df['exposure_delta']
    ).clip(lower=0.0)

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    updated_df['last_updated_at'] = now_utc
    updated_df['update_source'] = 'lender_feedback_realtime'
    updated_df['is_stale'] = 0

    return updated_df[[
        'msisdn', 'outstanding_exposure_amt', 'last_updated_at', 'update_source', 'is_stale'
    ]].copy()


def detect_lender_noncompliance(ingested_report_df, final_capacity_df, config):
    """
    Flags lenders who either:
      LENT_TO_RESTRICTED_SUBSCRIBER — disbursed to a subscriber in DECLINE or RESTRICT state
      EXCEEDED_CREDIT_LIMIT         — disbursed an amount above the approved CreditLimit

    Returns a DataFrame of noncompliant disbursement events (empty if all compliant).
    """
    breach_tolerance = config['noncompliance_credit_limit_breach_tolerance']

    disb_df = ingested_report_df[ingested_report_df['is_disbursement'] == 1].copy()
    if disb_df.empty:
        return pd.DataFrame()

    capacity_df = final_capacity_df[[
        'subscriber_msisdn', 'CreditLimit', 'selected_action', 'feature_dt'
    ]].rename(columns={'subscriber_msisdn': 'msisdn'}).copy()

    merged = disb_df.merge(capacity_df, on='msisdn', how='left')

    merged['lent_to_restricted_flag'] = (
        merged['selected_action'].isin(['DECLINE', 'RESTRICT'])
    ).fillna(False).astype(int)

    merged['exceeded_credit_limit_flag'] = (
        merged['event_amount'] > merged['CreditLimit'].fillna(0) + breach_tolerance
    ).astype(int)

    merged['noncompliance_reason'] = np.select(
        [merged['lent_to_restricted_flag'] == 1, merged['exceeded_credit_limit_flag'] == 1],
        ['LENT_TO_RESTRICTED_SUBSCRIBER', 'EXCEEDED_CREDIT_LIMIT'],
        default='COMPLIANT'
    )

    noncompliant_df = merged[merged['noncompliance_reason'] != 'COMPLIANT'][[
        'msisdn', 'lender_id', 'loan_id', 'event_date', 'event_amount',
        'CreditLimit', 'selected_action', 'noncompliance_reason'
    ]].copy()

    return noncompliant_df


def run_lender_feedback_pipeline(raw_report_df, observed_events_df, current_exposure_df,
                                  final_capacity_df, config=None):
    """
    Orchestrates the full lender feedback pipeline:
      1. Validate raw report
      2. Ingest valid rows
      3. Reconcile against observed events
      4. Update exposure in near-realtime
      5. Detect lender non-compliance
    Returns a dict of all outputs.
    """
    local_config = LENDER_FEEDBACK_CONFIG.copy() if config is None else config.copy()

    validated_df = validate_lender_report(raw_report_df, local_config)
    ingested_df = ingest_lender_report(validated_df, local_config)
    reconciliation_detail_df, reconciliation_summary_df = reconcile_lender_vs_observed(
        ingested_df, observed_events_df, local_config
    )
    updated_exposure_df = compute_near_realtime_exposure_update(
        ingested_df, current_exposure_df, local_config
    )
    noncompliance_df = detect_lender_noncompliance(ingested_df, final_capacity_df, local_config)

    invalid_count = int((validated_df['is_valid_flag'] == 0).sum())
    if invalid_count > 0:
        print(f'WARNING: {invalid_count} lender report rows failed validation and were not ingested.')
        print(validated_df[validated_df['is_valid_flag'] == 0][['msisdn', 'loan_id', 'validation_error_summary']])

    if not noncompliance_df.empty:
        print(f'ALERT: {len(noncompliance_df)} lender non-compliance events detected.')
        print(noncompliance_df[['msisdn', 'lender_id', 'noncompliance_reason']])

    return {
        'validated_df': validated_df,
        'ingested_df': ingested_df,
        'reconciliation_detail_df': reconciliation_detail_df,
        'reconciliation_summary_df': reconciliation_summary_df,
        'updated_exposure_df': updated_exposure_df,
        'noncompliance_df': noncompliance_df
    }
