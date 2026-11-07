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
Engine reads, internal tools, and the minimal demo/operator routes are mounted.
No `.env` file is loaded and no provider SDK or network call is used.

`create_app(db_path=temporary_path, mock=True)` injects isolated state. Runtime defaults
to `runtime/agents/demo.sqlite3` in public demo and `runtime/agents/state.sqlite3`
in protected mode; `PEATFORGE_DB_PATH` overrides it with the same isolation checks.
`PUBLIC_DEMO` defaults true when blank/unset; false selects protected scope.
Public demo forces mock outbound; environmental inputs are independent.
Fresh custom paths receive a scope marker; cross-scope reuse and unmarked populated
demo databases are rejected before fixture seeding.
The app now uses one actual `EngineService` instance for tools and engine routes.
`PEATFORGE_DATA_SOURCE` defaults `live` (public providers/cache); `fixture` explicitly
selects synthetic inputs for tests. Missing provider data remains unavailable.
The legacy ToolService MockEngine remains only for isolated foundational tests.
`PEATFORGE_ENGINE_MODE` and the legacy create_app mock argument no longer select
application scientific inputs; use data_source or an injected engine instead.
`seed_mock` is explicit and has no phone numbers, consent or field tokens.

`ToolService` exposes status, forecast, simulation, canals, resources, hotspots,
landmark resolution, confirmed observation storage and mock notification. Observation
storage retains the model snapshot; adaptive recalibration is cut and verification
arrives in B5. `OfflineBrain` is default; `safe_rewrite` only accepts wording preserving
numeric tokens and URLs within the message limit. No OpenAI calls are implemented.

## B2 channels

`ChannelAdapter.send` supports SMS and web; both use the same message store.
`inbound(Inbound(...))` is the common entry point for future field/Warden handling.
Inbound SMS is stored with `status=received` for the next pipeline block, deduplicated
by gateway message ID. Unknown senders are acknowledged without creating contacts.
Telegram is deferred. Real sends require a consented encrypted contact and configured
gateway; provider failures produce failed messages, never mock success.

`POST /api/channels/sms/inbound?key=...` requires `SMS_WEBHOOK_SECRET` (at least 32
characters), including in public demo. Configured `SMS_SIGNING_KEY` additionally
requires HMAC-SHA256(raw body + X-Timestamp), within five minutes. Query credentials
are removed from request scope before server access logging. Reverse proxies must
also omit query strings from their access logs. Request bodies and provider responses
are never logged or returned. Public demo accepts only synthetic sender
`+620000000001` into fixture responder `r1`; other senders are ignored and all outbound
messages remain mock even when gateway credentials exist.

CLI from root: `.venv/Scripts/python.exe -m agents.cli check`, `send <responder> <text>`,
`bind <responder> --consent` (phone entered through a hidden prompt), or
`register-webhook <HTTPS-callback-without-query>`. Real CLI actions require
`PUBLIC_DEMO=false`; `check` in public demo reports mock without accessing a provider.
Credentials are read only from the process environment; no `.env` loading occurs.
Fernet contact encryption requires `CONTACT_ENCRYPTION_KEY`; phone lookup uses
HMAC-SHA256 with `PHONE_HASH_SALT`. Contacts expose last four digits only to callers
that deliberately query that safe field; no HTTP contact endpoints exist yet.

`SMS_GATEWAY_URL` includes `/3rdparty/v1` for cloud mode, or a local origin for local
mode. Sending uses cloud `/messages` or local `/message`, Basic auth, and the provider
textMessage/phoneNumbers shape. Registration uses `/webhooks`; `check` uses `/health`.
Provider API reference: https://docs.sms-gate.app/features/webhooks/ and
https://api.sms-gate.app/docs/index.html . No provider action has been tested live.

Validation: `.venv/Scripts/python.exe -m unittest agents.test_foundation agents.test_channels -v`.
Tunnel lifecycle scripts remain orchestrator-owned outside this folder.

## B3 Warden and core integration

