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
