"""
Integration test: PD model scoring step wired into the orchestrator, verified
against the real committed models/pd_v1/ artifacts and the shared conftest
layer1_df/layer0_df fixtures. Confirms the new step merges cleanly onto
layer0_df and that the result still passes the existing QA gate (i.e. the
extended layer0_scores contract and the new step are compatible end to end).
"""
from telecom_credit_engine.orchestration.orchestrator import (
    run_pd_model_scoring_step,
    run_sql_qa_gates,
)


def test_pd_model_scoring_step_merges_onto_layer0_and_passes_qa_gates(layer1_df, layer0_df):
    merged_layer0_df = run_pd_model_scoring_step(layer1_df, layer0_df)

    assert 'pd_score_v1_model' in merged_layer0_df.columns
    assert 'pd_model_version' in merged_layer0_df.columns
    assert merged_layer0_df['pd_score_v1_model'].between(0, 1).all()
    assert len(merged_layer0_df) == len(layer0_df)

    # Must not raise ContractViolationError — proves the extended contract
    # and the new merge step are compatible with the existing QA gate.
    run_sql_qa_gates(layer1_df, merged_layer0_df)


def test_pd_model_scoring_step_does_not_mutate_original_layer0_df(layer1_df, layer0_df):
    original_columns = set(layer0_df.columns)
    run_pd_model_scoring_step(layer1_df, layer0_df)
    assert set(layer0_df.columns) == original_columns
