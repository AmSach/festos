# AI disclosure

**Tools used:** Claude (Anthropic) was used as a coding and analysis assistant throughout the build. No AI model runs inside FestOS: every decision the portal makes comes from deterministic rules, optimisation (HiGHS MILP / greedy fallback, simulated annealing, max-flow) and statistics that you can inspect in the code.

## What AI did
- Profiled the data pack and surfaced anomalies: duplicates, inverted times, team-ID inversion, certificate expiries, label conflicts, capacity gaps.

## What the team decided
- Product scope and the principle that **hard needs are never traded for score**: accessibility, gender pools, minors' safety and budget are constraints, not weights.
- Policy choices, all visible in *Assumptions*: blank ≠ gender / consent / clearance; prefer-not-to-say is never re-asked; all-gender floors need Director approval; escorts get private rooms; tentative leader holds; a ₹26 L assumed budget; pessimistic treatment of vendor label conflicts; 3-hour core-sleep rule for bed-nights.
- What needs a human: duplicate merges, high-risk overrides (reason ≥ 20 characters, Director only), property contracts, budget changes, waivers.
- Which assumptions to expose and to whom (role and field permissions).

## How outputs were checked
- **Independent verifier** (`python3 -m festos.verify`): re-derives 25 hard rules directly from the stored plan through a separate code path. It passes on the shipped plan and after all disruption types are applied in sequence.
- **End-to-end tests** (`tests/`, 14 tests): data counts, exactly-two duplicates (no namesake false positives), blank fields never coerced, preview never mutates live data, every disruption keeps all rules, duplicate merch blocked, masking per role, audit-chain tamper detection, token signatures. They pass on Python 3.13 with SciPy (MILP) and on Python 3.11 without it (fallback).
- **Spot checks against raw CSVs:** reconciliation totals, peak-night demand (1,546), accessible beds (26), ID-card gap (1,489), duplicate evidence tables.
- **Visual QA:** every screen rendered and reviewed in a headless browser, including full disruption flows.
- **Known limits** are listed in the README rather than hidden.
