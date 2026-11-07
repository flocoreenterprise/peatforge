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
