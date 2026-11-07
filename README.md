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
Protected mode now requires the configured PIN and session secret. Public demo remains the default.
Real SMS/phone delivery requires a separate explicit flag and recipient setup; it has not been tested live.

## Scheduled SMS/phone UAT

Only enter configuration through `scripts/set-secrets.ps1 -Name ...`. For protected UAT, set
`PUBLIC_DEMO=false`, `PEATFORGE_LIVE_CHANNELS=true`, a six-digit `DEMO_PIN`, `SESSION_SECRET`,
`CONTACT_ENCRYPTION_KEY`, `PHONE_HASH_SALT`, and the consenting Telkomsel receiver as `UAT_PHONE_NUMBER`.
The secret setup accepts `!generate` for the three local cryptographic keys, without printing them.
Phone needs `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, and `PUBLIC_BASE_URL`
set to the active HTTPS tunnel origin. TwiML uses no OpenAI key.

SMS uses a separate gateway: `SMS_GATEWAY_URL`, `SMS_GATEWAY_USER`, `SMS_GATEWAY_PASS`,
`SMS_WEBHOOK_SECRET` (use `!generate`), and optionally `SMS_SIGNING_KEY` if the gateway signs callbacks.
The gateway must be reachable from this server. Its credentials are not Twilio voice credentials.

After the chosen receiver consents and configuration is stored:

```powershell
& .\scripts\uat.ps1 -Action Prepare -Consent
& .\scripts\uat.ps1 -Action Check
# Restart scripts/start.ps1 to apply the stored configuration to the server.
# Only when beginning the agreed physical UAT:
& .\scripts\uat.ps1 -Action RegisterSms
& .\scripts\uat.ps1 -Action Sms -Text 'Uji coba Peatforge. Balas AIR dalam cm dan ASAP YA atau TIDAK dekat kanal 4.'
& .\scripts\uat.ps1 -Action Call
```

`Check` only checks voice configuration/contact readiness; it does not contact a provider or prove delivery.
`Prepare` stores the consenting contact locally. `RegisterSms`, `Sms` and `Call` have real external effects
when protected live channels are configured; do not run them as routine build checks.
The normal Indonesian Warden UAT explicitly bypasses the risk threshold for the chosen receiver only;
quiet hours, cooldown and the hard one-call-per-WIB-day cap remain. A diagnostic call also consumes that cap.
Provider acceptance means queued, not answered or delivered. Failed/unknown sends are never automatically retried.
For SMS, confirm the readback with a separate YA, approve the resulting proposal, and verify the follow-up
on the physical receiver. For voice, report depth, smoke and landmark, then confirm the readback separately.

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
- Scout enforces quiet hours and a hard one-call-per-day limit. Confirmed live WT can update a bounded initial condition; replay remains unchanged.
- Proposal traces and national heat counts are available. Province boundaries are the labelled 2017/34-province set.
- A7 hotspot QA,3D,Telegram and ask bar remain cut.
- SMS processing is offloaded from the async server loop. Live provider delivery still needs the scheduled UAT.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest engine/tests agents --ignore=agents/.venv -q
Set-Location web
npm.cmd run build
node src/inbox/benefit.test.mjs
```

Latest root suite:173 passed +37 subtests; web build PASS; benefit/delivery/field-link and QA presentation/cache/milestone checks PASS.
Manual cloud test passed the complete mocked SMS loop including1day proposal and one follow-up.
Security review found a quiet-hours follow-up gap; it was fixed and regression-tested. Live Twilio policy now uses current live risk even when the conversation is a replay. Provider and microphone UAT remain untested.
Read AGENTS.md and CONTRACT.md before edits; contract includes planned routes, while OpenAPI lists implemented routes.

## Browser Warden

Open Telepon-SMS and select Telepon Warden (or use the scoped responder page). Browser voice uses an ephemeral credential, gpt-realtime-2.1 and marin by default. Allow the microphone only when starting a call. Final transcripts pass the server guard before buffered audio plays; report confirmation requires the validated readback followed by a new YA. Provider or browser errors retain the labelled Simulasi text flow. Numeric paraphrases may fail closed. Live session mint and real microphone quality remain separate UAT checks.

## Attended UAT with an open demo

`./scripts/start.ps1 -Uat` serves the public fixture demo and only the three signed Twilio callbacks from an isolated protected store. CLI UAT commands use the protected store. This keeps real contacts and actions out of the open operator UI. `uat.ps1 -Action Diagnostic -UatPair` then `-Action Call -UatPair` implements the specifically authorized two-call test; it retains quiet hours/cooldown and blocks a third attempt. `uat.ps1 -Action Status` reads provider status and redacted transcripts. Keep the tunnel until the operator ends UAT. The launcher loads the latest set-secrets .env over stale inherited environment values.
