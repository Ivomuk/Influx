# Credit V1 Batch Orchestrator
# Defines the full pipeline DAG, dependency ordering, scheduling cadence,
# inter-cycle state persistence, and portfolio feedback loop wiring.
#
# Scheduling cadence:
#   Batch pipeline        : daily at 02:00 UTC (after MoMo data lands)
#   Hot path refresh      : every 15 minutes (write_hot_features)
#   Near-realtime update  : event-driven (per lender feedback event)
#   Outcome tracking      : daily at 06:00 UTC (after batch completes)
#
# Dependency graph (batch):
#   vw1 -> vw2 -> vw3 -> vw4 -> vw5 -> vw6
#                                      |
#                              decision_engine (Layers 2-4)
#                                      |
#                              layers_5_to_8
#                                      |
#                     ┌────────────────┴────────────────┐
#                     │                                 │
#             feature_store_bootstrap          outcome_tracking
#                     │
#             eligibility_api (ready for real-time)
#
# Cross-cycle state passed forward:
#   final_capacity_df    -> next run as previous_capacity_df (stability smoothing)
#   state_df             -> next run as prior_state_df (persistence enforcement)
#   circuit_breaker_multiplier -> next run applied to policy_capacity_cap

import json
import pandas as pd
import time
from datetime import datetime, timezone

from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import run_credit_v1_decision_engine, DECISION_ENGINE_CONFIG, ACTION_SPECS_DF
from telecom_credit_engine.state_management.run_layers_5_to_8 import run_layers_5_to_8, STATE_ENGINE_CONFIG, INTERVENTION_CONFIG, PORTFOLIO_CONFIG, GOVERNANCE_CONFIG
from telecom_credit_engine.feature_store.dim3_feature_store import load_from_batch_output, write_hot_features, get_store_health
from telecom_credit_engine.monitoring.dim6_outcome_tracking import run_dim6_outcome_tracking
from telecom_credit_engine.interfaces.dim6_lender_feedback_api import run_lender_feedback_pipeline
from telecom_credit_engine.monitoring.qa_decision_engine_output import run_qa_decision_engine_output
from telecom_credit_engine.monitoring.qa_api_output_contract import run_qa_api_and_states
from telecom_credit_engine.contracts.data_contracts import validate_all_pipeline_inputs
from telecom_credit_engine.orchestration.run_certification import run_certification, assert_certified
from telecom_credit_engine.governance.operating_modes import detect_operating_mode, apply_operating_mode_overrides, build_mode_log_entry
from telecom_credit_engine.governance.decision_replay import replay_decisions, diff_decisions

# ---------------------------------------------------------------------------
# Persistent cross-cycle state.
#
# WARNING: These process-level globals are a single-process stub. They work
# correctly for sequential daily batch runs within a single Python process but
# are NOT safe under horizontal scaling or process restarts. In production,
# replace persist_cross_cycle_state() / load_cross_cycle_state() stubs with
# reads/writes to durable storage (e.g. GCS, S3, or a database) so that state
# survives restarts and is shared across workers.
# ---------------------------------------------------------------------------
_previous_capacity_df = None        # stability smoothing: CreditLimit from prior run
_prior_state_df = None              # persistence enforcement: state + state_change_dt
_circuit_breaker_multiplier = 1.0   # portfolio feedback: scaled by Layer 7
_current_operating_mode = 'NORMAL'  # incident-time mode: gates config overrides
_last_certification_report = None   # most recent certification result


# ---------------------------------------------------------------------------
# Step 1: SQL batch pipeline
# Runs the SQL views in dependency order.
# In production these are Presto/Athena queries submitted via the job scheduler.
# ---------------------------------------------------------------------------

