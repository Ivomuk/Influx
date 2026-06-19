# Repository Analysis: Telecom-Controlled Lending Capacity Orchestration (v1)

## Scope Reviewed

This repository contains:
- Layer 0/1 SQL feature engineering views.
- QA SQL views for validating normalized event integrity.
- Deterministic (rule/config-driven) Layer 2–4 decision engine modules.
- Deterministic Layer 5–8 customer-state/intervention/portfolio/governance modules.
- A minimal Layer 9 external lender interface.
- Small sample extracts for disbursements, repayments, and non-loan transactions.

## Architectural Fit vs Stated Objective

The implementation matches the documented design intent: telecom-internal intelligence is retained while lenders receive only minimal outputs (`MSISDN`, `CreditLimit`, optionally validity and policy metadata).

## Layer-by-Layer Observations

### Layer 0/1 Data Foundation

1. **Event normalization is explicit and auditable**
   - Event-family mapping (`loan_disbursement`, `loan_repayment`, `wallet_inflow`, `wallet_outflow`, `spend_behavior`, `alt_credit_signal`) is deterministic.
   - Subscriber-role attribution (`subscriber_msisdn`) differs correctly by event context (borrower, sender, receiver).
   - Signed amount convention is coherent (repayments negative, disbursements positive, spend/outflow negative).

2. **Daily and 30-day features are production-relevant**
   - Daily rollups include loan, wallet, and lender activity counters.
   - 30-day windows create exposure, repayment ratio, recency metrics, wallet activity changes, stacked/repeated borrowing indicators.

3. **Coverage checks are appropriately separated from feature views**
   - Duplicate FID check.
   - Invalid/blank subscriber identifier check.
   - Service-family mapping coverage check.
   - Signed-amount sanity check by event family.

### Layer 0 Scoring View (Deterministic Proxy Models)

The `vw4` layer uses deterministic rules as v1 stand-ins for learned models:
- Repayment probability.
- Credit loss rate.
- Churn/cooling risk.
- Future transaction margin potential.
- Treatment cost score.
- Fraud/abuse risk score.
- Behavior consistency score.
- Debt stress index and coarse state probabilities.

This is an acceptable v1 bootstrap strategy and aligns with your requirement for a working deterministic baseline.

### Governance + Conservative Action Layer (`vw5`, `vw6`)

`vw5` converts risk/behavior profile into a **primary reason code** (e.g., severe DSI, stacked borrowing, low repayment). `vw6` maps this into a **recommended action** and **conservative credit cap**, including hard-zero outcomes under severe stress / very low repayment.

This cleanly separates “why” (reason codes) from “what” (action + cap).

### Layer 2–4 Python Decision Engine

Strengths:
- Configuration externalized (`DECISION_ENGINE_CONFIG`) and reusable action specs (`ACTION_SPECS_DF`).
- Pre-filter policy controls include identity, fraud, severe stress, exposure-to-inflow, thin-file, and recency guardrails.
- Feasible action-set generation is explicit and inspectable.
- TNV-style action evaluation balances margin upside versus expected loss/churn/treatment downside.
- Final selection applies policy cap, stability multiplier, validity windows, and output versioning.

### Layer 5–8 Operationalization

The modules operationalize post-decision lifecycle management:
- **Layer 5**: customer operating states with persistence constraints.
- **Layer 6**: intervention routing based on trigger combinations.
- **Layer 7**: portfolio guardrails (restrict rate, distress rate, concentration, stacking pressure).
- **Layer 8**: governance log with manual-review and audit trigger flags.

This is a strong design choice because it treats lending as a controlled system, not just a single credit-limit prediction.

### Layer 9 External Interface

The final interface correctly minimizes outbound data to lenders:
- required: `MSISDN`, `CreditLimit`
- optional: validity period, decision timestamp, policy version

This is consistent with anti-gaming and IP-protection principles.

## Data Sample Observations

The repository sample files are very small extracts:
- `Repayments.txt`: 2 lines (header + sample rows context).
- `disbursement.txt`: 2 lines.
- `Transactions.txt`: 4 lines.

These are suitable for structural sanity checks but not statistical calibration or threshold tuning.

## Gaps / Risks to Address Before Production

1. **Identity confidence is currently near-trivial in SQL v1**
   - `identity_confidence_score_v1_rule` is mostly a non-null check; this should evolve into stronger identity-integrity controls.

2. **Action search currently allows DECLINE/RESTRICT in candidate set universally**
   - This is safe but may increase evaluation overhead and can bias action-competition if not carefully interpreted.

3. **No explicit uncertainty banding in deterministic scores**
   - Consider confidence classes (high/medium/low confidence) that force conservative actions in low-confidence conditions.

4. **Very small visible sample files**
   - Calibration evidence (threshold sensitivity, TNV backtesting, fairness/responsible-lending checks) is not present in this repository snapshot.

## Practical Next Steps (v1.1)

1. Add explicit feature snapshot/output schemas (data contracts) for each layer.
2. Add unit tests for reason-code precedence and feasible-action logic.
3. Add reproducible backtest notebook/script for TNV + policy override outcomes.
4. Strengthen identity confidence and abuse-linkage features.
5. Add drift/health monitoring metrics for each deterministic score distribution.

## Conclusion

The repository is coherently structured and implements your intended telecom-controlled orchestration pattern end-to-end: normalized events -> feature/risk proxies -> policy prefilter -> TNV evaluation -> conservative final capacity -> lifecycle governance -> minimal lender output. For a deterministic v1, this is a strong and production-minded baseline.
