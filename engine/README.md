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
# Public environmental data is independent of login and communication credentials.
# live_data = Ingest(DiskCache('engine/.cache'), mode='live')
```

`weather()` returns daily rain/ET0 in mm, temperature C, mineral-soil moisture
percent. Archive uses ERA5 and is labelled historical perfect-forecast
forcing. Forecast derives a 0-7 cm proxy from depth-weighted 0-1, 1-3, 3-9 cm
layers, assuming uniform moisture within the last layer. Missing values remain
null with reasons. Neither input is measured peat groundwater.

`hotspots()` merges NOAA-20/21 regional seven-day CSVs. Both unavailable raises
`DataUnavailable`, never a synthetic empty success. A single source failure sets
`partial`; stale cached data is labelled separately. Heat detections are not
confirmed fires. `archive_hotspots()` requires an explicitly supplied key, uses
five-day chunks, and defaults to NOAA-20 SP; no 2019 NOAA-21 coverage is invented.
A1 did not verify live availability. A4 subsequently verified real cached numeric paths.

Cache filenames are SHA256 of credential-redacted URLs; URLs and credentials are
not persisted. Successful parsed bodies and UTC retrieval time are saved atomically.
`data_as_of` for live adapters means retrieval time, not a satellite detection or
provider model-run time; hotspot records separately contain `detected_at`. Failed
refreshes use validated stale cache, but 401/403 always raises AuthenticationError.
Each request has a ten-second timeout and one retry with backoff by default.
Credentials are passed by callers, never loaded from environment files here.

Five adm4/name/reference-coordinate mappings were verified against BMKG public metadata
on 2026-11-07; no official boundaries are provided. Canals, wells and hamlets are fixtures. Fixture weather
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

A4 service:

`engine.service` exports the exact agent tool interface: `village_status(id,
mode='live', day=0)`, `forecast(id, days=14, mode='live', day=0)`,
`simulate(id, request_dict)`, `get_canals`, `get_resources`, `get_hotspots`.
The lazy default service uses real public inputs and disk cache under
`runtime/engine/http`; importing it causes no network access. Offline tests use
`EngineService(Ingest())` explicitly. Status, forecast and simulation satisfy
strict agent models without extra keys.

Replay precomputes 76 daily states (2019-06-15..2019-08-29), exposing 62 replay
days and up to 14 future days at every replay position. Both historical and
live states use the actual groundwater/PFVI engines. End-of-day states include
that day's weather forcing and are estimates, not field measurements. Live
requests retrieve 16 forecast days. Only initial soil moisture seeds PFVI;
missing later soil moisture is not imputed or used to calibrate the model.
Missing rain/ET0/temperature or initial soil moisture makes the run unavailable.

A4 ingestion correction: ERA5-Land-only selection returned null rain and ET0;
archive selection now explicitly uses ERA5, verified with complete required
fields at all five corrected BMKG reference coordinates. BMKG metadata was
checked from provider cache; names, adm4 IDs and coordinates now match provider
records. Canal/well/hamlet layouts remain synthetic fixtures. Regional weather
is gridded and does not imply village sensor precision.

Simulation curves include the current state at index zero and `days` future
states. Critical means PFVI>=250 OR WT>=60cm. Already critical returns zero;
no baseline crossing returns null; scenario beyond horizon returns null plus
a discrete lower bound (earliest possible unobserved crossing minus baseline
crossing). Both retain horizon-end water/PFVI benefit. A block raises the shared
strip boundary once regardless of number of fixture target IDs; effects are
not multiplied. Pump standby/verification have no invented hydraulic effect.
Replay hotspots are unavailable without archival data; current detections are
never substituted. Backtest remains deferred. A6 below adds bounded initial-condition assimilation
and versioned parameter history; it does not calibrate K/Sy.

Manual provider verification 2026-11-07: all five correct-coordinate archives
had 76 rows and zero missing rain/ET0/temperature/soil-moisture values. Talio
replay day9, 60cm canal raise: baseline crossing June30, scenario crossing July1,
Days Bought=1; horizon-end WT gain=14.263cm, PFVI reduction=5.526. Day8 instead
returns a lower bound of at least8days, not an exact crossing. Parameters were
not retuned to produce these outcomes. Real Talio live day0: PFVI79.875,
WT38.470cm; no critical crossing within 14 days at retrieval time.

A5 API:

```python
from engine.api import create_router
# Parent app owns the operator/public environmental access decision.
app.include_router(create_router(), prefix='/api',
                   dependencies=[Depends(require_operator)])
