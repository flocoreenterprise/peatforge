# Peatforge

Autonomous fire command prototype for Indonesian peatlands. Built from scratch on 7 November 2026.

Build in progress: numerical engine, cached public data, channel foundation and responsive web shell are implemented.
The integrated SMS decision loop is still being built. See BUILDLOG.md and FEATURE_PARITY.md for actual evidence.
Read `AGENTS.md` and `CONTRACT.md` before implementation.

- `engine/` — groundwater, PFVI, intervention simulation and replay (Lead).
- `agents/` — tools, SMS/voice, Warden/Scout/Planner/Verifier and auth (CS #1).
- `web/` — operator Command and responder pages (CS #2).
- `scripts/set-secrets.ps1` — interactive masked entry; no secrets in chat or Git.

From PowerShell:

```powershell
Set-Location C:\Users\fadli\Desktop\peatforge
powershell -ExecutionPolicy Bypass -File .\scripts\set-secrets.ps1
```

Select just the initial auth fields with `-Name DEMO_PIN,SESSION_SECRET` when invoking
the script directly in PowerShell (`& .\scripts\set-secrets.ps1 -Name DEMO_PIN,SESSION_SECRET`).
Blank input preserves the current setting. Enter `!clear` to remove a setting.
Provider credentials may remain empty. The operator opens without PIN per the user's instruction;
public environmental data are independent from authentication, while external messaging remains mocked.
No current credential is displayed. See CONTRACT.md for validation and required groups.

The historical documents are planning inputs, not evidence that this fresh build passed any tests.
The new implementation will record tests, manual cloud QA and risks per task.

Agent file format checked against the official documentation:
https://learn.chatgpt.com/docs/agent-configuration/subagents
