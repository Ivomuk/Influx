"""
telecom_credit_engine.interventions
=====================================
Reserved for decomposition of run_layers_5_to_8.py (Layer 6).

Planned modules:
  build_intervention_flags.py  — per-subscriber intervention flag logic
                                 (wallet_drop, rising_dsi, missed_repayment,
                                  repeat_borrowing, cooling_off, recovery)
  intervention_actions.py      — maps flag combinations to intervention_action

Currently lives in:
  state_management/run_layers_5_to_8.py → build_intervention_df()
"""