# Isolated offline tests instead pass create_router(EngineService(Ingest())).
```

Routes cover villages, status/forecast/simulate, canals/resources, initial
parameter history, risk point GeoJSON, hotspots, replay metadata/day plus scenario
alias, unavailable backtest, and POST warmup. All response shapes retain the
contract; full provider/cache/model assumptions are available on status/simulation.
The observations write route belongs to the agents application; engine API exposes parameter history. There is no import-time
service initialization or network. Parent auth exceptions preserve their status.

Inputs are bounded to the available 14-day horizon and 62-day replay window.
Errors return safe contract envelopes: unknown village404, invalid input422,
provider/model unavailable503. Invalid/nonfinite output never becomes JSON.
No provider exception strings, tracebacks or raw validation input are returned.
Warmup runs one bounded five-village batch in one selected mode, at most five
workers; overlapping warmup requests return429. Network retry/timeout limits
remain those of the injected cache. Tests use temporary/synthetic data; manual
real-cache verification explicitly disabled network fetches in its injected cache.
The real-cache TestClient returned200 for Talio live/replay status and replayday9
simulation, preserving the independently verified exact one-day result. No
second deployed server or tunnel was started for this verification.

A6 confirmed WT assimilation (reopened by user):

```python
service = EngineService(ingest, state_path=scoped_path)  # None means memory only
result = service.recalibrate(village_id, observation_dict, observation_id)
```

The agents owner supplies its public-demo/protected scoped persistence path and
owns `/api/observations`. The engine accepts `confirmed=True`, finite WT0..150cm,
matching village and a persistent unique observation ID. Live day must be zero.
A disagreement over30cm returns `discrepancy_requires_verification`, preserves
the pre-update model and does not adapt. Replay always returns
`replay_skips_recalibration` without changing parameters, history, confidence or
adaptive persistence. Replay also ignores previously accepted live corrections.

Accepted observations adjust the village's forecast INITIAL WT CONDITION by at
most15cm per observation, bounded0..150cm overall. This is state assimilation,
not calibration of conductivity/specific yield from one point. K/Sy and PFVI
retention parameters stay unchanged. The actual groundwater and PFVI trajectories
are recomputed, including subsequent intervention simulations. Results retain
old/new strict Status snapshots, version, reason, observed WT, pre-update residual
and applied initial-WT delta. Output remains modelled; accepted-observation
confidence0.7 is explicitly heuristic, not a calibrated probability. This
correction persists until another accepted observation or scoped-state reset;
it is not a time-decaying data assimilation filter.

Parameter history adds the triggering observation ID, actual UTC time, bounded
initial condition, provenance, old/new snapshots and discrepancy. Accepted and
held observation results are idempotent; reusing an ID with different WT/village
is rejected. Returned objects cannot mutate stored evidence. Optional JSON
persistence is atomic with fsync/replace and contains only model evidence, not
contacts, tokens or raw report text. Save failures roll back model state/cache;
invalid state files fail closed. Each state file needs one service/process owner;
cross-process concurrent writers are not supported.

A6 verification uses temporary scoped files, fixtures, restart/idempotency,
replay and cross-village isolation, bounds, disagreement holds, rollback,
parameter-history API and post-assimilation simulation. An isolated memory-only
real-cache check (no network, no demo-state mutation) changed Talio modelled WT
38.470->48.470cm and PFVI79.875->80.893 after a synthetic +10cm observation;
this demonstrates computation, not an actual field observation or validation.

Post-UAT FIRMS2019 archive:

`FirmsArchive(cache, map_key=None).hotspots()` retrieves VIIRS_SNPP_SP standard
processing, June15..August15 2019 in thirteen <=5day official AreaAPI chunks.
Only during an archive request, a missing explicit key is resolved from the
latest programmatic dotenv configuration (fallback process environment). No
credential or keyed URL is returned/logged/persisted. Missing key stays
unavailable. Cache filenames redact path credentials and parsed CSV bodies are
cached for historical reuse. Official documentation:
https://firms.modaps.eosdis.nasa.gov/api/area/

Download bbox is only a prefilter. Detection membership uses haversine <=5km
from actual five BMKG pilot reference coordinates. Union is deduplicated by
satellite/time/coordinate ID; per-village overlapping counts must not be summed.
All13 chunks are required for `total_unique`; partial success instead sets
`total_unique=null`, `retrieved_unique` and incomplete date coverage with exact
missing ranges. `source` is exactly `NASA FIRMS archive`, not confirmed fires.
FIRMS acquisition dates/coverage are UTC. Replay windows are model-day WIB;
a UTC August15 late-evening detection can fall on August16 WIB and therefore
is not displayed in the August15 replay window.

GET `/api/replay/hotspots/summary` exposes counts/coverage without the point list.
Existing hotspots mode=replay now filters real archive data by historical time
and requested pilot circle (radius <=5km, matching archive coverage). Fixtures
do not access real credentials/providers and remain explicitly unavailable.
Verified all13 chunks succeeded: uniqueunion2, Talio1, TalioMuara1, otherpilots0;
detections August9 05:27UTC and August15 17:57UTC. These are actual heat
detections, not validation of groundwater/PFVI predictions.

Post-UAT OSM adapter / roadmap gate:

GET `/api/canals/osm` provides current mapped canal/ditch/drain LineString
GeoJSON from Overpass around the five5km pilot circles. Source and ODbL
attribution accompany any real cached geometry. Whole mapped ways may extend
outside query circles. It is explicitly current geometry, not2019 replay data.
No median hydraulic spacing is inferred from these lines; `spacing.available`
is false, `median_spacing_m` is null, and the400m model spacing remains a labelled
team assumption. Geometry never automatically changes groundwater parameters.

Public Overpass primary and documented alternate were unavailable during the
bounded post-UAT checks, so OSM geometry remains ROADMAP at this gate. The adapter
returns `available:false`, emptyfeatures and an explicit unavailable reason;
this is not a claim that no canals exist. No geometry or spacing was fabricated.
Successes use24h diskcache with labelled stale fallback. Failed requests are
negative-cached for5min to avoid hammering providers during4s UI polling; each
of two documented endpoints has a <=10s timeout and no automatic retry.
Offline tests cover geometry parsing/deduplication, malformed/incomplete
responses, stale cache, truthful spacing and unavailable fixture/API behavior.