def run_sql_batch_pipeline(execution_date):
    """
    Executes vw1 -> vw2 -> vw3 -> vw4 -> vw5 -> vw6 in order.
    Returns (layer1_df, layer0_df, vw5_df, vw6_df) as pandas DataFrames
    loaded from the query results.
    execution_date: date string 'YYYY-MM-DD' for partition filtering.
    """
    print(f'[{_now()}] SQL batch pipeline starting for {execution_date}')

    # Wire up query_engine (BigQuery client or equivalent) before calling this.
    # Each view must complete before the next starts (strict dependency order).
    raise NotImplementedError(
        'run_sql_batch_pipeline: provide a query_engine implementation. '
        'Expected pattern:\n'
        '  query_engine.run("vw1_credit_v1_normalized_events", date=execution_date)\n'
        '  query_engine.run("vw2_credit_v1_subscriber_day",    date=execution_date)\n'
        '  query_engine.run("vw3_credit_v1_layer1_features",   date=execution_date)\n'
        '  query_engine.run("vw4_credit_v1_layer0_scores",     date=execution_date)\n'
        '  query_engine.run("vw5_credit_v1_reason_codes",      date=execution_date)\n'
        '  query_engine.run("vw6_credit_v1_cap_and_action",    date=execution_date)\n'
        '  layer1_df = query_engine.read("vw3_credit_v1_layer1_features")\n'
        '  layer0_df = query_engine.read("vw4_credit_v1_layer0_scores")\n'
        '  vw5_df    = query_engine.read("vw5_credit_v1_reason_codes")\n'
        '  vw6_df    = query_engine.read("vw6_credit_v1_cap_and_action")\n'
        '  return layer1_df, layer0_df, vw5_df, vw6_df'
    )


# ---------------------------------------------------------------------------
# Step 2: QA gates — SQL outputs
# ---------------------------------------------------------------------------

def run_sql_qa_gates(layer1_df, layer0_df, vw5_df=None, vw6_df=None):
    """
    Validates schema contracts on all SQL outputs, then runs structural QA checks.
    Raises ContractViolationError or RuntimeError if any critical check fails.
    """
    from telecom_credit_engine.contracts.data_contracts import validate_all_pipeline_inputs, ContractViolationError

    print(f'[{_now()}] Running data contract validation.')
    validate_all_pipeline_inputs(layer1_df, layer0_df, vw5_df, vw6_df, strict=True)

    print(f'[{_now()}] Running SQL QA gates.')

    dupes_l1 = layer1_df.groupby(['feature_dt', 'subscriber_msisdn']).size().reset_index(name='count')
    if (dupes_l1['count'] > 1).any():
        raise RuntimeError('QA GATE FAIL: duplicate (feature_dt, subscriber_msisdn) in layer1_df')

    dupes_l0 = layer0_df.groupby(['feature_dt', 'subscriber_msisdn']).size().reset_index(name='count')
    if (dupes_l0['count'] > 1).any():
        raise RuntimeError('QA GATE FAIL: duplicate (feature_dt, subscriber_msisdn) in layer0_df')

    if 'debt_stress_index_v1' in layer0_df.columns:
        if layer0_df['debt_stress_index_v1'].lt(0).any() or layer0_df['debt_stress_index_v1'].gt(100).any():
            raise RuntimeError('QA GATE FAIL: debt_stress_index_v1 outside [0, 100]')

    print(f'[{_now()}] Data contracts + SQL QA gates passed.')


# ---------------------------------------------------------------------------
# Step 3: Decision engine (Layers 2-4)
# ---------------------------------------------------------------------------

def run_decision_engine_step(layer1_df, layer0_df, vw5_df, vw6_df,
                              config_dict, action_specs_df):
    """
    Detects the current operating mode, applies config overrides, then runs the
    Python decision engine with vw5/vw6 wired as authoritative SQL artifacts.
    """
    global _previous_capacity_df, _circuit_breaker_multiplier, _current_operating_mode

    from telecom_credit_engine.governance.operating_modes import detect_operating_mode, apply_operating_mode_overrides, build_mode_log_entry
    from telecom_credit_engine.feature_store.dim3_feature_store import get_store_health

    # Detect mode from current signals before running the engine.
    cert_passed = (_last_certification_report is None or _last_certification_report.get('certified', True))
    _current_operating_mode, mode_reasons = detect_operating_mode(
        circuit_breaker_multiplier=_circuit_breaker_multiplier,
        feature_store_health_df=get_store_health(),
        certification_passed=cert_passed,
    )
    effective_config = apply_operating_mode_overrides(
        config_dict, _current_operating_mode, _circuit_breaker_multiplier
    )

    print(f'[{_now()}] Decision engine starting. '
          f'mode={_current_operating_mode} circuit_breaker={_circuit_breaker_multiplier}')

    engine_outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=effective_config,
        action_specs_input_df=action_specs_df,
        previous_capacity_df=_previous_capacity_df,
        vw5_reason_codes_df=vw5_df,
        vw6_cap_action_df=vw6_df,
        circuit_breaker_multiplier=_circuit_breaker_multiplier,
    )

    # QA gate on decision engine outputs
    qa_results = run_qa_decision_engine_output(
        engine_outputs, config_dict, _previous_capacity_df
    )
    critical_failures = [k for k, v in qa_results.items()
                         if isinstance(v, pd.DataFrame) and not v.empty
                         and k in ('QA-DE-01_negative_credit_limit', 'QA-DE-05_duplicate_msisdn')]
    if critical_failures:
        raise RuntimeError(f'QA GATE FAIL: critical decision engine checks failed: {critical_failures}')

    print(f'[{_now()}] Decision engine complete.')
    return engine_outputs


