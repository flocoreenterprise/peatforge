# Planning-pack alignment

Fresh repository implementation only. Planning documents were read-only; rehearsal was never opened. No reference screenshots were supplied.

| Capability | Implemented evidence | Boundary |
|---|---|---|
| A1-A6 engine | Five BMKG reference points, cached public weather/ERA5, groundwater/PFVI, Days Bought, bounded confirmed-WT assimilation | Modelled, not independently field calibrated; replay unchanged by assimilation |
| B3-B5 Warden/Scout/Planner/Verifier | Persisted SMS readback, standalone YA, proposal, human approve/reject and follow-up; risk crossings and code policy | Public demo and Scout outbound remain mocked; approval is not execution |
| C1-C5 Command | Map/inbox/detail/trace; 4s polling; input-based simulation cache | Original mock SMS loop passed cloud QA; final screenshots scheduled12:15 |
| C7 light national | NOAA-20/21 province counts with pinned2017/34-unit polygon coverage | Heat detections, not confirmed fires; no national peat risk |
| B8 auth and field links | Configured PIN cookies, isolated storage, scoped responder link and3s polling | Public demo default; no real contacts exposed |
| B6a TwiML | Strong expiring signed session, consented chosen UAT, same Warden pipeline, current-live policy and hard daily cap | Provider/carrier UAT not run; no real calls or SMS sent |
| Browser Realtime | Authorized after five QA commits; implementation underway | Offline tests only; user enters key later; 12:45 fallback gate |
| Deployment | One FastAPI server on loopback8000 | Tunnel stopped per unattended restriction; no push |
| Remaining cuts | A7 hotspot QA,3D,Telegram,askbar | Not started |

All five team-lead QA fixes are committed. Actual cached Talio replay day9 gives1modelled day and14.263cm horizon-end WT gain. K4/K4+K5 are identical symmetric-strip scenarios; no numbers were tuned. See GROUNDWATER_REVIEW.md.

The B6a merge root suite passed162tests +37subtests. Updated task results are recorded in BUILDLOG.md. Real provider delivery and real microphone quality remain attended UAT, not offline-test claims.
