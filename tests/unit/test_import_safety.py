"""
Import-safety tests: verify that no module in src/ executes side effects
at import time. Each test imports a module that previously had unguarded
module-level execution and asserts that the demo variables are absent from
the module namespace after import.

Covered modules and the variables that must NOT be present at module scope:
  external_lender_interface   — required_output_df, optional_output_df
  run_credit_v1_decision_engine — engine_outputs, vw7/vw8/vw9 DataFrames
  dim3_eligibility_api        — eligibility_results_df, msisdn_list_sample
"""
import importlib
import pytest


def _import(dotted_path):
    """Import a module by dotted path, reloading to bypass the import cache."""
    mod = importlib.import_module(dotted_path)
    return importlib.reload(mod)


# ---------------------------------------------------------------------------
# external_lender_interface
# ---------------------------------------------------------------------------

def test_external_lender_interface_no_side_effects():
    mod = _import('telecom_credit_engine.interfaces.external_lender_interface')

    assert not hasattr(mod, 'required_output_df'), (
        "'required_output_df' exists at module scope — "
        "__main__ guard is missing or broken in external_lender_interface.py"
    )
    assert not hasattr(mod, 'optional_output_df'), (
        "'optional_output_df' exists at module scope — "
        "__main__ guard is missing or broken in external_lender_interface.py"
    )


def test_external_lender_interface_callable_after_import():
    mod = _import('telecom_credit_engine.interfaces.external_lender_interface')
    assert callable(getattr(mod, 'build_external_lender_interface', None)), (
        "build_external_lender_interface not found — module did not load correctly"
    )


# ---------------------------------------------------------------------------
# run_credit_v1_decision_engine
# ---------------------------------------------------------------------------

_DECISION_ENGINE_DEMO_VARS = (
    'engine_outputs',
    'vw7_credit_v1_policy_prefilter',
    'vw8_credit_v1_tnv_action_evaluation',
    'vw9_credit_v1_final_capacity_output',
)

@pytest.mark.parametrize('var', _DECISION_ENGINE_DEMO_VARS)
def test_decision_engine_no_side_effects(var):
    mod = _import('telecom_credit_engine.decisioning.run_credit_v1_decision_engine')
    assert not hasattr(mod, var), (
        f"'{var}' exists at module scope — "
        f"__main__ guard is missing or broken in run_credit_v1_decision_engine.py"
    )


def test_decision_engine_callable_after_import():
    mod = _import('telecom_credit_engine.decisioning.run_credit_v1_decision_engine')
    assert callable(getattr(mod, 'run_credit_v1_decision_engine', None))


# ---------------------------------------------------------------------------
# dim3_eligibility_api
# ---------------------------------------------------------------------------

_ELIGIBILITY_API_DEMO_VARS = (
    'eligibility_results_df',
    'msisdn_list_sample',
)

@pytest.mark.parametrize('var', _ELIGIBILITY_API_DEMO_VARS)
def test_eligibility_api_no_side_effects(var):
    mod = _import('telecom_credit_engine.interfaces.dim3_eligibility_api')
    assert not hasattr(mod, var), (
        f"'{var}' exists at module scope — "
        f"__main__ guard is missing or broken in dim3_eligibility_api.py"
    )


def test_eligibility_api_callable_after_import():
    mod = _import('telecom_credit_engine.interfaces.dim3_eligibility_api')
    assert callable(getattr(mod, 'check_eligibility', None))