# ---------------------------------------------------------------------------
# Step 4: Layers 5-8 — state, intervention, portfolio, governance
# ---------------------------------------------------------------------------

def run_layers_5_to_8_step(engine_outputs, state_config, intervention_config,
                            portfolio_config, governance_config):
    """
    Runs Layers 5-8 and captures:
      - state_df for next-cycle persistence enforcement
      - circuit_breaker_multiplier for next-cycle capacity cap scaling
    """
    global _prior_state_df, _circuit_breaker_multiplier

    policy_prefilter_df = engine_outputs['vw7_credit_v1_policy_prefilter']
    final_capacity_df = engine_outputs['vw9_credit_v1_final_capacity_output']

    print(f'[{_now()}] Layers 5-8 starting.')

    layer_outputs = run_layers_5_to_8(
        policy_prefilter_df, final_capacity_df,
        state_config=state_config,
        intervention_config=intervention_config,
        portfolio_config=portfolio_config,
        governance_config=governance_config,
        prior_state_df=_prior_state_df,
    )

    # Capture cross-cycle state for next run.
    _prior_state_df = layer_outputs['state_df'][
        ['subscriber_msisdn', 'operating_state', 'state_change_dt']
    ].copy()
    _circuit_breaker_multiplier = layer_outputs['circuit_breaker_capacity_multiplier']

    print(f'[{_now()}] Layers 5-8 complete. '
          f'portfolio_signal={layer_outputs["portfolio_control_signal"]} '
          f'cb_multiplier={_circuit_breaker_multiplier}')

    return layer_outputs


# ---------------------------------------------------------------------------
# Step 5: Feature store bootstrap
# ---------------------------------------------------------------------------

def run_feature_store_bootstrap(layer1_df, layer0_df):
    """Loads batch output into the cold + hot paths so the eligibility API is ready."""
    print(f'[{_now()}] Bootstrapping feature store from batch output.')
    load_from_batch_output(layer1_df, layer0_df)
    print(get_store_health().to_string(index=False))


# ---------------------------------------------------------------------------
# Step 6: Outcome tracking
# ---------------------------------------------------------------------------

def run_outcome_tracking_step(engine_outputs, layer0_df, layer1_df,
                               lender_feedback_df, evaluation_date,
                               outcome_config, fairness_config, explainability_config):
    """Runs the full Dimension 6 outcome tracking pipeline."""
    print(f'[{_now()}] Outcome tracking starting for evaluation_date={evaluation_date}.')

    final_capacity_df = engine_outputs['vw9_credit_v1_final_capacity_output']

    tracking_outputs = run_dim6_outcome_tracking(
        final_capacity_df, layer0_df, layer1_df,
        lender_feedback_df, evaluation_date,
        outcome_config=outcome_config,
        fairness_config=fairness_config,
        explainability_config=explainability_config,
    )
    print(f'[{_now()}] Outcome tracking complete.')
    return tracking_outputs


# ---------------------------------------------------------------------------
# Step 7: Persist / load cross-cycle state via Hive (Presto)
# Tables are created by deployment/hive_state_tables.sql (run once).
#
# Atomicity: pipeline_commit_log is written LAST. load_cross_cycle_state
# resolves the target run_date from the commit log, so a partial write
# (where data inserts succeed but the commit write fails) is never loaded.
#
# Idempotency: if pipeline_run_id already appears in the commit log as
# COMMITTED, persist_cross_cycle_state skips the write entirely.
# ---------------------------------------------------------------------------

VALID_OPERATING_STATES = frozenset({
    'healthy', 'at_risk', 'distressed', 'recovered', 'cooling', 'fraud_review',
})


