# Engine — owner: Lead

Read ../AGENTS.md and ../CONTRACT.md. Edit engine/ only. Python; no product code at kickoff.
Source prompts: read-only prep/01-codex-prompts.md, section A.

| Prompt | Task |
| --- | --- |
| A1 | Weather, soil moisture, FIRMS and BMKG ingest; disk cache; five village fixtures |
| A2 | Groundwater Boussinesq solver, stability, bucket fallback, mass balance |
| A3 | PFVI equations, bounded calibration, provenance |
| A4 | Status, Days Bought, historical replay and optional backtest |
| A5 | FastAPI engine endpoints and warm-up |
| A6 | Confirmed field observations, recalibration and parameter history |
| A7 | Optional hotspot QA snapshot and event classification |
| A8 | Optional national overview (extra in updated prompt pack) |

Schedule: A1 -> A2 -> A3; A4 -> A5; A6 -> optional A7/A8. One writer owns this folder at a time.
Audit after each block; mock fixtures only in tests; isolated temporary state.
Required checks include mass balance, RF branches, PFVI clamps, bounded parameters,
Days Bought null/zero/positive and replay. Do not tune to an advertised demo number.
Return files changed, test commands with pass/fail, open risks. Orchestrator commits after passing checks.
