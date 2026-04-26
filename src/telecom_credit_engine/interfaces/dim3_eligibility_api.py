# Dimension 3 — Real-Time Eligibility API
# Wraps the decision engine behind a latency-budgeted request/response interface.
#
# Latency budget:
#   Feature store read       :  < 5 ms  (hot path)
#   Policy pre-filter check  :  < 20 ms (vectorised, single subscriber)
#   TNV evaluation           :  < 80 ms (6 actions × scalar arithmetic)
#   Total p99 target         :  < 200 ms
#
# Graceful degradation:
#   If features are stale (> 15 min) : serve last-known CreditLimit with is_stale=True
#   If features are missing entirely  : return CreditLimit=0, decision_status=NO_DATA
#   If decision engine errors         : return CreditLimit=0, decision_status=ENGINE_ERROR
#
# The API does not expose internal scores, states, or reason codes externally.
# It returns only the fields permitted by the external lender interface (Layer 9).

import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# API configuration
# ---------------------------------------------------------------------------
ELIGIBILITY_API_CONFIG = {
    # Latency budget in milliseconds for each stage.
    'latency_budget_feature_read_ms': 5,
    'latency_budget_prefilter_ms': 20,
    'latency_budget_tnv_ms': 80,
    'latency_budget_total_ms': 200,
    # Age in minutes beyond which features are considered stale (aligns with HOT_PATH_MAX_AGE_MINUTES=15).
    'stale_path_threshold_minutes': 15,
    # Return this CreditLimit when no data is available rather than erroring.
    'no_data_credit_limit': 0,
    # If True, log timing to stdout for observability.
    'log_timing': True,
    # Policy version to stamp on every response.
    'policy_version': 'credit_v1_python_decision_engine_prod',
}

# ---------------------------------------------------------------------------
# Internal: single-subscriber decision
# ---------------------------------------------------------------------------

def _run_single_subscriber_decision(features_dict, config_dict, action_specs_df, previous_credit_limit=None):
    """
    Runs the full Layer 2–4 decision engine for a single subscriber's features.
    Returns a dict with CreditLimit, selected_action, and internal fields.
    features_dict: output of read_features() — contains all Layer 1 + Layer 0 columns.
    """
    row = pd.Series(features_dict)

    # Build single-row DataFrames that match the engine's expected schema.
    layer1_cols = [
        'feature_dt', 'subscriber_msisdn', 'outstanding_exposure_amt', 'active_lender_cnt_30d',
        'disbursement_cnt_30d', 'days_since_last_disbursement', 'repayment_ratio_30d',
        'wallet_inflow_amt_30d', 'wallet_outflow_amt_30d', 'spend_amt_30d',
        'wallet_active_days_30d', 'stacked_borrowing_flag_30d', 'repeated_borrowing_flag_30d',
        # Dimension 2+5 additions
        'timing_manipulation_flag', 'suspicious_repayment_jump_flag', 'loan_cycling_flag',
        'repayment_ratio_trend_7d', 'wallet_inflow_trend_7d_vs_90d',
    ]
    layer0_cols = [
        'feature_dt', 'subscriber_msisdn',
        'expected_repayment_probability_v1_rule', 'expected_credit_loss_rate_v1_rule',
        'churn_cooling_probability_v1_rule', 'expected_future_transaction_margin_score_v1_rule',
        'expected_treatment_cost_score_v1_rule', 'customer_lifetime_value_contribution_v1_rule',
        'debt_stress_index_v1', 'fraud_abuse_risk_score_v1_rule',
        'behavior_consistency_score_v1_rule', 'identity_confidence_score_v1_rule',
    ]

    def _safe_row(cols):
        return pd.DataFrame([{c: row.get(c, None) for c in cols}])

    layer1_df = _safe_row(layer1_cols)
    layer0_df = _safe_row(layer0_cols)

    prev_df = None
    if previous_credit_limit is not None:
        prev_df = pd.DataFrame([{
            'subscriber_msisdn': row.get('subscriber_msisdn'),
            'CreditLimit': float(previous_credit_limit)
        }])

    # Inline import to avoid circular dependency in production.
    from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import run_credit_v1_decision_engine
    outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=config_dict,
        action_specs_input_df=action_specs_df,
        previous_capacity_df=prev_df
    )
    return outputs['vw9_credit_v1_final_capacity_output'].iloc[0].to_dict()


# ---------------------------------------------------------------------------
# Public API: single-subscriber eligibility check
# ---------------------------------------------------------------------------

