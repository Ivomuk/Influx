# Wrap the external lender payload into a reusable production-style function and verify it on the in-memory final output.
import pandas as pd

def build_external_lender_interface(final_capacity_df, include_optional_fields=True, decision_timestamp=None, policy_version_col='output_version'):
    required_cols = ['subscriber_msisdn', 'CreditLimit']
    missing_cols = [col_name for col_name in required_cols if col_name not in final_capacity_df.columns]
    if len(missing_cols) > 0:
        raise ValueError('final_capacity_df is missing required columns: ' + ', '.join(missing_cols))
    interface_df = final_capacity_df.copy()
    interface_df = interface_df.rename(columns={'subscriber_msisdn': 'MSISDN'})
    if decision_timestamp is None:
        decision_timestamp_val = pd.Timestamp.now('UTC').floor('s')
    else:
        decision_timestamp_val = pd.to_datetime(decision_timestamp)
    if policy_version_col not in interface_df.columns:
        interface_df[policy_version_col] = 'unknown_policy_version'
    if 'validity_days' not in interface_df.columns:
        interface_df['validity_days'] = pd.NA
    interface_df['decision_timestamp'] = decision_timestamp_val
    interface_df['policy_version'] = interface_df[policy_version_col]
    if include_optional_fields:
        output_cols = ['MSISDN', 'CreditLimit', 'validity_days', 'decision_timestamp', 'policy_version']
        lender_interface_df = interface_df[output_cols].copy()
        lender_interface_df = lender_interface_df.rename(columns={'validity_days': 'validity_period_days'})
    else:
        lender_interface_df = interface_df[['MSISDN', 'CreditLimit']].copy()
    lender_interface_df = lender_interface_df.sort_values(['MSISDN']).reset_index(drop=True)
    return lender_interface_df

if __name__ == '__main__':
    # Self-contained smoke test — synthetic data matching the required schema.
    sample_df = pd.DataFrame({
        'subscriber_msisdn': ['256700000001', '256700000002', '256700000003'],
        'CreditLimit':       [10000.0,        5000.0,         0.0],
        'output_version':    ['credit_v1_prod'] * 3,
        'validity_days':     [14, 7, 7],
    })
    required_output_df = build_external_lender_interface(
        sample_df, include_optional_fields=False, decision_timestamp='2026-04-23'
    )
    optional_output_df = build_external_lender_interface(
        sample_df, include_optional_fields=True, decision_timestamp='2026-04-23'
    )
    print('--- required fields only ---')
    print(required_output_df)
    print('--- with optional fields ---')
    print(optional_output_df)