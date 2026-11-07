# Agents and channels — owner: CS #1

Read ../AGENTS.md and ../CONTRACT.md. Edit agents/ only. Python/FastAPI; no product code at kickoff.
Source prompts: read-only prep/01-codex-prompts.md section B and prep/06-voice-telepon.md.

| Prompt | Task |
| --- | --- |
| B1 | Typed tools, mock engine, SQLite storage, offline template brain |
| B2 | SMS gateway and common inbound/outbound adapter; optional Telegram |
| B3 | Warden state machine, readback, units, confirmation and anonymity |
| B4 | Scout, risk-change events and autonomy policy enforced in code |
| B5 | Planner, Verifier, proposals, expiry and follow-up |
| B6 | Optional browser voice session and offline simulation |
| B6a | Twilio TwiML phone, diagnostic mode, authenticated callbacks |
| B6b | Optional Realtime bridge after SMS loop passes |
| B7 | Shared SMS/speech renderer |
| B8 | PIN cookie, field tokens, rate limits and static web serving |

Schedule: B1 -> B2 -> B7; B3 -> B4 -> B8; B5 -> B6a; B6/B6b only as time permits.
One writer owns this folder at a time. Mock providers until configured through the secrets script.
Auditor after each block; security review after webhooks, auth and phone.
Return files changed, test commands with pass/fail, open risks. Orchestrator commits after passing checks.

## B1 foundation

From repository root: `.venv/Scripts/python.exe -m unittest agents.test_foundation -v`.
Run service: `.venv/Scripts/python.exe -m uvicorn agents.app:app --port 8000`.
Only health is mounted at this stage; tools are internal server functions.
No `.env` file is loaded and no provider SDK or network call is used.

`create_app(db_path=temporary_path, mock=True)` injects isolated state. Runtime defaults
to `runtime/agents/demo.sqlite3` in public demo and `runtime/agents/state.sqlite3`
in protected mode; `PEATFORGE_DB_PATH` overrides it with the same isolation checks.
`PUBLIC_DEMO` defaults true when blank/unset; false selects protected scope.
Public demo currently forces MockEngine and mock outbound even if real mode is
requested. Fresh custom paths receive a scope marker; cross-scope reuse and
unmarked populated demo databases are rejected before fixture seeding.
Engine defaults to synthetic fixtures. `PEATFORGE_ENGINE_MODE=real` lazily imports
`engine.service`; failures propagate without fixture fallback. Real signature integration
must be checked when engine is ready. Fixture simulation intentionally returns no Days Bought.
`seed_mock` is explicit and has no phone numbers, consent or field tokens.

`ToolService` exposes status, forecast, simulation, canals, resources, hotspots,
landmark resolution, confirmed observation storage and mock notification. Observation
storage retains the model snapshot; recalibration and the verification pipeline arrive
in B3/B5. `OfflineBrain` is default; `safe_rewrite` only accepts wording preserving
numeric tokens and URLs within the message limit. No OpenAI calls are implemented.
