"""Tests for the YAML config loader and hash propagation."""

import copy
import os
import tempfile

import pytest
import yaml

from telecom_credit_engine.config_loader import (
    ConfigValidationError,
    REQUIRED_CONFIG_KEYS,
    compute_config_hash,
    load_policy_config,
)

# Path to the canonical YAML shipped with the repo.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
_YAML_PATH = os.path.join(_REPO_ROOT, "configs", "credit_v1_policy_config.yaml")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _write_yaml(data: dict, tmp_path: str) -> str:
    """Write *data* as YAML to a temp file and return its path."""
    path = os.path.join(tmp_path, "policy.yaml")
    with open(path, "w") as fh:
        yaml.dump(data, fh, default_flow_style=False)
    return path


# ------------------------------------------------------------------
# Tests: loading the canonical YAML
# ------------------------------------------------------------------

class TestLoadCanonicalYaml:
    """The checked-in YAML must load without errors."""

    def test_loads_successfully(self):
        config = load_policy_config(_YAML_PATH)
        assert isinstance(config, dict)

    def test_all_required_keys_present(self):
        config = load_policy_config(_YAML_PATH)
        for key in REQUIRED_CONFIG_KEYS:
            assert key in config, f"Missing required key: {key}"

    def test_bookkeeping_keys_attached(self):
        config = load_policy_config(_YAML_PATH)
        assert "_config_hash" in config
        assert "_config_loaded_at" in config
        assert len(config["_config_hash"]) == 64  # SHA-256 hex

    def test_values_match_original_defaults(self):
        """Spot-check a few values against the Python fallback dict."""
        config = load_policy_config(_YAML_PATH)
        assert config["identity_confidence_restrict_threshold"] == 0.70
        assert config["fraud_restrict_threshold"] == 0.80
        assert config["policy_capacity_cap"] == 20000.0
        assert config["thin_file_capacity_cap"] == 5000.0
        assert config["budget_priority_states"] == ["Healthy", "Recovered"]
        assert config["output_version"] == "credit_v1_python_decision_engine_prod"


# ------------------------------------------------------------------
# Tests: hash determinism
# ------------------------------------------------------------------

class TestConfigHash:
    """Same config must always produce the same hash."""

    def test_deterministic_hash(self):
        config = load_policy_config(_YAML_PATH)
        hash1 = config["_config_hash"]
        hash2 = compute_config_hash(config)
        assert hash1 == hash2

    def test_same_config_same_hash(self):
        c1 = load_policy_config(_YAML_PATH)
        c2 = load_policy_config(_YAML_PATH)
        assert c1["_config_hash"] == c2["_config_hash"]

    def test_different_config_different_hash(self):
        c1 = load_policy_config(_YAML_PATH)
        # Mutate one threshold and recompute.
        c2 = {k: v for k, v in c1.items() if not k.startswith("_")}
        c2["policy_capacity_cap"] = 99999.0
        assert compute_config_hash(c2) != c1["_config_hash"]


# ------------------------------------------------------------------
# Tests: validation errors
# ------------------------------------------------------------------

class TestValidationErrors:
    """Missing keys and wrong types must raise ConfigValidationError."""

    def test_missing_required_key_raises(self, tmp_path):
        config = load_policy_config(_YAML_PATH)
        # Remove bookkeeping, then drop a required key.
        flat = {k: v for k, v in config.items() if not k.startswith("_")}
        del flat["fraud_restrict_threshold"]
        path = _write_yaml(flat, str(tmp_path))
        with pytest.raises(ConfigValidationError, match="fraud_restrict_threshold"):
            load_policy_config(path)

    def test_wrong_type_numeric_raises(self, tmp_path):
        config = load_policy_config(_YAML_PATH)
        flat = {k: v for k, v in config.items() if not k.startswith("_")}
        flat["severe_dsi_threshold"] = "not_a_number"
        path = _write_yaml(flat, str(tmp_path))
        with pytest.raises(ConfigValidationError, match="severe_dsi_threshold"):
            load_policy_config(path)

    def test_wrong_type_bool_raises(self, tmp_path):
        config = load_policy_config(_YAML_PATH)
        flat = {k: v for k, v in config.items() if not k.startswith("_")}
        flat["budget_constraint_enabled"] = "yes"
        path = _write_yaml(flat, str(tmp_path))
        with pytest.raises(ConfigValidationError, match="budget_constraint_enabled"):
            load_policy_config(path)

    def test_non_dict_top_level_raises(self, tmp_path):
        path = os.path.join(str(tmp_path), "bad.yaml")
        with open(path, "w") as fh:
            fh.write("- item1\n- item2\n")
        with pytest.raises(ConfigValidationError, match="YAML mapping"):
            load_policy_config(path)


# ------------------------------------------------------------------
# Tests: config_hash in decision engine output
# ------------------------------------------------------------------

class TestConfigHashInDecisionOutput:
    """config_hash must propagate into select_final_capacity_output."""

    def test_config_hash_column_present(self):
        import numpy as np
        import pandas as pd
        from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import (
            DECISION_ENGINE_CONFIG,
            select_final_capacity_output,
        )
        from telecom_credit_engine.config_loader import compute_config_hash

        # Build a minimal tnv_actions_input_df that select_final_capacity_output expects.
        rows = [{
            "feature_dt": pd.Timestamp("2026-06-26"),
            "subscriber_msisdn": "254700000001",
            "expected_tnv": 100.0,
            "target_capacity_raw": 5000.0,
            "action": "MAINTAIN",
            "stability_multiplier": 1.0,
            "update_direction": "maintain",
            "validity_days": 14,
            "primary_policy_reason": "none",
        }]
        tnv_df = pd.DataFrame(rows)

        # Inject a config hash into the fallback config.
        cfg = copy.deepcopy(DECISION_ENGINE_CONFIG)
        cfg["_config_hash"] = compute_config_hash(cfg)

        result = select_final_capacity_output(tnv_df, cfg)
        assert "config_hash" in result.columns
        assert result["config_hash"].iloc[0] == cfg["_config_hash"]
        assert len(result["config_hash"].iloc[0]) == 64
