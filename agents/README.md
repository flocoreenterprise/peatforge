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
