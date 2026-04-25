# Certification Pipeline
# Single entry point that runs every QA suite in dependency order and
# returns a CertificationReport with per-check pass/fail status.
#
# SQL QA suites (vw1–vw4): each check is a SQL query expected to return
# zero rows. Pass the SQL text as a dict {check_name: sql_text} sourced
# from the qa_vw*.txt files. A bq_client (BigQuery client or compatible
# adapter with .query(sql).to_dataframe()) must be supplied.
#
# Python QA suites: called directly against in-memory DataFrames using
# the existing qa_decision_engine_output and qa_api_output_contract modules.
#
# assert_certified(report) raises CertificationFailedError if any check
# failed or errored and halt_on_failure=True. Called by the orchestrator
# after run_certification to gate pipeline progression.

import pandas as pd
import numpy as np
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# SQL QA execution
# ---------------------------------------------------------------------------

def _run_sql_check(sql_text, check_name, bq_client):
    try:
        result_df = bq_client.query(sql_text).to_dataframe()
        fail_count = len(result_df)
        status = 'PASS' if fail_count == 0 else 'FAIL'
        if fail_count > 0:
            print(f'SQL QA FAIL [{check_name}]: {fail_count} rows')
        return {'check_name': check_name, 'status': status, 'fail_count': fail_count, 'error': None}
    except Exception as exc:
        print(f'SQL QA ERROR [{check_name}]: {exc}')
        return {'check_name': check_name, 'status': 'ERROR', 'fail_count': None, 'error': str(exc)}


def _run_sql_suite(suite_name, sql_checks_dict, bq_client):
    print(f'\n--- SQL QA: {suite_name} ---')
    return [_run_sql_check(sql, name, bq_client) for name, sql in sql_checks_dict.items()]


# ---------------------------------------------------------------------------
# Python QA result normalisation
# ---------------------------------------------------------------------------

def _python_qa_to_rows(qa_dict):
    rows = []
    for check_name, fail_df in qa_dict.items():
        fail_count = len(fail_df)
        rows.append({
            'check_name':  check_name,
            'status':      'PASS' if fail_count == 0 else 'FAIL',
            'fail_count':  fail_count,
            'error':       None,
        })
    return rows


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def _build_report(all_results, execution_dt, pipeline_run_id):
    results_df = pd.DataFrame(all_results)
    passed  = int((results_df['status'] == 'PASS').sum())
    failed  = int((results_df['status'] == 'FAIL').sum())
    errors  = int((results_df['status'] == 'ERROR').sum())
    total   = len(results_df)
    certified = (failed == 0 and errors == 0)

    print(f'\n{"="*60}')
    print(f'CERTIFICATION RESULT: {"CERTIFIED" if certified else "NOT CERTIFIED"}')
    print(f'  {passed}/{total} checks passed | {failed} failed | {errors} errors')
    print(f'  Run ID: {pipeline_run_id} | {execution_dt.isoformat()}')
    if not certified:
        bad = results_df[results_df['status'] != 'PASS']['check_name'].tolist()
        print(f'  Failed/Errored: {bad}')
    print(f'{"="*60}\n')

    return {
        'pipeline_run_id': pipeline_run_id,
        'execution_dt':    execution_dt.isoformat(),
        'certified':       certified,
        'total_checks':    total,
        'passed':          passed,
        'failed':          failed,
        'errors':          errors,
        'details':         results_df,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_certification(
    # SQL QA inputs — dicts of {check_name: sql_text} from the qa_vw*.txt files
    bq_client,
    sql_qa_vw1_checks,
    sql_qa_vw2_checks,
    sql_qa_vw3_checks,
    sql_qa_vw4_checks,
    # Python QA inputs — in-memory outputs from the decision engine and API
    engine_outputs,
    config_dict,
    eligibility_df,
    state_df,
    # Optional
    previous_capacity_df=None,
    pipeline_run_id=None,
):
    """
    Runs all QA suites in dependency order:
      SQL:    vw1 → vw2 → vw3 → vw4
      Python: decision engine → API output contract

    Returns a CertificationReport dict. Call assert_certified() on the result
    to halt the pipeline if any check failed.
    """
    execution_dt = datetime.now(timezone.utc)
    if pipeline_run_id is None:
        pipeline_run_id = f'cert_{execution_dt.strftime("%Y%m%d_%H%M%S")}'

    all_results = []

    # SQL QA — vw1 through vw4 in dependency order
    all_results += _run_sql_suite('vw1_normalized_events', sql_qa_vw1_checks, bq_client)
    all_results += _run_sql_suite('vw2_subscriber_day',    sql_qa_vw2_checks, bq_client)
    all_results += _run_sql_suite('vw3_layer1_features',   sql_qa_vw3_checks, bq_client)
    all_results += _run_sql_suite('vw4_layer0_scores',     sql_qa_vw4_checks, bq_client)

    # Python QA — decision engine
    print('\n--- Python QA: decision_engine ---')
    from qa_decision_engine_output import run_qa_decision_engine_output
    all_results += _python_qa_to_rows(
        run_qa_decision_engine_output(engine_outputs, config_dict, previous_capacity_df)
    )

    # Python QA — API output contract
    print('\n--- Python QA: api_output_contract ---')
    from qa_api_output_contract import run_qa_api_and_states
    all_results += _python_qa_to_rows(run_qa_api_and_states(eligibility_df, state_df))

    return _build_report(all_results, execution_dt, pipeline_run_id)


# ---------------------------------------------------------------------------
# Gate function
# ---------------------------------------------------------------------------

class CertificationFailedError(Exception):
    pass


def assert_certified(report, halt_on_failure=True):
    """
    Raises CertificationFailedError if the report is not certified and
    halt_on_failure=True. Safe to call unconditionally in automated pipelines.
    Returns True if certified, False otherwise.
    """
    if not report['certified'] and halt_on_failure:
        raise CertificationFailedError(
            f"Pipeline run {report['pipeline_run_id']} did not certify: "
            f"{report['failed']} failed, {report['errors']} errored checks."
        )
    return report['certified']