def check_eligibility(msisdn, config_dict=None, previous_credit_limit=None):
    """
    Real-time eligibility check for a single subscriber.
    Returns an EligibilityResponse dict:
      MSISDN                : subscriber identifier
      CreditLimit           : approved lending capacity (0 if blocked or no data)
      validity_period_days  : how long this decision is valid
      decision_timestamp    : UTC timestamp of this decision
      policy_version        : version string for audit
      decision_status       : approved_or_maintained | blocked_or_restricted | declined | NO_DATA | ENGINE_ERROR
                              declined            — DECLINE action (no prior credit or TNV ≤ 0)
                              blocked_or_restricted — RESTRICT action (prior credit, now blocked)
      is_stale              : 1 if features were served from stale cache
      latency_ms            : end-to-end wall-clock time
    Internal fields (scores, states, reason codes) are NOT included.
    """
    api_config = ELIGIBILITY_API_CONFIG
    local_config = DECISION_ENGINE_CONFIG.copy() if config_dict is None else config_dict.copy()
    t_start = time.perf_counter()

    # ------------------------------------------------------------------
    # Step 1: Feature store read
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        from telecom_credit_engine.feature_store.dim3_feature_store import read_features
        features_dict, path_used, age_min, is_stale = read_features(
            msisdn, allow_stale=True,
            stale_threshold_minutes=api_config['stale_path_threshold_minutes']
        )
    except RuntimeError:
        # No data at all for this subscriber.
        latency_ms = round((time.perf_counter() - t_start) * 1000, 1)
        return {
            'MSISDN': msisdn,
            'CreditLimit': api_config['no_data_credit_limit'],
            'validity_period_days': local_config.get('validity_days_restrict', 7),
            'decision_timestamp': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            'policy_version': api_config['policy_version'],
            'decision_status': 'NO_DATA',
            'is_stale': 1,
            'latency_ms': latency_ms,
        }
    feature_read_ms = round((time.perf_counter() - t0) * 1000, 1)

    # ------------------------------------------------------------------
    # Step 2: Run decision engine
    # ------------------------------------------------------------------
    t1 = time.perf_counter()
    try:
        from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import ACTION_SPECS_DF
        decision = _run_single_subscriber_decision(
            features_dict, local_config, ACTION_SPECS_DF, previous_credit_limit
        )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - t_start) * 1000, 1)
        print(f'ENGINE_ERROR for MSISDN {msisdn}: {exc}')
        return {
            'MSISDN': msisdn,
            'CreditLimit': api_config['no_data_credit_limit'],
            'validity_period_days': local_config.get('validity_days_restrict', 7),
            'decision_timestamp': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            'policy_version': api_config['policy_version'],
            'decision_status': 'ENGINE_ERROR',
            'is_stale': int(is_stale),
            'latency_ms': latency_ms,
        }
    engine_ms = round((time.perf_counter() - t1) * 1000, 1)
    total_ms = round((time.perf_counter() - t_start) * 1000, 1)

    if api_config.get('log_timing', False):
        print(
            f'MSISDN={msisdn} | path={path_used} | age={age_min:.1f}min | '
            f'feature_read={feature_read_ms}ms | engine={engine_ms}ms | total={total_ms}ms'
        )

    # SLA breach warning (does not block response — graceful degradation).
    if total_ms > api_config['latency_budget_total_ms']:
        print(f'LATENCY_SLA_BREACH: MSISDN={msisdn} total={total_ms}ms > {api_config["latency_budget_total_ms"]}ms')

    # ------------------------------------------------------------------
    # Step 3: Build external-facing response (no internal fields exposed)
    # ------------------------------------------------------------------
    return {
        'MSISDN': msisdn,
        'CreditLimit': int(decision.get('CreditLimit', 0)),
        'validity_period_days': int(decision.get('validity_days', local_config.get('validity_days_restrict', 7))),
        'decision_timestamp': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'policy_version': api_config['policy_version'],
        'decision_status': str(decision.get('decision_status', 'unknown')),
        'is_stale': int(is_stale),
        'latency_ms': total_ms,
    }


# ---------------------------------------------------------------------------
# Public API: batch eligibility check
# ---------------------------------------------------------------------------

