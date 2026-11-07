# Peatforge build instructions

## Shared context — Prompt 0

You are helping build Peatforge during a 7-hour hackathon. Everything is written today from scratch.
Product: an autonomous fire command system for Indonesian peatlands.
- Forge Engine (Python): groundwater depth (WT) + Peat Fire Vulnerability Index (PFVI 0-300) per village,
  canal-blocking simulation ("Days Bought").
- Agents (OpenAI Agents SDK, but the app MUST also run with an offline template brain and no API keys):
  Scout (who to contact), Warden (talks to responders by SMS/voice in natural Bahasa Indonesia),
  Planner (proposes actions), Verifier (checks evidence).
- Peatforge Command (React + MapLibre): operator console, decision inbox with human approval.
- Responders use SMS, phone calls, or a secret web link. No app install.
Hard rules:
- LLMs never compute numbers. Every number comes from an engine tool. LLM output that changes any digit/URL is rejected.
- Field actions (L3) need human approval. Autonomy limits (quiet hours 21-05 WIB except EKSTREM, max 1 call/day) are enforced in code.
- Hotspots are heat detections, not confirmed fires. Modelled water table is always labelled "modelled".
- Never log or return secrets. Fail closed when a secret is unset. Keep code small, typed, tested.
- Only edit the folder I name. Read CONTRACT.md for shared types and endpoints.

## Sources and ownership

- Planning documents are read-only: `C:/Users/fadli/Desktop/peatforge-hackathon/prep/00-README.md`,
  `01-codex-prompts.md`, `05-lessons-from-rehearsal.md`, `06-voice-telepon.md`,
  `07-orkestrasi-model.md`, `08-kickoff-jam-7.md`; plus the parent research brief and blueprint section 07.
- Never open, search, import, execute, or copy from `rehearsal/`. Never copy existing product code from elsewhere on disk.
- Write all product code from scratch in this repository. Planning text and requested agent configuration are allowed inputs.
- Read CONTRACT.md first. The orchestrator owns root files, contract, integration and scripts.
- Builders own only their assigned folder: Lead `engine/`, CS #1 `agents/`, CS #2 `web/`.
- One prompt from the pack per task. Never run two writers in the same folder at once.
- No changes to another owner's files; report needed changes to the orchestrator.
- Latest user override: skip login now, show the operator openly. PUBLIC_DEMO defaults true; mock outbound only.
  Auth, environmental data source and channels are independent. Integrate real no-key public weather/hotspots/history and caches;
  label provider/cache/model/fixture accurately. Preserve PIN auth for protected mode (PUBLIC_DEMO=false).
  Public demo must not access real contacts or send live SMS/calls. Reference application may be inspected visually if a URL is available;
  its source and rehearsal remain off-limits. Match the planning pack's flows/capabilities before refinement.

## Execution and review

- Phase 1 contains scaffolding and configuration only. User has now said "langsung go": proceed to Phase 2 after kickoff commit.
- Phase 2: orchestrator plans, delegates and judges; subagents write product code.
- Builders: `gpt-6.1-sol`, medium. Auditor: `gpt-6-luna`, high, after every block.
- Security: `gpt-6.1-sol`, high, after auth, webhooks and phone. UI: `gpt-6.1-terra`, medium, after each screen.
- Respect the runtime concurrency limit as well as the user's maximum of four builders; schedule reviewers when a slot is free.
- Every subagent returns files changed, tests run with pass/fail, and open risks.
- Orchestrator merges/commits one task only after its relevant tests pass. Do not commit another writer's partial work.
- Mock engine/tools until real APIs exist. Mock data must be labelled; never fall back to mock on HTTP 401.
- Command polls every 4 seconds; responder pages every 3 seconds. SSE is optional.
- Manual visual QA uses the running cloud/tunnel result, at widths 1440, 1024 and 390. Capture screenshots for UI reviewer.
  Never claim cloud QA passed from local-only checks. Unit tests/build checks from the prompts still apply.
- Every hour report five lines: done, in progress, blocked, next, what to cut if late.
- Feature freeze is 13:00 Asia/Jakarta on 2026-11-07; record actual start time at Phase 2, and never silently move the freeze.
- Keep must-haves in order: groundwater+PFVI, Days Bought, full SMS loop, PIN auth, Replay Jun-Aug 2019, B6a, extras.
- Integrate by elapsed hour 3, phone UAT at hours 4-5, freeze and video/pitch in the final hour.
- Cut order: 3D/terrain, national+QA, Realtime B6b, Telegram, historical backtest, detailed trace, glossary/ask bar, full anonymous feature.
  Remove unsupported anonymity claims if that feature is cut. Never cut must-haves.

## Secret and numeric boundaries