def _validate_state_df(state_df, context='persist'):
    """Raise ValueError if state_df contains unknown operating_state values."""
    if state_df is None or state_df.empty:
        return
    unknown = set(state_df['operating_state'].unique()) - VALID_OPERATING_STATES
    if unknown:
        raise ValueError(
            f'load_cross_cycle_state [{context}]: unknown operating_state values: {unknown}. '
            f'Valid: {sorted(VALID_OPERATING_STATES)}'
        )


def persist_cross_cycle_state(run_date, pipeline_run_id, query_client):
    """Write cross-cycle state to Hive via Presto.

    Idempotent: skips write if pipeline_run_id is already COMMITTED.
    Atomic: pipeline_commit_log is written last; load never reads a
    run_date that lacks a commit marker.
    """
    global _previous_capacity_df, _prior_state_df, _circuit_breaker_multiplier, \
           _current_operating_mode, _last_certification_report

    # --- Idempotency check -------------------------------------------------
    existing = query_client.query(
        f"SELECT COUNT(*) AS cnt FROM credit_engine.pipeline_commit_log "
        f"WHERE run_date = '{run_date}' AND pipeline_run_id = '{pipeline_run_id}' "
        f"AND write_status = 'COMMITTED'"
    ).to_dataframe()
    if not existing.empty and int(existing.iloc[0]['cnt']) > 0:
        print(f'[{_now()}] State already committed for run_id={pipeline_run_id}; skipping.')
        return

    # --- Validate states before writing ------------------------------------
    _validate_state_df(_prior_state_df, context='persist')

    # --- Capacity state ----------------------------------------------------
    if _previous_capacity_df is not None and not _previous_capacity_df.empty:
        cap_rows = [
            (row.subscriber_msisdn, row.CreditLimit, pipeline_run_id, run_date)
            for row in _previous_capacity_df.itertuples(index=False)
        ]
        query_client.insert_rows(
            'credit_engine.pipeline_capacity_state',
            ['subscriber_msisdn', 'credit_limit', 'pipeline_run_id', 'run_date'],
            cap_rows,
            overwrite=True,
        )
        # Row-count reconciliation: read back and verify
        reconcile = query_client.query(
            f"SELECT COUNT(*) AS cnt FROM credit_engine.pipeline_capacity_state "
            f"WHERE run_date = '{run_date}' AND pipeline_run_id = '{pipeline_run_id}'"
        ).to_dataframe()
        written = int(reconcile.iloc[0]['cnt'])
        expected = len(cap_rows)
        if written != expected:
            raise RuntimeError(
                f'Capacity reconciliation failed: expected {expected} rows, '
                f'found {written} in Hive for run_id={pipeline_run_id}'
            )

    # --- Subscriber state --------------------------------------------------
    if _prior_state_df is not None and not _prior_state_df.empty:
        state_rows = [
            (row.subscriber_msisdn, row.operating_state,
             pd.Timestamp(row.state_change_dt), pipeline_run_id, run_date)
            for row in _prior_state_df.itertuples(index=False)
        ]
        query_client.insert_rows(
            'credit_engine.pipeline_subscriber_state',
            ['subscriber_msisdn', 'operating_state', 'state_change_dt',
             'pipeline_run_id', 'run_date'],
            state_rows,
            overwrite=True,
        )
        reconcile = query_client.query(
            f"SELECT COUNT(*) AS cnt FROM credit_engine.pipeline_subscriber_state "
            f"WHERE run_date = '{run_date}' AND pipeline_run_id = '{pipeline_run_id}'"
        ).to_dataframe()
        written = int(reconcile.iloc[0]['cnt'])
        expected = len(state_rows)
        if written != expected:
            raise RuntimeError(
                f'Subscriber state reconciliation failed: expected {expected} rows, '
                f'found {written} in Hive for run_id={pipeline_run_id}'
            )

    # --- Run metadata ------------------------------------------------------
    cert_passed = bool(_last_certification_report.get('certified', False)) \
        if _last_certification_report else False
    cert_summary = json.dumps({
        k: v for k, v in _last_certification_report.items() if k != 'details'
    }) if _last_certification_report else '{}'
    query_client.insert_rows(
        'credit_engine.pipeline_run_metadata',
        ['circuit_breaker_multiplier', 'operating_mode', 'certification_passed',
         'certification_summary', 'pipeline_run_id', 'run_date'],
        [(_circuit_breaker_multiplier, _current_operating_mode,
          cert_passed, cert_summary, pipeline_run_id, run_date)],
        overwrite=True,
    )
    reconcile = query_client.query(
        f"SELECT COUNT(*) AS cnt FROM credit_engine.pipeline_run_metadata "
        f"WHERE run_date = '{run_date}' AND pipeline_run_id = '{pipeline_run_id}'"
    ).to_dataframe()
    if int(reconcile.iloc[0]['cnt']) != 1:
        raise RuntimeError(
            f'Run metadata reconciliation failed: expected 1 row, '
            f'found {int(reconcile.iloc[0]["cnt"])} in Hive for run_id={pipeline_run_id}'
        )

    # --- Commit marker (written LAST) -------------------------------------
    query_client.insert_rows(
        'credit_engine.pipeline_commit_log',
        ['pipeline_run_id', 'write_status', 'committed_at', 'run_date'],
        [(pipeline_run_id, 'COMMITTED',
          datetime.now(timezone.utc).isoformat(), run_date)],
    )
    print(f'[{_now()}] Cross-cycle state committed: run_id={pipeline_run_id}, date={run_date}.')


