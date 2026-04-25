# Configuration for Dimension 6: Outcome Tracking, Lender Feedback API, Fairness Monitoring, and Explainability.
# All thresholds and schema definitions are externalised here so no function needs editing to change policy.

import pandas as pd

LENDER_FEEDBACK_CONFIG = {
    # Required fields every lender report row must contain.
    'required_report_fields': [
        'msisdn', 'loan_id', 'lender_id', 'event_type', 'event_amount', 'event_date'
    ],
    # Optional fields ingested when present.
    'optional_report_fields': [
        'days_past_due', 'loan_status', 'loan_tenor_days', 'interest_amount'
    ],
    # Accepted event types.
    'valid_event_types': ['DISBURSEMENT', 'REPAYMENT', 'DEFAULT', 'STATUS_CHANGE'],
    # Accepted loan statuses when loan_status field is present.
    'valid_loan_statuses': ['ACTIVE', 'REPAID', 'DEFAULTED', 'RESTRUCTURED', 'WRITTEN_OFF'],
    # Amount tolerance when matching lender-reported vs observed transaction events.
    'reconciliation_amount_tolerance_pct': 0.05,
    # Feature store exposure is considered stale if older than this many minutes.
    'stale_exposure_threshold_minutes': 30,
    # Single disbursement amount hard ceiling for validation.
    'max_single_disbursement_amount': 500000.0,
    # Tolerance when checking CreditLimit breach (0 = zero tolerance).
    'noncompliance_credit_limit_breach_tolerance': 0.0,
    'output_version': 'credit_v1_lender_feedback_v1'
}

OUTCOME_TRACKING_CONFIG = {
    # Number of days after the decision date to collect repayment outcomes.
    'outcome_lookback_days': 30,
    # Number of equal-frequency buckets for calibration analysis.
    'calibration_bucket_count': 10,
    # Minimum subscribers per calibration bucket before the bucket is reported.
    'calibration_min_bucket_size': 10,
    # Absolute difference between predicted and actual rate flagged as miscalibrated.
    'calibration_miscalibration_threshold': 0.10,
    # DSI band boundaries and their display labels.
    'dsi_bands': [0, 20, 40, 60, 80, 101],
    'dsi_band_labels': ['0-19', '20-39', '40-59', '60-79', '80-100'],
    # Minimum subscribers per DSI band before reporting default rate.
    'dsi_min_band_size': 5,
    # How many days of snapshots to retain for outcome joins.
    'snapshot_retention_days': 365,
    'output_version': 'credit_v1_outcome_tracking_v1'
}

FAIRNESS_CONFIG = {
    # A segment's restrict rate must not exceed this multiple of the system-wide rate.
    'disparate_impact_restrict_rate_multiplier': 3.0,
    # Segments smaller than this are excluded from fairness reporting.
    'min_segment_size': 20,
    # Warning threshold: top segment's share of total credit limit.
    'credit_limit_top_segment_warning_pct': 0.30,
    # Segment columns to analyse when they are present in the decision DataFrame.
    # Populated at runtime from available columns; set to [] to disable.
    'monitored_segment_cols': ['sim_age_cohort', 'kyc_tier', 'region']
}

EXPLAINABILITY_CONFIG = {
    # Maximum character length for the SMS-friendly explanation string.
    'sms_max_chars': 160,
    # Human-readable descriptions keyed by primary_reason_code.
    'reason_descriptions': {
        'LOW_IDENTITY_CONFIDENCE':          'identity verification could not be confirmed',
        'HIGH_FRAUD_ABUSE_RISK':            'unusual borrowing patterns were detected',
        'SEVERE_DSI':                       'debt stress level is very high',
        'RECENT_DISBURSEMENT_NO_REPAYMENT': 'a recent loan has not been repaid yet',
        'STACKED_BORROWING':                'multiple active loans were detected',
        'LOW_REPAYMENT_RATIO':              'repayment history is below the required level',
        'HIGH_ACTIVE_LENDER_COUNT':         'too many active lenders at the same time',
        'HIGH_EXPOSURE_TO_INFLOW':          'outstanding debt is too high relative to income',
        'THIN_FILE_CONSERVATIVE_PATH':      'insufficient activity history — a starter limit applies',
        'TIMING_MANIPULATION_DETECTED':     'an unusual inflow spike was detected before evaluation',
        'LOAN_CYCLING_DETECTED':            'a rapid borrow-repay-borrow pattern was detected',
        'PASS':                             'your profile meets lending criteria'
    },
    # Counterfactual guidance keyed by primary_reason_code.
    'counterfactual_texts': {
        'SEVERE_DSI':                       'Limit improves if debt stress falls below 40 and repayment ratio reaches 0.75.',
        'LOW_REPAYMENT_RATIO':              'Limit improves if repayment ratio reaches 0.75 over the next 30 days.',
        'HIGH_EXPOSURE_TO_INFLOW':          'Limit improves once outstanding balance falls below 80% of monthly inflow.',
        'STACKED_BORROWING':                'Limit improves after reducing to one active lender.',
        'RECENT_DISBURSEMENT_NO_REPAYMENT': 'Limit will be re-evaluated after the current loan is repaid.',
        'THIN_FILE_CONSERVATIVE_PATH':      'Limit grows automatically as repayment history is established.',
        'HIGH_FRAUD_ABUSE_RISK':            'Account is under review. Contact support if this is unexpected.',
        'LOW_IDENTITY_CONFIDENCE':          'Completing identity verification will unlock full eligibility.',
        'HIGH_ACTIVE_LENDER_COUNT':         'Limit improves after reducing active loans to fewer than 3.',
        'TIMING_MANIPULATION_DETECTED':     'Limit is re-evaluated after a stable inflow pattern is observed.',
        'LOAN_CYCLING_DETECTED':            'Limit is re-evaluated after a normal repayment gap is observed.',
        'PASS':                             'Maintain regular activity and repayments to grow your limit.'
    }
}

print('dim6_config loaded.')
print('LENDER_FEEDBACK_CONFIG:', pd.Series(LENDER_FEEDBACK_CONFIG))
print('OUTCOME_TRACKING_CONFIG:', pd.Series(OUTCOME_TRACKING_CONFIG))
print('FAIRNESS_CONFIG:', pd.Series(FAIRNESS_CONFIG))
