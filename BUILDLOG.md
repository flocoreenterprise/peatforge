# Build log — 7 November 2026, Asia/Jakarta

## Kickoff

- Repo created fresh at C:/Users/fadli/Desktop/peatforge. Planning documents only; rehearsal never opened.
- Commit ddeb62b: kickoff contract, agents, secrets script, folder skeletons.
- Validation: three TOML files parse; all 20 original environment fields empty; ignore checks pass.
- Secrets helper tested on fake temporary settings: format validation, no embedded URL credentials,
  preservation of unrelated lines, duplicate removal. Fixed Windows File.Replace null argument.
- User authorized Phase 2 with "langsung go". Builders started 09:41 WIB. Freeze remains 13:00.
- User subsequently requested open operator UI, no PIN/key setup now. Commit 51c7de7:
  PUBLIC_DEMO defaults true with fixture-only data/storage and mocked external channels. Protected PIN mode retained for later.
- The added PUBLIC_DEMO field brings the environment inventory to 21; all example values remain empty.

## Execution plan

Three Sol 6.1 medium builders, one folder each; root orchestrates and commits. Same-folder tasks are sequential.
Engine: A1 -> A2 -> A3 -> audit -> A4 -> A5 -> audit -> A6 -> audit; optional A7/A8 only if time.
Agents: B1 -> B2 -> B7 -> audit/security -> B3 -> B4 -> B8 -> audit/security -> B5 -> B6a -> audit/security.
Web: C1 -> C2 -> C3 -> C4 -> C5 -> C6; cloud manual QA and Terra screenshots per screen; optional C7/C8 after core.
Reviewers occupy a free builder slot. All test reports must show command and pass/fail before task commit.

Manual cloud QA uses an HTTPS tunnel to this Python/FastAPI application. Sites hosting supports Worker/static
deployments, not this Python backend, so no substitute architecture or disconnected static deployment is introduced.
No provider keys, actual recipients or live delivery have been configured. Real SMS/phone UAT remains separate from mock validation.

## Evidence

Task results are appended here by the orchestrator. Test counts are only recorded after actual execution.

### User correction — source/auth independence

The user's approved side conversation clarified that skipping PIN does NOT require fixture data.
Public environmental provider data and historical archives must be integrated, cached, verified and labelled.
Only contact state and external messaging remain mock in the open demo. PUBLIC_DEMO is an access/channel choice,
not an environmental source switch. Existing matching implementation is retained; no restart.
Reference app may be used visually/behaviorally when its URL is available, never by reading old source.

### A1 and B1

- A1 commit 08bf942; 14 offline tests pass, including archive date bound added after Luna audit.
- B1 commit ff92501; 14 offline tests pass, including public/protected database scope separation after Luna audit.
- Root dependency environment created. PowerShell launch scripts parse successfully; actual launch succeeded on 8000.
- Real provider retrieval checks begun independently of credentials. No messages/calls sent.

### Course correction and finished commits

- Team lead establishes official start09:31, must-have demo12:00, integration12:00-12:30, freeze13:00.
- A2 commit6859ba6:25 engine testsPASS, steep-profile monotonicity fixed after Luna audit.
- C1 commitac9e334: buildPASS, API behaviorPASS, dependency audit0; initial cloud QA found and fixed mobile attribution,
  missing retry label and muted contrast. Final desktop screenshot evidence needs recapture.
- B2 commitefa0d41:22 combined backend testsPASS. Security found noP0/P1; nested authenticated JSON now controlled422.
- Public fetch checks succeeded: forecast14, archive2019-06-15..2019-08-29 76, BMKG1 village result,
  NASA regional hotspot56916 records from both feeds (not village fires). Cache runtime/engine/http.
- Preview tunnel closed on course correction. Integrated tunnel will open after final security gate.
- Scope/cuts and source-behavior mapping tracked in FEATURE_PARITY.md. Visual reference only user-supplied screenshots.

### Engine gate progress

- A3 commit7963c84:33 testsPASS, Luna audit no findings.
- A4 commit032c493:43 testsPASS incl agent response shape validation and correct parameter-history timestamp.
- Corrected all5 names/coordinates using actual BMKG metadata. Weather cache re-fetched for corrected coordinates.
- Found explicitERA5-Land returns null rain/ET0 despite populated dates/temp/soil. Changed toERA5; all5 archives76days now complete.
- Root verified actualcached Talio day9: modelled WT/PFVI, canal+60cm scenario delays crossingJune30 toJuly1 (+1day).
  Day8 has a lower-bound benefit, not a fabricated exactdays value. All5 live numeric paths also verified.
