# Planning-pack alignment

Source of truth: planning pack (01 prompts, 05 lessons, 06 phone, 07 orchestration, 08 kickoff),
research brief, blueprint; latest team-lead instructions override scope/schedule.
No reference screenshots have been supplied yet. No claim of visual parity with the earlier app.
No rehearsal source has been opened. Existing matching implementation is retained.

| Capability / behavior | Required reference | Current evidence | Remaining / deviation |
| --- | --- | --- | --- |
| Cached public weather + ERA5 | A1, lessons 1 | Live fetch succeeded: forecast14 rows, replay76 rows; disk cache | Wire numerical service/API |
| NASA live NOAA20/21 hotspots | A1 | Both sources fetched, 56916 regional detections, no key | Filter village radius/time; never label all as fires |
| BMKG village forecast | A1 | All5 adm4 requests succeeded; root identified4 name mismatches and coordinate mismatch | Builder updating seeds to verified metadata before actual weather integration |
| Groundwater | A2 |25 tests incl mass/stability; effective thickness disclosed | Integration to PFVI |
| PFVI | A3 | Assigned builder | Equations, bounded calibration, hand example |
| Days Bought + Replay | A4 | Not implemented | Null/zero/positive/beyond-horizon; real cached2019 forcing |
| Engine API | A5 | Contract fixed | Mount on same service with metadata |
| Tool/database isolation | B1 |14 tests; public and protected scope | Use computed engine independently from auth |
| SMS adapter/security | B2 |22 tests combined; security review and nested-JSON fix | Real delivery untested, stays mock |
| Warden readback and confirmation | B3 | Not implemented | AIR61 ASAPYA -> readback -> standaloneYA |
| Planner/Verifier/approval/follow-up | B5 | Not implemented | Single complete loop; no A6 recalibration under cut plan |
| Open operator and optional PIN later | Latest user override | C1 opens; public storage isolation | Login bypass must not force environmental fixtures |
| Responsive Command | C1 |Build passes; initial cloud mobile/tablet QA and fixes | Final3-width screenshot recheck |
| Map + decisions + village detail | C2/C3/C4 | Shell only | Bind actual API; summaries/poll4s |
| One server + tunnel | lead plan |Launch scripts present, preview tunnel closed after firstQA | Final integrated security + cloudQA at integration gate |
| A6/A7/national/3D/Telegram/Realtime/trace/ask | lead cut list | Not started | Explicitly cut |

Public-source retrieval is verified; end-to-end product use of those sources is NOT yet verified.
All current village names/coordinates, responder contacts and canal details remain explicitly synthetic/unverified seeds.
Field delivery remains mock. Do not present model calibration or replay as independent predictive validation.

Verified BMKG metadata awaiting integration:2002 Talio;2003 Pangkoh Hilir;2004 Pangkoh Hulu;2005 Kantan Muara;
2006 Talio Muara (all under62.11.01). Coordinates returned by provider, not village boundaries.
