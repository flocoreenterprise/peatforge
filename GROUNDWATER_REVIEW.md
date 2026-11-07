# Groundwater audit — 7 November 2026

Independent Luna review of the current deterministic model and real cached ERA5 replay inputs. No parameters were changed, and no external provider was called by the audit.

## Reproduced scenario

Talio, replay day 9 (24 June 2019), K4 boundary rise +60 cm, 14-day horizon:

- Current WT 43.432 cm and PFVI 39.059 (RENDAH).
- Baseline WT crosses 60 cm on 30 June, replay day 15; the scenario crosses on 1 July. Days Bought is 1 day.
- On 8 July, baseline WT is 75.490 cm and scenario WT is 61.227 cm: modelled gain 14.263 cm. The approximately 9 cm claim does not describe this scenario/window.
- PFVI at the horizon ends at 93.653 without intervention and 88.127 with intervention. This is not a measured reduction in fire probability.
- In the full replay, the first PFVI >=175 occurs on day 41 (26 July); PFVI >=250 occurs on day 59 (13 August).

A future critical crossing is distinct from current criticality. Current critical means WT >=60 cm OR PFVI >=250. PFVI level remains the PFVI-only classification.

## Physical interpretation and limits

The solver represents a 400 m strip with 51 nodes, conductivity 5 m/day and specific yield 0.20. The effective hydraulic thickness is 24 m. These are team assumptions, not surveyed village parameters. The 24 m value was tuned to a zero-forcing boundary-response benchmark; it is not peat depth or independent field calibration.

Baseline canal water depth is 80 cm at both endpoints. A +60 cm boundary rise changes both endpoint depths to 20 cm. The mid-strip response is delayed because a boundary head change must propagate across the strip; weather-driven drying continues. Thus a meaningful end-of-horizon WT gain can coexist with only one extra day before the first threshold crossing.

K4 and K4+K5 return exactly the same result: target IDs are validated but their count and geometry do not change this single symmetric-strip calculation. Adding blocks cannot honestly be presented as an additive benefit. The model does not rank real field layouts.

The supported simulation horizon is 14 days. A 28-day request is rejected. Longer projections could expose later behaviour but do not themselves create a stronger intervention, and cannot establish field realism without geometry, measurements and validation.

The defensible displayed scenario therefore remains the supported +60 cm symmetric boundary change: 1 modelled day and 14.263 cm horizon-end WT gain for this date. No claim is made that it is the physically optimal field intervention. Keep both outcomes visible, with the assumptions, instead of tuning parameters or selecting extra blocks to inflate the headline.

## Verification

Auditor used EngineService with cached-only DiskCache (network fetcher raises), the Talio replay cache, and identical K4/K4+K5 requests. It reviewed groundwater.py, params.yaml, service.py, ingest.py, cache.py, villages.py and their existing tests. Subsequent regression tests verify unchanged multi-target results, actual parameter metadata and the 14-day cap. Replay weather is historical perfect-forecast input, not an out-of-sample prediction.