def load_cross_cycle_state(query_client):
    """Restore cross-cycle state from the most recently COMMITTED run in Hive.
    Call once at process startup before the first run_batch_pipeline invocation."""
    global _previous_capacity_df, _prior_state_df, _circuit_breaker_multiplier, \
           _current_operating_mode

    # Resolve target run_date from the commit log — never from data tables directly
    commit_df = query_client.query(
        "SELECT MAX(run_date) AS last_committed_date "
        "FROM credit_engine.pipeline_commit_log "
        "WHERE write_status = 'COMMITTED'"
    ).to_dataframe()

    if commit_df.empty or commit_df.iloc[0]['last_committed_date'] is None:
        print(f'[{_now()}] No committed state found in Hive; starting with defaults.')
        return

    last_date = str(commit_df.iloc[0]['last_committed_date'])

    cap_df = query_client.query(
        f"SELECT subscriber_msisdn, credit_limit "
        f"FROM credit_engine.pipeline_capacity_state "
        f"WHERE run_date = '{last_date}'"
    ).to_dataframe()

    state_df = query_client.query(
        f"SELECT subscriber_msisdn, operating_state, state_change_dt "
        f"FROM credit_engine.pipeline_subscriber_state "
        f"WHERE run_date = '{last_date}'"
    ).to_dataframe()

    meta_df = query_client.query(
        f"SELECT circuit_breaker_multiplier, operating_mode "
        f"FROM credit_engine.pipeline_run_metadata "
        f"WHERE run_date = '{last_date}'"
    ).to_dataframe()

    if not cap_df.empty:
        _previous_capacity_df = cap_df.rename(columns={'credit_limit': 'CreditLimit'})

    if not state_df.empty:
        _prior_state_df = state_df.copy()
        _prior_state_df['state_change_dt'] = pd.to_datetime(_prior_state_df['state_change_dt'])
        _validate_state_df(_prior_state_df, context='load')

    if not meta_df.empty:
        _circuit_breaker_multiplier = float(meta_df.iloc[0]['circuit_breaker_multiplier'])
        _current_operating_mode     = str(meta_df.iloc[0]['operating_mode'])

    print(f'[{_now()}] Cross-cycle state loaded from Hive (run_date={last_date}).')


# ---------------------------------------------------------------------------
# Main orchestrator entry point (batch run)
# ---------------------------------------------------------------------------

