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

A1 ingest foundation (Python 3.11+, stdlib only):

```python
from engine.ingest import Ingest
from engine.cache import DiskCache
fixture_data = Ingest()  # explicit synthetic provenance, never network
weather = fixture_data.weather('62.11.01.2002')
# Configured private runtime only; PUBLIC_DEMO must keep using Ingest().
# live_data = Ingest(DiskCache('engine/.cache'), mode='live')
```

`weather()` returns daily rain/ET0 in mm, temperature C, mineral-soil moisture
percent. Archive uses ERA5-Land and is labelled historical perfect-forecast
forcing. Forecast derives a 0-7 cm proxy from depth-weighted 0-1, 1-3, 3-9 cm
layers, assuming uniform moisture within the last layer. Missing values remain
null with reasons. Neither input is measured peat groundwater.

`hotspots()` merges NOAA-20/21 regional seven-day CSVs. Both unavailable raises
`DataUnavailable`, never a synthetic empty success. A single source failure sets
`partial`; stale cached data is labelled separately. Heat detections are not
confirmed fires. `archive_hotspots()` requires an explicitly supplied key, uses
five-day chunks, and defaults to NOAA-20 SP; no 2019 NOAA-21 coverage is invented.
No live provider availability has been verified during A1.

Cache filenames are SHA256 of credential-redacted URLs; URLs and credentials are
not persisted. Successful parsed bodies and UTC retrieval time are saved atomically.
`data_as_of` for live adapters means retrieval time, not a satellite detection or
provider model-run time; hotspot records separately contain `detected_at`. Failed
refreshes use validated stale cache, but 401/403 always raises AuthenticationError.
Each request has a ten-second timeout and one retry with backoff by default.
Credentials are passed by callers, never loaded from environment files here.

Five seeds have unverified synthetic adm4/name/coordinate mappings; no official
boundaries are provided. Canals, wells and hamlets are fixtures. Fixture weather
is a repeated three-day synthetic sequence, including in replay date windows;
it must never be described as downloaded ERA5 history.

Offline verification: `python -m unittest discover -s engine/tests -v`.
Tests inject provider bodies/failures and isolate disk caches in temporary folders.

A2 groundwater:

```python
from engine.groundwater import load_params, solve_groundwater
result = solve_groundwater([0, 10], [4, 4], params=load_params(),
                           initial_wt_cm=40, canal_wt_cm=80)
```

Each returned point is the state AFTER its daily forcing. Hydraulic profiles
are metres above the effective impermeable datum. `wt_cm` is centimetres below
peat surface, positive down. `canal_wt_cm` accepts a scalar or daily depths.
`initial_profile_m` enables continuity between runs. No missing forcing is
silently converted to zero.

The N=51 conservative interior-node scheme uses automatic explicit substeps and
asserts the conductivity-weighted stability ratio is <=0.90. Positive rain raises
water table; ET0 lowers it. Heights are clipped at surface and datum with runoff
and unmet-demand volumes explicitly accounted for. Mass balance is per metre
of strip width (m2) over 49 interior control volumes (392m at L400); the two
boundary nodes are external reservoirs. Daily and cumulative residuals are
reported. This ledger is a numerical conservation check, not model validation.

The 24m effective hydraulic thickness is a TEAM ASSUMPTION used to set diffusion
speed, not a measured peat thickness. The synthetic 30-day, no-forcing benchmark
at K=5m/day, Sy=0.20, L=400m starts flat at 60cm depth; raising canal boundary by
60cm yields 0.346m mid-strip rise. This tunes toward the ~0.35m target in A2;
the planning note's Sebangau 0.38m literature benchmark has not been independently
verified and is not independent validation. YAML source strings carry doc04
literature-snippet caveats and identify unverified team values.

If the explicit solver exceeds its substep budget, the entire run restarts
from the original profile using a labelled lumped bucket approximation, with
the same output shape and a `fallback_reason`. Invalid inputs always raise.
Callers can require the resolved solver using `allow_fallback=False`.

A3 PFVI:

`run_pfvi(wt_cm, rain_mm, temperature_c, ...)` consumes positive-down centimetres,
returns a finite 0..300 index, deterministic risk level, and daily DF/RF/WTF
components. Missing/nonfinite forcing raises. First-day previous rain is unknown
(`None`) unless explicitly provided; `last_rain_mm` supports sequence continuation.
Each returned value is post-day, matching groundwater. Drying defaults to an
assumed annual rainfall R0=3000mm, not that day's rain.

Equations credited to peatfr / Mahdiyasa, Melly, Pasaribu, Taufik and Muljadi
(2025), package author Melly. Primary reference read during implementation:
https://github.com/mellygsln/peatfr/blob/main/R/Function%20Fire%20Predict.R
Upstream DESCRIPTION declares MIT. This independent implementation deliberately
corrects the upstream `c(Rf[-length(Rf)], NA)` same-day rain alignment to the
specified previous-day rule; public outputs are clamped after each step.

`calibrate(...)` uses deterministic two-start bounded Nelder-Mead with aH 0..50,
bH .01...5, n 1.05..8, alpha 5..300cm. Soil DIobs uses the 15/45 mineral proxy;
`load_pfvi_config()` reads these proxy bounds and default values from YAML.
The objective combines trajectory MSE and explicitly labelled dry-equilibrium
priors 175@40cm / 250@60cm at 28C and R0=3000. Those priors also define initial
team defaults and are NOT independent validation. Output reports convergence;
a finite bounded result with `converged=false` must not be called a successful
scientific calibration. No parameters are silently persisted or adapted.