- User enters configuration only through `scripts/set-secrets.ps1`; never ask for keys in chat or print/log them.
- Do not inspect `.env` contents or expose field tokens, full phone numbers, PINs, auth cookies or webhook URLs with secrets.
- `.env.example` has names with empty values only. No generated or printed fallback PIN; missing auth configuration fails closed in protected mode.
- Network adapters remain mocked until configured. No live SMS/call merely to test without the user's chosen recipient.
- Tests use temporary state; never mutate demo state. Arithmetic, thresholds, dates and benefits come from deterministic code/tools.
- Distinguish measured observation, model estimate, fixture, cached data and historical perfect-forecast replay.
- Contract examples are illustrative fixtures, never measurements or promised scientific outcomes.

## Team lead course correction — latest, overrides older schedule/scope above

- Official start 09:31 WIB, 2026-11-07. Feature freeze stays 13:00; confirm with user only if organizers' deadline differs.
- Commit finished tasks individually with actual test commands/results in commit messages.
- Must-have demo by 12:00, gated in this order: (a) A3 PFVI -> A4 Days Bought/Replay -> A5 API on real cached data,
  labelled modelled; (b) B3 Warden -> B5 Planner/Verifier -> complete mock-delivery SMS loop;
  (c) C2 map -> C3 approve/reject inbox -> C4 summary-first village detail, polling 4 seconds.
- Do not start other work before those gates pass. Existing foundations remain; do not restart.
- 12:00-12:30 integration on one server + one tunnel. Real SMS/B6a only if full loop is green and credentials/receiver are ready.
- CUT NOW: A6 adaptive, A7 QA, national/C7, 3D, Telegram, B6b Realtime, trace drawer, ask bar.
- Auditor after each block, max 5 minutes. Security review once before opening the integrated tunnel.
- Reference visuals only from screenshots supplied by user. Do not browse the old example application or open rehearsal source.
- Every 30 minutes report five lines: done / in progress / blocked / next / cut.

## Speed mode — latest user override until 12:00
- Three Sol low builders own disjoint web/src/map, web/src/inbox, web/src/village files; root owns App.tsx/api.ts/shared wiring.
- B5 + C2 by11:00; C3 + C4 by11:30; integrated SMS UI by12:00. Engine finished; no new engine work.
- Runtime allows three active subagents plus root; root finishes B5 while three web builders run.
- Builders check only owned modules; root full suite/build at merge. No docs-only commits; BUILDLOG one line/task.
- Luna audits11:30 and12:15, max5min each; Terra screenshots once12:15; security once before tunnel opens.
- Report blockers only until12:00. Existing cuts, secret boundaries and13:00 freeze remain.

## Extension block authorized after core gate at10:50
- Latest user explicitly reopens B4 Scout/autonomy, A6 adaptive, C5 trace and B8 PIN/scoped links; C7 light province hotspot counts if slot frees.
- Targets: B4+A6 by11:30, C5 by11:45, B8 by12:00. One agents owner runs B4 then B8; engine and trace run parallel.
- Three active subagents maximum plus root (runtime limit). Sol low/medium. Root owns wiring and full merge checks.
- Stop visual polishing. One Luna audit and one Terra1440/1024/390 pass at12:15; fixP0/P1only. This supersedes earlier11:30audit slot.
- At12:30 report readiness for realSMS/phone UAT; user enterskeys onlythroughset-secrets.ps1. Freeze13:00.
- A6 replay still skipsrecalibration perprompt; keep pre-update discrepancy. Singleobservation assimilation is not independent field calibration.
- A7/3D/Telegram/Realtime/askbar remaincut. C7limitedcounts only, no nationwide peat-risk claims.

## QA gate and browser voice — latest team-lead override
- Five independent QA fixes committed in order: Scout demo banner, current WT criticality versus PFVI, honest hydraulic assumptions, input-based simulation cache, computed replay jumps/default.
- No real SMS/calls/provider requests, tunnels, secret changes or push while unattended. Existing tunnel stopped; continue local verification until attended UAT.
- B6 browser OpenAI Realtime is now explicitly authorized after those five commits, Sol medium, agents/ and web/src/voice/ ownership; root wires shared components.
- User will enter OPENAI_API_KEY via set-secrets.ps1; do not ask to paste it or create/reuse a key. Offline tests only until user begins live UAT.
- Defaults OPENAI_REALTIME_MODEL=gpt-realtime-2.1 and OPENAI_REALTIME_VOICE=marin. Ephemeral browser secret only; never expose/log the master key.
- Operator auth or responder field-token scope; tools force identity and reuse SMS parser/readback/plausibility/Verifier. Five session requests/minute/token.
- Browser voice is user-initiated; no autonomous real browser/provider calls. SMS/Twilio remain governed separately by protected live-channel gates.
- At12:45 if browser voice is not green keep it behind clearly labelled Simulasi fallback. Feature freeze13:00 unchanged.

