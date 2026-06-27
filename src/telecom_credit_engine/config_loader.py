"""
Config loader for the Credit V1 decision engine.

Reads a versioned YAML policy file, validates it against the required
schema, and attaches a deterministic SHA-256 hash so every decision row
can be traced back to the exact config that produced it.
"""

import datetime
import hashlib
import json
from pathlib import Path

import yaml


class ConfigValidationError(Exception):
    """Raised when a policy config file is missing required keys or has invalid types."""


# Every flat key that must appear in the final (flattened) config dict.
REQUIRED_CONFIG_KEYS = frozenset([
    "identity_confidence_restrict_threshold",
    "fraud_restrict_threshold",
    "severe_dsi_threshold",
    "reduce_only_dsi_min",
    "reduce_only_dsi_max",
    "maintain_only_dsi_min",
    "maintain_only_dsi_max",
    "recent_disb_days_threshold",
    "recent_disb_repayment_threshold",
    "low_repayment_ratio_threshold",
    "high_exposure_to_inflow_threshold",
    "high_active_lender_cnt_threshold",
    "thin_file_wallet_days_threshold",
    "thin_file_capacity_cap",
    "policy_capacity_cap",
    "validity_days_restrict",
    "validity_days_reduce",
    "validity_days_maintain",
    "validity_days_increase",
    "output_version",
    "stability_max_increase_pct",
    "stability_max_decrease_pct",
    "stability_restrict_cooldown_days",
    "budget_constraint_enabled",
    "network_lending_budget_total",
    "budget_priority_states",
    "block_timing_manipulation",
    "block_loan_cycling",
    "tnv_base_capacity_factor",
    "tnv_future_margin_multiplier",
    "tnv_churn_cost_multiplier",
    "tnv_treatment_cost_scalar",
    "policy_version",
])


def _flatten(nested: dict) -> dict:
    """Flatten a nested YAML dict into a single-level dict.

    Top-level scalar keys are kept as-is.  Section dicts (e.g.
    ``identity_and_fraud``, ``dsi``) are expanded so that their child
    keys become top-level keys.
    """
    flat: dict = {}
    for key, value in nested.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def compute_config_hash(config_dict: dict) -> str:
    """Return a deterministic SHA-256 hex digest for *config_dict*.

    Internal bookkeeping keys (``_config_hash``, ``_config_loaded_at``,
    ``policy_version``) are excluded so that the hash reflects only the
    operational thresholds.
    """
    excluded = {"_config_hash", "_config_loaded_at", "policy_version"}
    hashable = {k: v for k, v in config_dict.items() if k not in excluded}
    canonical = json.dumps(hashable, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_policy_config(yaml_path: str | Path) -> dict:
    """Load a YAML policy config, validate it, and return a flat dict.

    The returned dict contains every key from
    :data:`REQUIRED_CONFIG_KEYS` plus two bookkeeping keys:

    * ``_config_hash`` -- SHA-256 of the operational thresholds
    * ``_config_loaded_at`` -- ISO-8601 timestamp of when the config was loaded

    Raises
    ------
    ConfigValidationError
        If the file is missing any required key or a value has an
        obviously wrong type (e.g. a threshold that should be numeric
        is a string).
    """
    path = Path(yaml_path)
    with open(path, "r") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ConfigValidationError(
            f"Expected a YAML mapping at top level, got {type(raw).__name__}"
        )

    config = _flatten(raw)

    # --- check required keys ---
    missing = REQUIRED_CONFIG_KEYS - config.keys()
    if missing:
        raise ConfigValidationError(
            f"Missing required config keys: {sorted(missing)}"
        )

    # --- lightweight type checks ---
    _NUMERIC_KEYS = {
        "identity_confidence_restrict_threshold",
        "fraud_restrict_threshold",
        "severe_dsi_threshold",
        "reduce_only_dsi_min",
        "reduce_only_dsi_max",
        "maintain_only_dsi_min",
        "maintain_only_dsi_max",
        "recent_disb_days_threshold",
        "recent_disb_repayment_threshold",
        "low_repayment_ratio_threshold",
        "high_exposure_to_inflow_threshold",
        "high_active_lender_cnt_threshold",
        "thin_file_wallet_days_threshold",
        "thin_file_capacity_cap",
        "policy_capacity_cap",
        "validity_days_restrict",
        "validity_days_reduce",
        "validity_days_maintain",
        "validity_days_increase",
        "stability_max_increase_pct",
        "stability_max_decrease_pct",
        "stability_restrict_cooldown_days",
        "network_lending_budget_total",
        "tnv_base_capacity_factor",
        "tnv_future_margin_multiplier",
        "tnv_churn_cost_multiplier",
        "tnv_treatment_cost_scalar",
    }
    for key in _NUMERIC_KEYS:
        val = config[key]
        if not isinstance(val, (int, float)):
            raise ConfigValidationError(
                f"Config key '{key}' must be numeric, got {type(val).__name__}: {val!r}"
            )

    _BOOL_KEYS = {
        "budget_constraint_enabled",
        "block_timing_manipulation",
        "block_loan_cycling",
    }
    for key in _BOOL_KEYS:
        val = config[key]
        if not isinstance(val, bool):
            raise ConfigValidationError(
                f"Config key '{key}' must be bool, got {type(val).__name__}: {val!r}"
            )

    if not isinstance(config.get("budget_priority_states"), list):
        raise ConfigValidationError(
            "Config key 'budget_priority_states' must be a list"
        )

    # --- attach bookkeeping ---
    config["_config_hash"] = compute_config_hash(config)
    config["_config_loaded_at"] = datetime.datetime.utcnow().isoformat()

    return config
