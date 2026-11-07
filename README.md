# Peatforge

Indonesian peatland command prototype built from scratch on 7 November 2026.
Five BMKG pilot village reference points, cached public environmental inputs, deterministic groundwater/PFVI,
Days Bought simulation, Replay 2019, and a complete human-approved SMS workflow with mocked delivery.

## Run

```powershell
Set-Location C:\Users\fadli\Desktop\peatforge
& .\scripts\start.ps1 -Install -Build
```

Open http://127.0.0.1:8000. In a second terminal, `& .\scripts\start-tunnel.ps1` prints a temporary HTTPS URL.
The same FastAPI server serves the React build and API. Keep server/tunnel running during the demo.
No credentials are required for the current open operator demo. Do not run the secrets script just to start it.
If credentials are needed later, use only `scripts/set-secrets.ps1`; never paste secrets into chat or Git.
Protected mode currently fails closed; PIN login and real SMS/phone setup are deferred by user instruction.

## Demo

1. Select Talio, Replay 2019, day9 (24 June). Village detail shows actual cached weather model output.
2. Open Telepon-SMS. Trigger the model alert; send `AIR 61 ASAP YA dekat kanal 4`.
3. Read Warden's readback, then press standalone YA. Embedded ASAP YA does not confirm the report.
4. Open Keputusan. Inspect the proposal, press Setujui then Konfirmasi.
5. Return to SMS: five persisted progress steps and the conditional follow-up appear. Approval is not field execution.
6. Generate another report to demonstrate rejection. Every Command panel polls every4seconds.

The current Talio day9 scenario returns1day from the engine. This is a reproducible example, not a promised
fire-prevention outcome. Other dates legitimately return zero, unavailable exact days or a lower bound.

## Boundaries

- WT/PFVI/benefits are modelled, not sensor measurements or validated fire probabilities.
- Replay uses known historical ERA5 weather as a perfect forecast; it is not out-of-sample validation.
- BMKG points are reference coordinates, not official boundaries. Canals/resources/responders are labelled fixtures.
- NASA hotspots are satellite heat detections, not confirmed fires. Unavailable archive hotspots remain unavailable.
- Open demo storage is isolated from real contacts; every external delivery is mocked. No real SMS/call was sent.
- A6 adaptation, A7 hotspot QA, national view,3D,Telegram,Realtime,trace drawer and ask bar are cut.
- Before real SMS activation, offload synchronous webhook processing to preserve prompt acknowledgements.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest engine/tests agents --ignore=agents/.venv -q
Set-Location web
npm.cmd run build
node src/inbox/benefit.test.mjs
```

Latest root suite:96passed +15subtests, webbuild PASS, formatter7assertions PASS.
Manual cloud test passed the complete mocked SMS loop including1day proposal and one follow-up.
Security found noP0/P1 within public-demo scope. Scheduled audits and final screenshots remain separate checks.
Read AGENTS.md and CONTRACT.md before edits; contract includes planned routes, while OpenAPI lists implemented routes.
