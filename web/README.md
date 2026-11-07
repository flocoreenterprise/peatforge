# Command and responder web — owner: CS #2

Read ../AGENTS.md and ../CONTRACT.md. Edit web/ only. Vite + React + TypeScript + MapLibre.
Source prompts: read-only prep/01-codex-prompts.md section C; prep/05 section 4.
No product code at kickoff.

| Prompt | Task |
| --- | --- |
| C1 | Responsive shell, PIN login, relative API and explicitly labelled mocks |
| C2 | Pilot risk map, Live/Replay; optional 3D |
| C3 | Decision inbox, approval, verifier items and five-step progress |
| C4 | Village summary, Days Bought comparison and detailed assumptions |
| C5 | Trace and transcript; English subtitles only in Command |
| C6 | Secret responder page, QR handoff and optional simulated voice |
| C7 | Optional national view |
| C8 | Optional QA event list (extra in updated pack) |

Schedule C1 -> C2 -> C3 -> C4 -> C5 -> C6; C7/C8 only after must-haves.
Light theme default, night mode; one summary sentence and three key numbers.
Command polling 4 s, responder polling 3 s. 401 means login, never mock fallback.
Run npm build after each task. Manual cloud/tunnel QA at 1440/1024/390, then Terra review of screenshots.
Return files changed, test commands with pass/fail, open risks. Orchestrator commits after passing checks.

## Run

`npm.cmd install`, then `npm.cmd run dev`. The development server proxies relative `/api` requests to port 8000 and accepts Cloudflare quick-tunnel hosts. `npm.cmd run build` produces `dist/` for FastAPI static serving.

C1 starts directly from `/api/config` for public demo mode. A protected backend's 401/403 shows the six-digit PIN screen. An unavailable or missing config endpoint enables a clearly labelled read-only shell preview; it never invents operational readings or sends messages. Public demo displays “Demo terbuka · kanal simulasi”; environmental provenance will be labelled per result, independently of channel simulation. The shell polls village metadata every four seconds when connected. Risk layers, decisions, detailed village data, trace and responder flows are subsequent C tasks.