## Attended UAT override
- User authorized live Realtime mint and two chosen Telkomsel calls (diagnostic then Warden) on 2026-11-07; no XL numbers. Explicit CLI-only UAT pair is capped at two/day; ordinary one/day, quiet hours and cooldown remain.
- User then revoked the PIN UI requirement: open fixture demo remains public. scripts/start.ps1 -Uat exposes only three signed Twilio callback POST routes from a separate protected app/store; no protected operator endpoint is exposed.
- User authorized generation of missing server secrets, including PIN, solely through set-secrets.ps1; never print them. Existing nonempty secrets are preserved.
- Tunnel stays open until user says UAT done. No automated background dispatch during attended UAT; SMS is mock without gateway settings.
- After live UAT only: FIRMS2019 archive if configured and OSM canal geometry, each must pass by12:50 or roadmap. No blocked-site scraping/accounts. Measured SIPALAGA WT is the first pilot calibration roadmap step. Freeze13:00.

## Organizer deadline update — latest override
- Submission16:30WIB; featurefreeze15:45 supersedes all13:00freeze instructions above.
- Order after current persona/layout block: extendReplay2019June15..September30 with cachedERA5/FIRMS5km, deterministic per-village model-warning/hotspot/lead-time and DaysBought, no tuning; then browserRealtime attendedUAT, Terra1440/1024/390P0/P1only. No furtherTwiMLcallunlessuserasks.
- At15:30 fullsuite andcleancommit; tunnelclosedunlessuserasks. Recordfive-line statusinBUILDLOG every30min. After15:45 backupvideo/pitch.
- LayoutuserC+A decisionconsole: alwaysvisibledecisionqueue, separate map/contextcolumns, data-firstsummary, plainBahasa, sourcescollapsed; no APIchangesforthatblock.

## Live command center priority — latest override
- Current layout first; then P0-A calm emergency-dispatcher Warden (no religious greeting, clear AI identity/no unsupportedBPBDaffiliation, consent,3observationalquestions, LOWqualitativeevidence, strictreadback/bye, fireURGENT safetyline).
- P0-B public/lapor +QR, anonymousoptional, samepipeline, reporterfollowup, hardserver180svoicecap plusglobal/daily/perIPlimits; reviewsecurityBEFOREtunnel.
- P0-C per-panel source/freshness and/status, FIRMS15minrefresh. OnlyafterP0green: P1-D arbitrarylocation/BMKGsearch withuncalibratedlabels andconsentedvolunteers/trialverifiedphones; P1-E extendedReplaySep30/leadtime andofflinecachefallback iftime.
- Solperfolder, Lunaaftereachblockmax5min, Terra1440/1024/390;15:30fullsuitecleancommit,15:45freeze,16:30submission. TunnelclosedunlessrequestedforattendedpublicUAT; no furtherTwiMLcallunlessasked.

## Hard deadline override — 13:14 WIB
- Feature freeze14:00 supersedes15:45. Commitlayoutnow afterbuild; nofurtherpolishing/visualrechecks.
- 13:15–13:30 P0-A dispatcherwording/tests;13:30–13:50minimalpublic/lapor+QR+hardcostguards/samequeue;13:50freshnesslabels onlyiftime, no/status.
- 13:55fullsuite+cleancommit;14:00five-linedone/cutreport. After14:00fixP0only.
- CUT:anywhere/volunteers,ReplaySeptember,offlinemode,OSMretries. Tunnelremainsclosedunlessrequested; noadditionalcalls.

## Repository handoff and phone exception — latest user override
- origin/main is public at github.com/flocoreenterprise/peatforge. After each green commit, git push origin main. No AI attribution, co-author trailers, or tool marks in commits/files.
- After14:00 do not touch web/; frontend partner owns shell rework on frontend-rework against CONTRACT.md. Maintain exact endpoint request/response examples for handoff.
- Dedicated parallel B6b builder owns new phone relay files; this explicitly permits disjoint writers in agents/. Other features freeze14:00, B6b only may finish14:15 then stop/fallback if not green.
- One additional attended UAT call to configured consenting Telkomsel recipient explicitly authorized after relay green, VOICE_ENGINE=realtime, tunnel allowed for this UAT. No other call/SMS authorization. Local open operator remains isolated from signed public callback/stream ingress.

## Attended stream repair and fire-event QA — latest override
- User authorized one more attended Realtime phone call after plain stream URL/custom Parameter and audible fallback repair; finish this UAT before A7 work. No further calls without explicit request.
- Then reimplement A7 from scratch in engine/, classified events/API and minimal web labels/badges only; no layout changes. This explicitly reopens the requested web labels despite earlier freeze. New block must be green by15:00 then freeze. Never open rehearsal. Sol builders, Luna five-minute audit, tests, commit and push; CONTRACT exact examples required.
- Fire classifications are model/rule-based candidates, not confirmed fires. Unavailable vegetation/peat/OSM evidence stays uncertain; never invent land cover or seasonal evidence.