- A5 API is next; no change to external messaging credentials or recipients.

### Engine gate passed, Warden completed

- A5 commit988b74f:52 testsPASS, actual-cache API checks200 with network disabled; bounded validation and finiteJSON.
- Auditor live/day guard fixed; root caught whole-region hotspot default, now union of5pilot-radius circles plus dedup.
- B3 commitbe93968:33 backend testsPASS; shared EngineService/API/tools, actual source independent from open auth.
- Luna found inbound receipt retry could skip failed processing. Fixed durable completion and original context;
  failed confirmation hooks resume same observation without duplicates. Transient failure is503, not invalid422.
- B5 underway: persisted proposal/approval/follow-up loop, mock delivery only, no adaptive recalibration.
- B5: Planner/Verifier + idempotent approval/follow-up + contextual SMS progress; 44 backend unittest tests PASS, audit context issues fixed; mock delivery only.
- C2: five real BMKG pilot markers, modelled risk and filtered hotspots, Live/Replay polling4s; web build PASS; root full suite96 +15subtests PASS; actual API smoke PASS.
- C3: real B5 decision approvals/rejections and SMS simulator/progress polling4s; web build PASS, formatter7 assertions PASS; backend full suite96+15subtests PASS. Integrated security: noP0/P1; synchronous live-webhook ack P2 deferred until realSMS.
- C4: summary-first village detail, modelled PFVI/WT, honest Days Bought, WT/PFVI curves and Replay; strictTS + webbuild PASS; actualcached replay9 gives1day; light/dark CSS corrected.
- Integration: cloud SMS loop passed through approval + exact1day mock follow-up; fixed external MapLibre worker asset and mjs MIME; fullsuite96+15subtests PASS; compact mobile controls, no duplicate mock banner.
- Core gate10:50 accepted; pending UI nits for12:15: mobile attribution competes with controls, small chart labels, long assumptions disclosure; no visual polishing now. Eight pending files alreadycommitted484c7e3.
- A6: bounded per-village initial-WT assimilation, before/after/history and idempotent persistence; engine60 PASS; root ready-block115+15subtests PASS; replay remains unchanged.
- B4: persisted risk crossings, SMS-first autonomy, quiet21-05 exceptEKSTREM, hard1call/day +30s cooldown, real trace events and A6 bridge; agents55 PASS; root115+15subtests PASS.
- C5: proposal-linked actual agent event timeline and scoped transcript, polling4s; isolatedstrictTS and rootwebbuild PASS; shared App wiring included in upcoming integration commit.
- C7 light: exact province polygon counts on pinned2017/34-unit data, cached5min; engine67tests PASS inclroutebounds/pointomission; strictTS+webbuild PASS; realcache classification5.26s, no nationalrisk claims.
- B8/integration: configured PIN/cookies, scoped responderlinks+3sfieldUI, protectedmock default, URLredaction/CSP, asyncSMSoffload, Scout/C5/C7/A6UI wiring; root133tests+33subtests PASS; webbuild+20formatterassertions PASS; secretsetupshape/uniqueness PASS.

- QA1 Scout: existing cloud POST route verified and banner absent; public-demo Scout failures stay in a quiet retry status instead of main banner; Scout11tests PASS, webbuild PASS.
- QA2 critical clarity: PFVI level preserved; current WT/PFVI critical flags and WT-critical map/detail labels separated from future forecast; root155tests+37subtests PASS, webbuild +10presentationassertions PASS; no numerical tuning.
- QA3 physical audit: preserved +1day; actualday9 horizonWTgain14.263cm, K4/K4+K5 identical; exposed actual400m/24m-effective assumptions and14day cap, no tuning; root156tests+37subtests PASS, webbuild+20scenario/criticalassertions PASS.
- QA4 simulation caching: full-input SHA256 + bounded shared browser cache; unchanged4spolls issue1POST, changedforcing/liveA6 invalidate, replay ignoreslivehistory; root157tests+37subtests PASS, webbuild/cache request-count tests PASS.
- QA5 replay default: publicdemo opensReplayday9; computed milestone endpoint/jump control uses actualfirstWT60/PFVI175/PFVI250 andnullifabsent, no presetdates; root160tests+37subtests PASS, webbuild+14milestoneassertions+cachetests PASS.
- B6a UAT plumbing: strongSID/TwiML sharedWarden, chosenconsent+explicitUAT, currentLIVEpolicy/harddailycap, durableSMSoutbox+quietguard and truthfulfollowup, protectedUI+UATwrappers; root162tests+37subtests PASS, webbuild/formatterchecks PASS; no provider calls, tunnel stopped.