def run_batch_pipeline(
    execution_date,
    lender_feedback_df,
    # Certification inputs (SQL QA check dicts sourced from qa_vw*.txt files)
    query_client=None,
    sql_qa_vw1_checks=None,
    sql_qa_vw2_checks=None,
    sql_qa_vw3_checks=None,
    sql_qa_vw4_checks=None,
    eligibility_df=None,
    # Decision engine
    config_dict=None,
    action_specs_df=None,
    # Layers 5–8
    state_config=None,
    intervention_config=None,
    portfolio_config=None,
    governance_config=None,
    # Outcome tracking
    outcome_config=None,
    fairness_config=None,
    explainability_config=None,
):
    """
    Runs the full daily batch pipeline in dependency order.
    Cross-cycle state (_previous_capacity_df, _prior_state_df,
    _circuit_breaker_multiplier, _current_operating_mode) is updated
    automatically between cycles.

    Returns a dict of all outputs for downstream inspection or storage.
    """
    global _previous_capacity_df

    t_start = time.perf_counter()
    print(f'\n{"="*60}')
    print(f'[{_now()}] Batch pipeline starting: {execution_date}')
    print(f'{"="*60}')

    # Step 1: SQL views
    layer1_df, layer0_df, vw5_df, vw6_df = run_sql_batch_pipeline(execution_date)

    # Step 2: Data contracts + QA gates
    run_sql_qa_gates(layer1_df, layer0_df, vw5_df, vw6_df)

    # Step 3: Decision engine
    engine_outputs = run_decision_engine_step(
        layer1_df, layer0_df, vw5_df, vw6_df,
        config_dict or DECISION_ENGINE_CONFIG,
        action_specs_df or ACTION_SPECS_DF,
    )

    # Step 4: Layers 5-8
    layer_outputs = run_layers_5_to_8_step(
        engine_outputs,
        state_config or STATE_ENGINE_CONFIG,
        intervention_config or INTERVENTION_CONFIG,
        portfolio_config or PORTFOLIO_CONFIG,
        governance_config or GOVERNANCE_CONFIG,
    )

    # Step 5: Feature store bootstrap
    run_feature_store_bootstrap(layer1_df, layer0_df)

    # Step 6: Outcome tracking
    tracking_outputs = run_outcome_tracking_step(
        engine_outputs, layer0_df, layer1_df,
        lender_feedback_df, execution_date,
        outcome_config, fairness_config, explainability_config,
    )

    # Advance previous_capacity_df for next cycle's stability smoothing.
    _previous_capacity_df = engine_outputs[
        'vw9_credit_v1_final_capacity_output'
    ][['subscriber_msisdn', 'CreditLimit']].copy()

    # Step 7: Certification — runs all QA suites and gates pipeline completion
    global _last_certification_report
    _last_certification_report = run_certification(
        query_client=query_client,
        sql_qa_vw1_checks=sql_qa_vw1_checks,
        sql_qa_vw2_checks=sql_qa_vw2_checks,
        sql_qa_vw3_checks=sql_qa_vw3_checks,
        sql_qa_vw4_checks=sql_qa_vw4_checks,
        engine_outputs=engine_outputs,
        config_dict=config_dict or DECISION_ENGINE_CONFIG,
        eligibility_df=eligibility_df,
        state_df=layer_outputs['state_df'],
        previous_capacity_df=_previous_capacity_df,
        pipeline_run_id=f'batch_{execution_date}',
    )
    assert_certified(_last_certification_report, halt_on_failure=True)

    # Step 8: Persist cross-cycle state
    persist_cross_cycle_state(execution_date, f'batch_{execution_date}', query_client)

    elapsed = round((time.perf_counter() - t_start) / 60, 1)
    print(f'\n[{_now()}] Batch pipeline complete in {elapsed} min.')

    return {
        'engine_outputs':        engine_outputs,
        'layer_outputs':         layer_outputs,
        'tracking_outputs':      tracking_outputs,
        'certification_report':  _last_certification_report,
        'operating_mode':        _current_operating_mode,
    }


# ---------------------------------------------------------------------------
# Near-realtime event handler (called per lender feedback event)
# ---------------------------------------------------------------------------

def handle_lender_feedback_event(raw_report_df, observed_events_df,
                                  current_exposure_df, lender_feedback_config):
    """
    Called immediately when a lender submits a disbursement or repayment event.
    Updates the warm feature path so the eligibility API reflects the new exposure
    without waiting for the next batch run.
    """
    from telecom_credit_engine.interfaces.dim6_lender_feedback_api import run_lender_feedback_pipeline
    from telecom_credit_engine.feature_store.dim3_feature_store import write_warm_features

    feedback_outputs = run_lender_feedback_pipeline(
        raw_report_df, observed_events_df, current_exposure_df,
        final_capacity_df=None,   # compliance check skipped in realtime path
        config=lender_feedback_config,
    )

    updated_exposure_df = feedback_outputs['updated_exposure_df']
    for _, row in updated_exposure_df.iterrows():
        write_warm_features(
            msisdn=row['msisdn'],
            updated_exposure_amt=row['outstanding_exposure_amt'],
            lender_event_type=row['update_source'],
        )

    return feedback_outputs


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