def check_eligibility_batch(msisdn_list, config_dict=None, previous_capacity_df=None):
    """
    Batch eligibility check for a list of MSISDNs.
    More efficient than calling check_eligibility() in a loop because it reads
    from the feature store in bulk and runs the decision engine on all subscribers
    in a single vectorised pass.

    previous_capacity_df: optional DataFrame [subscriber_msisdn, CreditLimit]
      for stability smoothing.

    Returns a DataFrame of EligibilityResponse rows sorted by MSISDN.
    Internal fields are not included in the output.
    """
    api_config = ELIGIBILITY_API_CONFIG
    local_config = DECISION_ENGINE_CONFIG.copy() if config_dict is None else config_dict.copy()
    t_start = time.perf_counter()

    # ------------------------------------------------------------------
    # Step 1: Bulk feature read
    # ------------------------------------------------------------------
    from telecom_credit_engine.feature_store.dim3_feature_store import read_features_batch
    features_df = read_features_batch(
        msisdn_list, allow_stale=True,
        stale_threshold_minutes=api_config['stale_path_threshold_minutes']
    )

    has_data = features_df['path_used'] != 'none'
    no_data_df = features_df[~has_data].copy()
    data_df = features_df[has_data].copy()

    responses = []

    # No-data rows
    for _, row in no_data_df.iterrows():
        responses.append({
            'MSISDN': row['subscriber_msisdn'],
            'CreditLimit': api_config['no_data_credit_limit'],
            'validity_period_days': local_config.get('validity_days_restrict', 7),
            'decision_timestamp': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            'policy_version': api_config['policy_version'],
            'decision_status': 'NO_DATA',
            'is_stale': 1,
        })

    # ------------------------------------------------------------------
    # Step 2: Run decision engine on subscribers with data
    # ------------------------------------------------------------------
    if not data_df.empty:
        try:
            from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import run_credit_v1_decision_engine, ACTION_SPECS_DF

            layer1_cols = [
                'feature_dt', 'subscriber_msisdn', 'outstanding_exposure_amt',
                'active_lender_cnt_30d', 'disbursement_cnt_30d', 'days_since_last_disbursement',
                'repayment_ratio_30d', 'wallet_inflow_amt_30d', 'wallet_outflow_amt_30d',
                'spend_amt_30d', 'wallet_active_days_30d',
                'stacked_borrowing_flag_30d', 'repeated_borrowing_flag_30d',
                'timing_manipulation_flag', 'suspicious_repayment_jump_flag', 'loan_cycling_flag',
                'repayment_ratio_trend_7d', 'wallet_inflow_trend_7d_vs_90d',
            ]
            layer0_cols = [
                'feature_dt', 'subscriber_msisdn',
                'expected_repayment_probability_v1_rule', 'expected_credit_loss_rate_v1_rule',
                'churn_cooling_probability_v1_rule', 'expected_future_transaction_margin_score_v1_rule',
                'expected_treatment_cost_score_v1_rule', 'customer_lifetime_value_contribution_v1_rule',
                'debt_stress_index_v1', 'fraud_abuse_risk_score_v1_rule',
                'behavior_consistency_score_v1_rule', 'identity_confidence_score_v1_rule',
            ]
            layer1_df = data_df[[c for c in layer1_cols if c in data_df.columns]].copy()
            layer0_df = data_df[[c for c in layer0_cols if c in data_df.columns]].copy()

            outputs = run_credit_v1_decision_engine(
                layer1_df, layer0_df,
                config_dict=local_config,
                action_specs_input_df=ACTION_SPECS_DF,
                previous_capacity_df=previous_capacity_df
            )
            decisions_df = outputs['vw9_credit_v1_final_capacity_output']

            stale_lookup = data_df.set_index('subscriber_msisdn')['is_stale'].to_dict()
            decision_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

            for _, dec_row in decisions_df.iterrows():
                msisdn = dec_row['subscriber_msisdn']
                responses.append({
                    'MSISDN': msisdn,
                    'CreditLimit': int(dec_row.get('CreditLimit', 0)),
                    'validity_period_days': int(dec_row.get('validity_days', local_config.get('validity_days_restrict', 7))),
                    'decision_timestamp': decision_ts,
                    'policy_version': api_config['policy_version'],
                    'decision_status': str(dec_row.get('decision_status', 'unknown')),
                    'is_stale': int(stale_lookup.get(msisdn, 0)),
                })

        except Exception as exc:
            print(f'BATCH_ENGINE_ERROR: {exc}')
            decision_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            for _, row in data_df.iterrows():
                responses.append({
                    'MSISDN': row['subscriber_msisdn'],
                    'CreditLimit': api_config['no_data_credit_limit'],
                    'validity_period_days': local_config.get('validity_days_restrict', 7),
                    'decision_timestamp': decision_ts,
                    'policy_version': api_config['policy_version'],
                    'decision_status': 'ENGINE_ERROR',
                    'is_stale': 1,
                })

    total_ms = round((time.perf_counter() - t_start) * 1000, 1)
    response_df = pd.DataFrame(responses).sort_values('MSISDN').reset_index(drop=True)
    response_df['batch_latency_ms'] = total_ms

    if api_config.get('log_timing', False):
        print(f'Batch eligibility: {len(msisdn_list)} MSISDNs in {total_ms}ms')

    if total_ms > api_config['latency_budget_total_ms'] * len(msisdn_list):
        print(f'LATENCY_SLA_BREACH: batch of {len(msisdn_list)} took {total_ms}ms')

    return response_df


if __name__ == '__main__':
    # Dev/notebook entry point — requires layer1_df and layer0_df in scope.
    # In production replace with a proper service entry point.
    from telecom_credit_engine.feature_store.dim3_feature_store import load_from_batch_output, get_store_health

    load_from_batch_output(layer1_df, layer0_df)
    print(get_store_health())

    msisdn_list_sample = layer1_df['subscriber_msisdn'].tolist()
    eligibility_results_df = check_eligibility_batch(msisdn_list_sample)
    print(eligibility_results_df)
