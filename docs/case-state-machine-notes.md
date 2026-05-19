# Case State Machine Notes

## Allowed transition model
- Source of truth: `app/services/case_state_machine.py`.
- `CASE_STATUS_TRANSITIONS` defines the explicit allowed `from -> to` transitions.
- `step_for_status` derives `currentStep` and `nextBestAction` from `status`.
- Any transition request is validated with `validate_case_transition(...)` before persisting.

## Requires-review prerequisite
- `status = requires_review` is only valid when payment is confirmed.
- Guard used: `has_confirmed_payment(...)`.
- Confirmation is true when:
1. Case is already in a payment-confirmed status (`payment_approved`, `paid_unlocked`, `full_analysis_unlocked`, `analysis_in_progress`, `completed`), or
2. There is an unlock order in `paid` state for product `FULL_ANALYSIS_UNLOCK` or `ANALISIS_COMPLETO_HISTORIA_LABORAL`, or
3. There is an `approved` payment tied to those products.
- If the guard fails, backend returns business error `409 PAYMENT_REQUIRED` and does not persist the transition.

## Current-step consistency
- Backend does not accept arbitrary `currentStep` from client.
- `currentStep` is always server-derived from `status` using `step_for_status`.

## Async/webhook consistency
- Payment and other async-style flows now run transition validation through the same state machine.
- Invalid transitions (including forced `requires_review` without payment) fail with business conflict and no state persistence.

## Data migration strategy for inconsistent legacy rows
- Migration: `alembic/versions/20260519_0021_fix_requires_review_without_payment.py`.
- Scope: cases with `status = requires_review` and no confirmed payment evidence.
- Strategy:
1. Recover latest prior non-invalid status from `case_status_history` when available.
2. Fallback to `preview_locked` if prior status is unavailable or unsafe.
3. Recompute `current_step`/`next_best_action` from fallback status.
4. Insert a new `case_status_history` row documenting the sanitation change (`from`, `to`, `reason`, `actor=system`, timestamp).