`POST /api/demo/inbound` accepts responder_id/text/mode/day in public demo only.
It goes through the SAME ChannelAdapter and Warden pipeline as authenticated inbound
SMS. Reports persist pending state in SQLite, read back facts, and save only on
standalone YA/IYA. Embedded ASAP YA and unrelated phrases cannot confirm. New reports
replace pending facts; TIDAK, implausible values, and new alerts clear them. Arabic
numbers, basic Bahasa number words and cm/m units are parsed deterministically.
Replay context is retained across confirmation, even if the browser changes context.
Confirmed observations retain the pre-update model snapshot; there is no adaptation.
Inbound receipts also retain their original normalized payload/context and a durable
completion flag. Failed processing resumes on the same provider ID; only completed
receipts are skipped. Confirmation checkpoints retain a stable observation ID, so a
failed post-confirm hook resumes without inserting a second observation. B5 hooks
must themselves be idempotent by observation ID. Transient channel/data failures
return 503 rather than a malformed-input response. The app runs as one process;
in-process locks serialize duplicate callback handling.

`GET /api/config`, `/api/responders`, `/api/responders/{id}/messages` and engine
routes share an operator dependency. Public demo opens them; protected operation
fails closed with 401 until PIN support is implemented. Contact ciphertext, hashes,
tokens, full phone numbers and provider credentials are never selected for responses.
Five synthetic responders correspond to the pilot villages, without real contacts.
The minimal `/api/events/risk-changed` route is a manual MOCK alert trigger. It uses
actual engine facts, resets pending context and never changes model levels to match
an optional demo override. Full B4 scheduling and phone calls are not implemented.

Full anonymity is cut: ANONIM gets an explicit unavailable message and is not saved
as an anonymous report. No unsupported anonymity promise is made. B5 will attach
Planner/Verifier to Warden.after_confirm and complete the proposal approval loop.

Validation: `.venv/Scripts/python.exe -m unittest agents.test_foundation agents.test_channels agents.test_warden -v`.

## B5 complete mock loop

Warden.after_confirm invokes an observation-ID-idempotent Planner. The Verifier
retains the pre-update discrepancy; over 30cm produces a visible `kind=verify`
dispatch-check item. Hotspot checks use 2km/48h with the same mode/day; unavailable
replay detections produce a flag, never a check against live detections.
Planner simulates selected canal blocking and pump standby with deterministic tools,
using a clearly labelled 60cm scenario raise. It retains positive hydraulic benefit
even when Days Bought is null or zero, drops zero benefit, and inserts at most two
ranked proposals. No adaptive update or accuracy claim is performed.

Action insertion and the pipeline completion receipt share one transaction. Proposed
items expire after six hours; newer same-village/type proposals supersede proposed
predecessors. Approval changes status to approved and atomically stores exactly one
mock follow-up for the original responder. It never marks field execution. Exact,
no-days and lower-bound variants preserve tool facts. Rejection and optional manual
completion are persisted. Protected approval delivery is unavailable until auth and
an approved real delivery flow exist.

Routes: GET `/api/actions?village_id=&include_old=false` -> items/hidden_count;
POST `/api/actions/{id}/approve` with `{}`; reject with `{reason}`; complete with
`{note}`. GET `/api/observations` returns items with id, confirmed input, pre-update
model and created_at. GET `/api/demo/progress?responder_id=r1` returns responder_id,
state, persisted mode/day, observation_id, action_id, counts (messages/observations/
actions) and steps (`{id,label,done}` for alert/report/proposal/approved/followup).
Progress reads persisted facts instead of parsing message text.

The app serves only web/dist assets and an HTML SPA fallback; `/api` never falls
back to HTML. Paths outside dist, dotfiles, source files, and missing assets are 404.
JavaScript/CSS MIME types are explicit. No tunnel is opened by this code.

Validation: `.venv/Scripts/python.exe -m unittest agents.test_foundation agents.test_channels agents.test_warden agents.test_planner -v`.
The cache integration check disables network, uses existing actual provider data and
temporary agent state, and verifies the current Talio replay day9 scenario returns
one modelled day of delay. It skips explicitly if the provider cache is absent.
