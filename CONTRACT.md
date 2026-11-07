# Peatforge contract v1 — 7 November 2026

Owner: orchestrator. Read this before editing. All examples below are synthetic schema examples, not measured results.
Latest user instructions > this reconciled contract > updated prompts 01/05/06/07/08 > older blueprint/research notes.
Changes require orchestrator agreement. Phase 2 has been authorized by the user's "langsung go".

## Runtime and shared types

- Python FastAPI, single server on 8000: engine functions imported by agents application. SQLite owned by agents/.
- Web Vite React TypeScript on 5173, `/api` proxy to 8000; production serves web/dist from FastAPI.
- All HTTP endpoints below start `/api`. No Supabase dependency in the hackathon build.
- ISO dates; timestamps UTC ISO8601, displayed Asia/Jakarta. `day` is zero-based replay offset.
- Depth `wt_cm` positive down from peat surface (cm); higher depth means drier. Hydraulic heights are metres internally.
- PFVI finite numeric 0..300. Levels: RENDAH <100, SEDANG 100..<175, TINGGI 175..<250, EKSTREM >=250.
- `channel`: sms|voice|telegram|web. `mode`: live|replay. `action.kind`: proposal|verify.
- `action.status`: proposed|approved|rejected|done|expired|superseded. Approved is not executed/done.
- `autonomy`: L0 observe, L1 inform, L2 engage registered opt-in responder, L3 field action requiring operator approval.
- `action.type`: canal_block|pump_standby|dispatch_verify. `verdict`: forward|forward_with_flag|hold.
- Errors: `{"error":{"code":"unavailable","message":"Data belum tersedia"}}` with appropriate 400/401/403/404/409/422/429/503.
- JSON never contains NaN/Infinity. Unavailable numeric values are null with a reason, not a fabricated zero.
- Standard list response `{items:[]}`. Geo endpoints return GeoJSON FeatureCollection where stated.
- `source`: fixture|modelled|measured for numerical data; administrative metadata may use provider with provider=BMKG.
  `stale`, `data_as_of`, `assumptions` accompany computed results.
- Every live Command view polls at 4 s, field view at 3 s; optional SSE never replaces polling.

## Engine and scientific decisions

- Open-Meteo rain/ET0/temperature, soil moisture model, ERA5 archive. BMKG supplementary village weather.
- Planning choice for live hotspots: NOAA-20/21 regional seven-day CSV; no FIRMS key needed for live. Verify availability when implementing.
  Archive FIRMS needs FIRMS_MAP_KEY and available historical sensors; do not fabricate archival coverage.
- Five pilot villages in Pulang Pisau, source/coordinates labelled fixtures until verified. Do not fabricate official boundaries.
- Groundwater: 1D Boussinesq, N=51, automatic stable substeps, mass balance diagnostic, explicit bucket fallback.
- Parameter values carry provenance; effective thickness tuned to a benchmark is labelled team assumption.
- PFVI deterministic equations, previous-day rain rule, constrained calibration (n >=1.05), two starts.
  Mineral-soil moisture bounds 15/45 are a proxy assumption; prior targets PFVI 175 at 40 cm and 250 at 60 cm are not independent validation.
- Critical crossing: PFVI >=250 OR wt_cm >=60. Horizon defaults 14 days.
- Days Bought: scenario crossing day minus baseline crossing day. If baseline already critical return 0 + benefit text;
  if baseline never critical return null + reason and horizon-end wt_gain_cm/pfvi_reduction.
  If scenario never crosses, return null + `scenario_beyond_horizon` and explicit `at_least_days` lower bound, never invent a crossing date.
- Replay dates 2019-06-15..2019-08-15 inclusive. ERA5 is historical perfect-forecast input, not an out-of-sample prediction.
- Confidence 0.5 modelled and 0.7 after a plausible confirmed observation are heuristic labels, not calibrated probabilities.
- Keep pre-update model snapshot for discrepancy checks. Observations beyond 30 cm disagreement create a verification item;
  never let recalibration erase evidence of disagreement. Replay skips recalibration and traces that fact.
- Tests isolate runtime state. Don't tune an engine solely to force a positive Days Bought demo.
- National risk is optional coarse modelling, not comparable to village pilot; missing layer != safe.

## Auth, secrets and external effects

- Latest user override: show operator openly now; no secrets setup yet. PUBLIC_DEMO=true is the default for blank/unset values.
  In this explicit public demo, operator dependency permits access, outbound adapters are mocked,
  and storage is a separate demo database without real contacts. Auth, data source and channels are independent.
  Integrate public environmental provider data without keys and cache it; numerical engine must run over those inputs when available.
  Fixtures are explicit fallback/selected demo input, not a consequence of skipping PIN. Return public_demo:true;
  render 'Demo terbuka · kanal simulasi' and per-source provider/cache/model/fixture provenance.
  Never expose real responder contacts, secrets or real outbound actions in public demo even if credentials exist.
  PUBLIC_DEMO=false enables protected operation and the following PIN requirements.
- Every operator route has a shared auth dependency, including config, trace, simulate and outbound calls.
- PIN six digits, HttpOnly HMAC cookie 12 hours, SameSite strict/lax, Secure on HTTPS; 5 login attempts/min/IP.
- Fail closed when DEMO_PIN or SESSION_SECRET missing. No printed fallback PIN and no public auth bypass.
- Only trust CF-Connecting-IP when traffic is from a configured trusted tunnel/proxy; otherwise use socket peer. Never trust arbitrary X-Forwarded-For.
- Field token: token_urlsafe(32), 43 chars, scope forced to its responder; unknown token 404. 30 messages/min/token; text <=500 chars.
- SMS webhook requires key=SMS_WEBHOOK_SECRET; if SMS_SIGNING_KEY set require X-Signature HMAC-SHA256(raw_body + X-Timestamp), +/-5 min.
  Do not log query strings/tokens. Dedupe messageId; return 2xx quickly. Unset webhook secret rejects.
- Telegram optional: required X-Telegram-Bot-Api-Secret-Token. Unconfigured rejects.
- Twilio checks X-Twilio-Signature against full PUBLIC_BASE_URL request URL + sorted form params. Wrong signature always 403.
  Missing signature accepted only with a valid signed sid; fail closed without token. Sid uses version, encoded responder,
  issued time, 128-bit nonce, diagnostic flag and full HMAC-SHA256, with a 15-minute lifetime. Legacy short MACs are rejected.
- No full phone numbers or raw secrets in logs/HTTP errors/trace. Encrypt contacts, hash with salt, display last four only.
- All credentials entered through scripts/set-secrets.ps1. Public UI never gets provider keys.
- Outbound channels in mock mode unless configured. Fixture simulation is explicitly labelled; 401 never activates mocks.
- LLMs only rewrite wording. Reject changed numeric tokens, URLs or excessive length; offline templates are default/fallback.
- Quiet hours 21:00-05:00 WIB except EKSTREM; voice TINGGI+ only; hard max one call/responder/WIB day including EKSTREM and diagnostic;
  hard 30 s call cooldown applies always. Field missions require human approval.
- `hour_override` and level override are replay/mock demo only. They cannot bypass live channel policy.
- Explicit `uat:true` permits a normal Warden test at low model risk only for the chosen consenting protected recipient.
  It does not bypass quiet hours, cooldown or the hard daily call cap; diagnostic also consumes that cap.
- Delivery status is independent of action approval. An ambiguous provider result is never automatically retried.
  `Action.followup_status` and message status report mock/queued/failed/unknown/suppressed rather than claiming delivery.

## Canonical REST endpoints

`O` = operator cookie; `F` = scoped field token; `S` = signed webhook; `P` = public (no sensitive data).
GET requests have no JSON body. `mode=live|replay&day=N` is supported on engine reads; defaults live/day=0.
Example objects referenced below are defined next. Routes marked optional are cut before must-haves.

| Method and route | Auth | Request/query | Response example/schema |
| --- | --- | --- | --- |
| GET /api/health | P | none | {"status":"ok"} |
| POST /api/auth/login | P | {"pin":"<masked input>"} | {"authenticated":true}; cookie |
| POST /api/auth/logout | P | none | {"authenticated":false}; clears cookie |
| GET /api/auth/session | P | cookie | {"authenticated":false} |
| GET /api/config | O | none | {"mode":"mock","public_demo":true,"poll_ms":4000,"field_poll_ms":3000,"voice_available":false} |
| GET /api/villages | O | none | {"items":[Village]} |
| GET /api/villages/{id}/status | O | mode,day | Status |
| GET /api/villages/{id}/forecast | O | days=14,mode,day | {"items":[ForecastPoint]} |
| POST /api/villages/{id}/simulate | O | SimulationRequest | Simulation |
| GET /api/villages/{id}/canals | O | none | {"items":[Canal]} |
| GET /api/villages/{id}/resources | O | none | {"pumps":1,"crews":1,"canal_blocks":[{"id":"K4","status":"open"}]} |
| GET /api/villages/{id}/params/history | O | none | {"items":[{"version":1,"params":{},"observation_id":null,"created_at":"2019-06-15T00:00:00Z"}]} |
| GET /api/map/risk | O | date?,mode,day | GeoJSON FeatureCollection with Status properties |
| GET /api/hotspots | O | hours=72,village_id?,radius_km=5,mode,day | {"items":[Hotspot],"available":true,"stale":false,"data_as_of":"2019-06-15T00:00:00Z"} |
| GET /api/replay/meta | O | none | {"scenario":"2019","start":"2019-06-15","end":"2019-08-15","days":62,"perfect_forecast":true} |
| GET /api/replay/day/{day} | O | none | {"mode":"replay","day":0,"date":"2019-06-15","items":[Status]} |
| GET /api/replay/{scenario}/day/{day} | O | scenario=2019 | alias of preceding route for blueprint compatibility |
| GET /api/backtest | O | village_id | {"available":false,"reason":"FIRMS_MAP_KEY unset","hits":null,"misses":null} (optional) |
| POST /api/warmup | O | {} | {"ready":true,"villages":5} |
| POST /api/observations | O | confirmed ObservationInput | {"observation_id":"o1","old":{},"new":{},"version":2,"recalibrated":true} |
| GET /api/observations | O | village_id? | {"items":[{"id":"o1","village_id":"62.11.01.2002","wt_cm":61,"confirmed":true,"pre_update_model":{},"created_at":"2026-11-07T03:00:00Z"}]} |
| GET /api/responders | O | village_id? | {"items":[Responder]} |
| GET /api/responders/{id}/field-link | O | none | {"field_path":"/f/<opaque-token>"} (never log) |
| GET /api/responders/{id}/messages | O | after? | {"items":[Message]} |
| POST /api/demo/inbound | O, public_demo only | {"responder_id":"r1","text":"AIR 61 ASAP YA dekat kanal 4","mode":"replay","day":9} | {"items":[Message],"state":"confirm"}; SAME inbound pipeline as SMS, no real send |
| GET /api/demo/progress | O | responder_id=r1 | {"responder_id":"r1","state":"saved","mode":"replay","day":9,"observation_id":"o1","action_id":"a1","steps":[{"id":"alert","label":"Peringatan","done":true}],"counts":{"messages":7,"observations":1,"actions":1}}; all5 steps emitted from persisted facts |
| POST /api/responders/{id}/send | O | {"text":"Pesan","channel":"sms"} | {"message_id":"m1","status":"mock"} |
| POST /api/events/risk-changed | O | {"village_id":"62.11.01.2002","level_to":"TINGGI","mode":"replay","day":5,"hour_override":10} | {"trace_id":"t1","notified":1,"suppressed":0} |
| GET /api/policy | O | none | {"max_calls_per_day":1,"quiet_hours":[21,5],"timezone":"Asia/Jakarta","default_channel":"sms","l3_requires_approval":true} |
| GET /api/actions | O | village_id?,include_old=false | {"items":[Action],"hidden_count":0} |
| POST /api/actions/{id}/approve | O | {} | Action; sends follow-up once |
| POST /api/actions/{id}/reject | O | {"reason":"Perlu bukti"} | Action |
| POST /api/actions/{id}/complete | O | {"note":"Pelaksanaan dikonfirmasi"} | Action status done |
| GET /api/trace | O | trace_id?,after? | {"items":[AgentEvent]} |
| POST /api/channels/sms/inbound | S | GatewayEvent | {"accepted":true,"duplicate":false} |
| POST /api/channels/telegram/inbound | S | Telegram update | {"accepted":true} (optional) |
| GET /api/field/{token}/messages | F | after? | {"items":[Message],"responder":{"name":"Responder demo","village_id":"62.11.01.2002"}} |
| POST /api/field/{token}/send | F | {"text":"AIR 61 ASAP YA dekat kanal 4","anonymous":false} | {"items":[Message],"state":"confirm"} |
| GET /api/field/{token}/stream | F | optional SSE | event: message; data: Message |
| POST /api/field/{token}/voice-session | F | {} | 503 offline; otherwise ephemeral client secret, never long-lived key (optional B6) |
| POST /api/field/{token}/tools | F | {"name":"get_village_status","arguments":{}} | Status; responder forced from token, allowlisted tools only (optional B6) |
| POST /api/voice/session | O | {"responder_id":"r1"} | 503 offline or scoped ephemeral session (optional blueprint alias) |
| POST /api/voice/twilio/call | O | {"responder_id":"r1","diagnostic":false,"uat":false} | {"call_id":"<opaque-internal-id>","status":"queued"}; mock/suppressed/unknown/failed also possible |
| POST /api/voice/twilio/twiml/start | S | sid query + Twilio form | application/xml Response: Pause then greeting inside Gather |
| POST /api/voice/twilio/twiml/gather | S | sid query + SpeechResult form | application/xml readback/Gather/Hangup |
| POST /api/voice/twilio/status | S | sid query + CallStatus/CallDuration | {"accepted":true} |
| WS /api/voice/stream | S | signed sid + Twilio media frames | bidirectional audio; optional B6b |
| GET /api/national/hotspots | O | days=1..7 (default1) | items:[], points_omitted:true, raw by_province plus by_province_events and event totals; implemented |
| GET /api/national/risk | O | none | GeoJSON coarse risk cells (optional) |
| GET /api/national/summary | O | none | {"headline":"...","snapshot_id":"s1","by_province":[]} (optional) |
| GET /api/fire-events | O | days=1..7 (default7), class, province, since | FireEventSnapshot; implemented A7, exact examples below |
| GET /api/national/overview | O | days=1..7 (default1) | headline_sentence + headline counts + per-province event counts; implemented A7 |
| POST /api/fire-events/{id}/feedback | O | {"confirmed":false} | {"accepted":true} (optional) |

Browser routes `/`, `/f/{token}`; static assets public, data authorized separately.
Login page can be rendered at `/`; deep links use SPA fallback excluding `/api`.

## JSON shared examples

```json
{
  "Village": {"id":"62.11.01.2002","name":"Talio","code":"TAL","lat":-3.0,"lon":114.1,"source":"fixture"},
  "Status": {"village_id":"62.11.01.2002","level":"TINGGI","pfvi":190,"wt_cm":45,"days_to_critical":5,"critical_date":"2019-06-20","confidence":0.5,"data_as_of":"2019-06-15T00:00:00Z","wt_source":"modelled","source":"fixture","stale":false,"mode":"replay","day":0,"assumptions":["Perkiraan model, belum diukur","ERA5 digunakan sebagai prakiraan sempurna historis"]},
  "ForecastPoint": {"date":"2019-06-15","pfvi":190,"level":"TINGGI","wt_cm":45,"rain_mm":0},
  "SimulationRequest": {"type":"canal_block","targets":["K4"],"delta_hc_cm":60,"days":14,"mode":"replay","day":0},
  "Simulation": {"baseline_critical_date":"2019-06-20","scenario_critical_date":"2019-06-24","days_bought":4,"at_least_days":null,"reason":null,"wt_gain_cm":10,"pfvi_reduction":25,"curve_baseline":[{"date":"2019-06-15","pfvi":190,"wt_cm":45}],"curve_scenario":[{"date":"2019-06-15","pfvi":190,"wt_cm":45}],"assumptions":["Contoh sintetis, bukan hasil model terverifikasi"]},
  "Hotspot": {"id":"h1","lat":-3.0,"lon":114.1,"detected_at":"2019-06-15T00:00:00Z","confidence":"nominal","frp":10,"source":"fixture"},
  "Canal": {"id":"K4","name":"Kanal empat","block_id":"K4","block_status":"open","spacing_m":400,"source":"fixture"},
  "Responder": {"id":"r1","village_id":"62.11.01.2002","name":"Responder demo","role":"MPA","tier":1,"phone_last4":null,"consent_at":null,"last_contacted_at":null},
  "Message": {"id":"m1","responder_id":"r1","direction":"outbound","channel":"web","body":"Mohon konfirmasi laporan Anda.","speech":"Mohon konfirmasi laporan Anda.","created_at":"2019-06-15T00:00:00Z","status":"mock","trace_id":"t1"},
  "ObservationInput": {"village_id":"62.11.01.2002","responder_id":"r1","wt_cm":61,"smoke":true,"fire":null,"landmark_id":"K4","note":"dekat kanal empat","anonymous":false,"confirmed":true,"mode":"replay","day":5},
  "Action": {"id":"a1","village_id":"62.11.01.2002","kind":"proposal","type":"canal_block","status":"proposed","title":"Tutup sekat kanal empat","reason":"Laporan lapangan terkonfirmasi","observation_id":"o1","responder_id":"r1","anonymous":false,"days_bought":4,"benefit":{"wt_gain_cm":10,"pfvi_reduction":25},"verifier":{"decision":"forward_with_flag","checks":[]},"created_at":"2019-06-15T00:00:00Z","expires_at":"2019-06-15T06:00:00Z","trace_id":"t1","approved_by":null},
  "AgentEvent": {"id":"e1","agent":"Warden","event":"readback","input":{},"output":{},"tool_calls":[],"trace_id":"t1","created_at":"2019-06-15T00:00:00Z"},
  "GatewayEvent": {"event":"sms:received","payload":{"messageId":"demo-event-1","sender":"<E.164 private input>","message":"AIR 61 ASAP YA"}}
}
```

## Tools and database

Tools from blueprint section 07 remain server functions; no second public API namespace:

| Tool | Input -> result |
| --- | --- |
| get_village_status | village_id,mode/day -> Status |
| get_forecast | village_id,days=14,mode/day -> ForecastPoint[] |
| get_hotspots | village_id,hours=48,radius_km=5 -> Hotspot[] + availability |
| get_canals | village_id -> Canal[] |
| simulate_intervention | village_id,{type,targets,delta_hc_cm?},days=14,mode/day -> Simulation |
| get_resources | village_id -> pumps,crews,canal_blocks |
| list_responders | village_id -> Responder[] |
| get_autonomy_policy | none -> policy |
| resolve_landmark | village_id,text -> landmark_id,name,lat,lon,match_score |
| send_message | responder_id,text,channel=sms -> message_id,status |
| place_call | responder_id,reason,level -> call_id,status |
| log_field_observation | village_id,confirmed ObservationInput -> observation + parameter change |
| report_fire | village_id,{landmark_id?,description,anonymous} -> pending readback, not automatic confirmation |
| draft_broadcast | village_id,audience=toa|wa -> text |
| propose_action | proposal -> action_id,status=proposed |
| notify_followup | observation_id or report_id,action_id -> message_id |
| record_report / confirm_report / end_call | voice wrappers to SAME Warden pipeline, no divergent logic |

Engine exposes in `engine/service.py`: `village_status`, `forecast`, `simulate`, `map_risk`,
`replay_meta`, `replay_day`, `params_history`, `recalibrate`, `backtest`, `warmup`, `list_villages`, `get_canals`, `get_hotspots`, `get_resources`.
`engine/api.py` exports APIRouter with paths relative to `/api`; main agents app mounts it with operator auth.
Return Python dicts/lists shaped above. Engine fixtures and real source paths share interface.

SQLite core: villages, landmarks, forecasts, responders (encrypted phone, hash,last4,consent_at,field_token),
messages (body,speech,status,trace_id), observations (anonymous,confirmed,pre_update_model),
actions (kind,status,benefit,approved_by,expires_at), agent_events, kv. Unique inbound message ID prevents duplicates.
Engine owns parameter/cache storage under runtime/engine, agents under runtime/agents; ignore both in Git.

Warden: idle -> confirm -> saved. Save only after standalone confirmation, not "matiin ya" or embedded ASAP YA.
New report replaces pending; alert resets pending; unrelated text gets a neutral reminder. Parse cm/m, clarify implausible >150 cm.
After confirmed report: retain pre-update comparison -> recalibrate eligible live observations -> Verifier -> Planner simulation ->
verify proposal -> insert proposed/verify action. Max two proposals, supersede same village+type; expire after 6 h.
Approvals idempotent; follow-up once, says proposed model benefit (never claims field execution or safety).
Anonymous mapping retained privately for follow-up; mask operator responses and traces, not merely UI.

## Environment inventory (all .env.example values empty)

| Name | When needed / validation |
| --- | --- |
| PUBLIC_DEMO | true/false; blank/unset defaults true per user; true = open operator, no real contacts/channels; environmental sources independent |
| PEATFORGE_DB_PATH | Optional SQLite path; guard database scope so public demo never reuses real-contact storage |
| PEATFORGE_ENGINE_MODE | mock/real; mock until service exists, then real numerical engine (independent from auth) |
| PEATFORGE_DATA_SOURCE | live/fixture; live = public no-key providers+cache; fixture must be labelled explicitly |
| DEMO_PIN | Required for operator login; exactly 6 digits; masked |
| SESSION_SECRET | Required auth HMAC; >=32 characters; masked |
| CONTACT_ENCRYPTION_KEY | Required before binding real phone; Fernet 32-byte URL-safe base64 key |
| PHONE_HASH_SALT | Required before real contacts; >=32 characters |
| OPENAI_API_KEY | Optional wording/Realtime; sk- prefix; no key -> offline templates |
| OPENAI_REALTIME_MODEL | Optional B6b model identifier; planned default gpt-realtime-2.1, verify account availability before API work |
| OPENAI_REALTIME_VOICE | Optional B6b voice identifier; planned default marin |
| VOICE_ENGINE | auto, twiml, realtime; blank -> twiml until Realtime verified |
| TWILIO_ACCOUNT_SID | B6a; AC + 32 hex |
| TWILIO_AUTH_TOKEN | B6a; 32 hex |
| TWILIO_FROM_NUMBER | B6a; E.164 |
| PUBLIC_BASE_URL | HTTPS cloud/tunnel origin, no credentials/query/fragment |
| FIRMS_MAP_KEY | Optional historical archive/backtest; alphanumeric/hyphen/underscore |
| SMS_GATEWAY_URL | Real SMS; http/https base URL, no embedded credentials/query/fragment |
| SMS_GATEWAY_USER | Real SMS; nonempty |
| SMS_GATEWAY_PASS | Real SMS; masked |
| SMS_WEBHOOK_SECRET | Real SMS inbound; >=32 characters |
| SMS_SIGNING_KEY | Real SMS inbound signature; >=32 characters |
| TELEGRAM_BOT_TOKEN | Optional; digits:token |
| TELEGRAM_WEBHOOK_SECRET | Optional; >=32 URL-safe characters |

Blank entry preserves current value; `!clear` removes it. Script masks all input, validates without echo,
updates only requested names in .env and prints `tersimpan` on success. Never dump .env.
Provider account readiness and delivery must be tested separately; shape validation does not verify credentials.

## Acceptance and cut plan

Latest lead gate overrides older optional plans: start09:31, core by12:00, integration12:00-12:30, freeze13:00.
Build only A3-A5 -> B3+B5/full mocked SMS loop -> C2-C4 now. A6/A7/national/C7/3D/Telegram/B6b/trace drawer/ask bar are CUT.
Core browser demo uses operator message timeline plus public-demo-only inbound simulator; no full C6 field app or B8 auth rollout now.
The shared access dependency allows publicdemo and fails closed for protected mode until PIN auth is implemented.
Observation submission records evidence; adaptive recalibration is deferred, with no claim of adaptation in UI/pitch.
Reference visuals only from user-supplied screenshots. Auditor max5min/block; integrated security before tunnel opening.

Must-haves: deterministic groundwater/PFVI, honest Days Bought, full alert -> AIR -> readback -> YA -> proposal -> approve -> follow-up,
PIN auth, replay. Then B6a. Optional: national/QA, 3D, Realtime, Telegram, backtest, expanded trace/glossary/anonymity.
Auditor after blocks, security after boundaries, manual cloud/tunnel screenshots at 1440/1024/390 after web screens.
Missing cloud access is an open risk, never a passed manual QA claim. Persist tests/QA results in each task report.
No live external communication during build until provider setup and the chosen test recipient are confirmed.

## Extension API agreement after core gate10:50
- POST /api/scout/tick {mode,day} -> {mode,day,items:[{village_id,level,previous_level,notified,suppressed,reason,trace_id?}],notified,suppressed,rewound}; operator auth, idempotent4s polling and background worker.
- GET /api/policy returns code-enforced quiet hours and voice limits. GET /api/trace returns existing redacted AgentEvent items.
- A6 EngineService.recalibrate(village_id,observation,observation_id) returns {old,new,version,recalibrated,reason}; confirmedliveWT only, bounded initial-condition assimilation, not K/Sy calibration. Replay skipped. Original evidence retained.
- Parameter history includes before/after and triggering observation. Engine state is separate per public/protected scope, in-memory bydefault in tests.
- B8 activates existing planned operator auth and scoped field routes. No random/printed fallbackPIN. Missingsecrets failclosed. Publicdemo remainsdefault.
- C5 trace enabled bylatestuser. C7limitedprovincecounts mayuse attributed public administrative polygons; no nationalrisk/A7classificationclaims.

B8 delivery configuration: PEATFORGE_LIVE_CHANNELS=true is an explicit separate opt-in; defaultfalse keeps channels mocked even in protectedmode. Publicdemo alwaysmock. UAT_PHONE_NUMBER is a masked E.164 chosen testrecipient, entered only throughset-secrets.ps1. LocalSESSION_SECRET/contactencryption/hash/webhook secrets may be created by entering !generate inside thatscript; never printed. PIN always userchosen, nofallback.
Latest Scout cap is hardonecall/responder/WIBday INCLUDINGEKSTREM; EKSTREM bypassesquiet hours only, neverdailycap/cooldown.
Uvicorn starts with --no-proxy-headers so request.client remains the socketpeer and spoofed forwardingheaders cannot bypassratelimits.

## Browser Warden Realtime — latest authorized B6

- Browser voice is explicitly initiated by the user, independent of SMS/Twilio live-channel flags. Public demo contacts remain fixtures and SMS/Twilio remain mocked.
- Default model is gpt-realtime-2.1; default voice marin. User configures OPENAI_API_KEY only through set-secrets.ps1. Missing key/provider errors return503; no master key or raw provider error is returned or logged.
- All browser voice endpoints accept an operator session (public-demo operator policy unchanged), or X-Field-Token. If a field header is present it takes precedence, forces responder/context from the stored token, and invalid tokens never fall through to operator access.
- POST /api/voice/session accepts {responder_id?,mode?,day?}; returns {session_id,client_secret,expires_at,session_expires_at,model,voice}. client_secret is only the short-lived provider ephemeral capability. Master API key stays server-side. Session creation is limited to5/min/token with additional bounded global/active limits.
- POST /api/voice/transcript accepts {session_id,event_id,role:user|assistant,text}; stores final scoped voice messages idempotently. Assistant text requires an explicit accepted:true ACK before display/playback; rejected numeric claims are not stored as validated statements.
- POST /api/voice/tool accepts {session_id,call_id,name,arguments:{}}. Names are get_village_status,record_report,confirm_report,end_call. Identity is server-bound; report/confirmation uses persisted user text, never model-supplied numerical fields.
- POST /api/voice/simulate accepts {responder_id?,mode?,day?,session_id?,text,event_id}; deterministic same-Warden voice conversation without provider access. POST /api/voice/end accepts {session_id} and closes the scoped local session idempotently.
- Browser responses are serialized with automatic provider response/interrupt generation disabled. Remote audio is buffered in memory and played only after matching transcript approval, completed response and drained output; rejected/unknown responses close the peer and show Simulasi. No audio file is persisted. A separate client call limit is not the ephemeral credential expiry.
- A report needs actual readback and a newer standalone confirmation. Bye words never mean yes. Field action proposals still need operator approval.
- Config and field-message responses expose browser_voice_supported as a route capability, not proof of provider readiness. Real microphone/voice quality requires attended UAT.

## Attended UAT isolation
- Latest user keeps demo UI open. The UAT server uses separate demo/protected stores and routes only exact POST Twilio start/gather/status callbacks to the protected service; all operator/demo endpoints use fixtures and mocked Twilio/SMS.
- Explicit CLI --uat-pair allows one diagnostic then one Warden call to the configured consenting recipient per WIB day; no counter reset, no third call, normal daily cap and cooldown remain. No HTTP field can opt in.
- cli_voice status reads only the chosen recipient provider calls and redacted local voice transcript; it does not initiate calls.
- SMS mock selection is independent of configured Twilio when gateway settings are absent; configured gateway failures never fall back to mock.

## Minimal public reporting — deadline14:00 override
- Local operator remains loopback8000 withoutPIN peruser. Publicingress loopback8001 is strictallowlist; tunnelONLY8001aftersecurityreviewanduserrequest. Sharedsingleapp lifespan/database/budgets. NooperatorAPI,fieldlinks,ephemeralvoicecredentials orTwilio callback exposedpublicly.
- GET/api/public/villages -> {items:[{id,name,lat,lon}]}; POST/api/public/sessions {village_id,consent:true} -> {token,items,state,voice_available}. TokenneverinURL/logs; frontendstores scopedcapabilitysessionStorage.
- GET/api/public/messages andPOST/api/public/messages {text,event_id} requireAuthorization:Bearer scopedtoken. Returnonlyownitems/actions/state. Reporterfollowup3spoll; operator4spoll. Serverforcesresponder/village/livecontext.
- Public reports source=public, label Laporan publik, LOW/unverified evenifvolunteerednumeric andcallerYA. NeverA6assimilation orautomaticfielddispatch. Sameparser/readback/Verifier/approval pipeline, noexternalSMS/calls.
- POST/api/public/voice/session and/end scoped; publicvoice unavailable503unlessserverowned180smediarelay andatomicglobal/perIP/dailybudgets areimplemented/tested. Neverfallthrough tooperatorWebRTCephemeralroute. Textfallbackmustremainusable.
- Proposed voicecaps:180s,global5/min,perIP2/min,concurrent3,total20/day. Unknownproxyidentitysharedconservativebucket; XForwardedFornevertrusted. Reservebeforeproviderstart, ambiguousattemptconsumesbudget, noautomaticretry.
- OptionalWS/api/public/voice/stream scopedcapabilityfirstframe, noquerytoken; serveracceptsonlyboundedPCM, ownstools/transcripts/config; closesupstreamdeadline/disconnect. Ifnotgreenby13:50keep503/textfallback, disclosecut.

## Public reporting frontend contract (implemented)
All examples below use synthetic IDs. No cookies are accepted instead of the scoped Bearer token. Public ingress exposes only /lapor, built assets and the exact public endpoints. Operator endpoints remain on localhost8000. The optional attended phone gateway adds only signed callbacks/stream, never operator access.

| Endpoint | Request | Response |
|---|---|---|
| GET /api/public/villages | none | {"items":[{"id":"62.11.01.2002","name":"Talio","lat":-3.1222040246,"lon":114.0981588862}]} |
| POST /api/public/sessions | {"village_id":"62.11.01.2002","consent":true} | PublicThread plus {"token":"<43-character capability>"} |
| GET /api/public/messages | Authorization: Bearer <capability> | PublicThread |
| POST /api/public/messages | same Authorization; {"text":"Parit sudah kering","event_id":"client-unique-id"} | PublicThread; repeated same event/text idempotent, changed text409 |
| POST /api/public/voice/session | same Authorization; {} | 503 {"error":{"code":"unavailable","message":"Layanan belum tersedia"}}; public voice is cut, no provider mint |
| POST /api/public/voice/end | same Authorization; {} | {"ended":true}; no active provider session |

PublicThread example:
```json
{"items":[{"id":"m_example","direction":"outbound","body":"Air di parit dekat kebun masih ada atau sudah kering?","status":"mock","created_at":"2026-11-07T06:30:00Z"}],"actions":[],"state":"collecting","stage":"wt","ended":false,"voice_available":false,"source":"public"}
```
After confirmed report, actions contain only reporter-scoped {id,title,status,priority,source,anonymous,verifier,approved_by,followup_status}; priority is URGENT or null, source public, anonymous true, verifier.confidence_label LOW. Status values match normal Action. Approval follow-up is a persisted web message; it does not mean an SMS was delivered or a crew dispatched. Command /api/actions retains the full Action shape with source public and optional priority. Never mix Live public actions into historical Replay calculations; switch to Live to review them.

Session expiry24h; text500characters; event_id128characters; max200newmessages/session. Creation caps:5/min global,2/min socket peer,20/day UTC buckets; messages30/min/token. Counters persist across restart. Unknown proxy clients share a conservative bucket; forwarded headers do not establish identity. Missing/invalid/expired token401, disabled public service404, unknown village/extra fields/missing consent422, duplicate event conflict409, rate cap429. Field identity, village, channel and live context are server-controlled; client extras cannot override them. Consent/readback confirms what was said, not field truth. Public reports always require human verification and never recalibrate the engine.

## Phone Realtime transport and attended UAT extension
- Config VOICE_ENGINE=twiml (default), realtime or auto. Configured key + realtime/auto uses GPT Realtime; otherwise normal TwiML. Default model gpt-realtime-2.1, voice marin. Keys never returned.
- Authenticated POST /api/voice/twilio/twiml/start still accepts Twilio form fields and signed sid query. When enabled returns Connect/Stream; fallback=1 keeps the same HMAC/signature validation and requests the old TwiML path. Terminal calls return Hangup.
- WS /api/voice/stream: plain URL with no query or credential path. Connect includes custom Parameter sid. The bounded initial start frame must supply its full HMAC capability before any OpenAI connection; invalid or missing capability closes1008. A present Twilio signature is also validated against the configured external origin. On start, streamSid/accountSid/callSid/mediaFormat are validated and callSid hash must match the persisted outgoing attempt. No unrelated call can use the stream.
- Wire Twilio connected/start/media/mark/stop JSON events, PCMU8000Hz mono; upstream GA Realtime format audio/pcmu (no rate field), semantic_vad, transcription and shared dispatcher tools. Server stores channel voice transcripts and validates assistant transcript before releasing buffered audio. Readback confirmation requires playback mark acknowledgment and a subsequent standalone YA.
- Three-minute server deadline plus Twilio TimeLimit180 for the Realtime call; never send Timeout. Pre-audio failure redirects once to signed TwiML; terminal timeout/goodbye never restarts a conversation. No new outbound call is created by fallback.
- One additional CLI-only --realtime-uat attempt is authorized after the earlier completed two-call pair. It consumes persistent call count3 and its own one-use receipt; chosen contact/consent, cooldown and quiet hours still apply. No HTTP body can activate this exception.
- GET /api/config now optionally includes public_reporting_enabled:true and public_report_url:"http://localhost:8001/lapor" (or explicitly enabled HTTPS tunnel origin). Frontend opens Live when public reporting is enabled. A localhost QR is labelled local and cannot be advertised as reachable from judges' phones.

## Supplemental implemented engine endpoints (operator only)
| Endpoint | Request/query | Response example / fields |
|---|---|---|
| GET /api/canals/osm | none; do not poll during this demo, provider retries are cut | {"type":"FeatureCollection","features":[],"available":false,"source":"OpenStreetMap via Overpass","data_as_of":null,"stale":false,"reason":"Current OSM providers unavailable; no canal geometry fabricated","spacing":{"available":false,"median_spacing_m":null,"model_spacing_m":400,"model_spacing_source":"team assumption"},"attribution":"© OpenStreetMap contributors, ODbL1.0","license":"https://www.openstreetmap.org/copyright"} |
| GET /api/replay/hotspots/summary | none | {"available":true,"source":"NASA FIRMS archive","sensor":"VIIRS S-NPP standard processing","stale":false,"data_as_of":"2026-11-07T05:00:00Z","partial":false,"total_unique":2,"retrieved_unique":2,"by_village":{"62.11.01.2002":1},"by_village_is_lower_bound":false,"date_coverage":{"requested_start":"2019-06-15","requested_end":"2019-08-15","timezone":"UTC FIRMS acquisition dates","complete":true,"successful_ranges":[],"unavailable_ranges":[]},"assumptions":[],"attribution":"NASA FIRMS VIIRS S-NPP archive","documentation":"https://firms.modaps.eosdis.nasa.gov/api/area/"} |
| GET /api/replay/milestones | village_id required | {"scenario":"2019","start":"2019-06-15","end":"2019-08-15","days":62,"perfect_forecast":true,"village_id":"62.11.01.2002","mode":"replay","source":"modelled","stale":false,"data_as_of":"2026-11-07T05:00:00Z","items":[{"kind":"critical","day":15,"date":"2019-06-30","wt_cm":60.6,"pfvi":61.3,"reason":null}],"assumptions":[]} |
Milestones always return four kinds: wt_60, pfvi_175, pfvi_250, critical. Missing crossing has day/date/wt_cm/pfvi null and reason not_reached_in_replay. Primary critical jump must use explicit critical item (WT>=60 OR PFVI>=250), not WT-only. Archive summary omits detection items; use /api/hotspots for display. In partial archives total_unique is null and per-village counts are lower bounds. Complete per-village maps include all five pilots; the shortened example above is illustrative, not a full response. Provider failures can return503; unavailable configuration can return available:false with reason. A zero count is only valid with successful available data.

## Endpoint availability notes for frontend handoff
- Currently served browser routes: /, /lapor, /f/{token}. /status was cut. Public ingress allows /lapor only; operator/field screens require local or separately authorized protected access.
- Optional rows in the original planning table (Telegram, field SSE/field voice aliases, fire-event feedback/national risk) are not a promise of an implemented route. The canonical browser voice API is /api/voice/session, /transcript, /tool, /simulate and /end with X-Field-Token for a scoped field caller or local operator authentication.
- Browser transcript success: {"accepted":true}; denied assistant speech must never play. Tool success includes server-derived text/stage/state/facts and optional ended/observation_id, depending on the tool. Confirm uses a new standalone caller reply after validated readback. /voice/end returns {"ended":true}. /voice/simulate returns {"session_id":"bv_example","items":[],"state":"collecting","stage":"wt","text":"Air di parit dekat kebun masih ada atau sudah kering?","ended":false,"mode":"live","day":0,"facts":{}}.
- Live FIRMS cache TTL900seconds; Command polling does not refetch upstream every4seconds. The data timestamp identifies the source/cache snapshot, while screen-sync time only identifies the last successful UI read.
- Public gateway request bodies are capped at8192bytes before JSON parsing, with10second input deadline (413/408). Public text caps remain500characters and no additional identity fields are accepted.
- Carrier TimeLimit180 remains the default for Realtime calls. A CLI-only server-limit-only override is prepared for attended UAT but requires the user's explicit acceptance of network-dependent carrier termination; it never weakens the server's180second audio/processing deadline. The independent callback watchdog survives WebSocket failure and oldTwiMLfallback, preserves the original deadline, and requests Status=completed. REST failure can delay carrier termination in legacy Gather; this is not a guaranteed carrier-side cap.
- A manual rejected-create retry is allowed at most once, only for a recorded definitive creation failure after the UAT reservation with no provider hash/callback/status/stream activity. Unknown/ambiguous requests and already-created calls are never retried. No endpoint exposes this CLI-only path.

- Stream repair UAT is a separate one-use CLI-only exception authorized after the silent completed Realtime attempt. It does not reopen normal autonomy or public dispatch. The accepted server-only180s limit still depends on Twilio REST for carrier termination during legacy fallback.

## A7 classified heat-event candidates — implemented

These are rule-based candidates, **never confirmed fires, factories or eruptions**. Operator routes retain the existing auth policy; the public reporting tunnel does not expose these APIs. No request body is required for these GET endpoints.

- `GET /api/fire-events?days=7&class=gunung_api&province=East%20Java&since=2026-11-01T00:00:00Z`
- `class` accepts exactly `gambut`, `lahan_vegetasi`, `industri`, `gunung_api`, `tidak_pasti`; omit for all. Province is an exact name from the existing 2017/34-province boundary data. Unknown province returns an empty selection. `since` requires a timezone and selects events with `last_seen >= since`, retaining their full members. `days` selects detections before clustering, while persistence uses available context up to seven days.

Illustrative filtered response (source timestamps/IDs/counts below are fixtures):
```json
{
  "available": true,
  "mode": "live",
  "days": 7,
  "source": "NASA FIRMS",
  "data_as_of": "2026-11-07T06:00:00Z",
  "stale": false,
  "partial": true,
  "snapshot_id": "fire-qa-v1-example",
  "rule_version": "fire-qa-v1",
  "raw_count": 2,
  "event_count": 1,
  "classification_counts": {"gambut":0,"lahan_vegetasi":0,"industri":0,"gunung_api":1,"tidak_pasti":0},
  "filters": {"class":"gunung_api","province":"East Java","since":"2026-11-01T00:00:00Z"},
  "since_semantics": "last_seen >= since; full event members retained",
  "evidence_coverage": {"peat_mask_available":false,"vegetation_available":false,"osm_industrial_available":false,"volcano_list_complete":false,"volcano_entries":7},
  "items": [{
    "id":"event-example","lat":-8.108,"lon":112.922,"location_method":"first_detection","province":"East Java",
    "first_seen":"2026-11-06T06:00:00Z","last_seen":"2026-11-07T06:00:00Z",
    "detection_count":2,"detection_ids":["heat-example-1","heat-example-2"],"max_frp":12.5,"satellites":["N20","N21"],
    "classification":"gunung_api","confidence":0.6,"confidence_label":"MED","confirmed":false,"persistence_days":2,
    "reasons":["Deteksi panas satelit belum membuktikan kebakaran.","Dalam 5 km gunung api Holosen Semeru; kandidat sumber panas vulkanik, bukan konfirmasi erupsi.","OSM industri tidak tersedia"],
    "sources":[{"name":"NASA FIRMS heat detections","url":"https://firms.modaps.eosdis.nasa.gov/"},{"name":"Smithsonian Global Volcanism Program: Semeru","url":"https://volcano.si.edu/volcano.cfm?vn=263300"}]
  }]
}
```
The response also carries `headline` counts defined below, `coverage`, `assumptions`, and inherited source/cache metadata. Filtered counts describe the returned events; snapshot ID identifies the underlying unfiltered day-window snapshot, and `filters` identifies the selection. IDs hash sorted detection IDs; snapshot hashes include the full rule parameters, volcano list, event contents and source time. A changed window can split/merge an event. Province and map location use the first detection; cross-border events are counted once, not once per province.

`GET /api/national/overview?days=1` has no `items`; it returns the same provenance/evidence metadata, `classification_counts`, `headline_sentence`, `headline`, and `by_province` as an array. Example count object, also used for each province:
```json
{
  "raw_count":8,"event_count":4,"land_event_count":0,
  "peat_event_count":null,"classified_peat_event_count":0,"vegetation_event_count":0,
  "excluded_industry_count":1,"excluded_volcano_count":1,"uncertain_event_count":2,
  "excluded_industry_detection_count":5,"excluded_volcano_detection_count":1,"excluded_detection_count":6
}
```
Example overview additions:
```json
{
  "headline_sentence":"0 kandidat kejadian lahan terklasifikasi; 2 kejadian belum diketahui. Data gambut belum tersedia. 1 kandidat industri dan 1 kandidat gunung api dipisahkan (klasifikasi heuristik, belum terkonfirmasi; daftar gunung api tidak lengkap).",
  "by_province":[{"province":"East Java","raw_count":1,"event_count":1,"land_event_count":0,"peat_event_count":null,"classified_peat_event_count":0,"vegetation_event_count":0,"excluded_industry_count":0,"excluded_volcano_count":1,"uncertain_event_count":0,"excluded_industry_detection_count":0,"excluded_volcano_detection_count":1,"excluded_detection_count":1}]
}
```
The example province array is shortened; the actual overview includes every mapped province. `land_event_count` counts only positively classified gambut/lahan_vegetasi events. It is **not an estimate of all actual land fires**. `peat_event_count:null` means no peat mask is available; `classified_peat_event_count:0` means no event was positively classified as peat. Never replace that null with zero.

`GET /api/national/hotspots?days=1` preserves the original raw `by_province:{"East Java":1,...}`, `total_matched`, regional/unmatched counts and coverage. It returns `items:[]`, `points_omitted:true`, and adds `raw_count`, `event_count`, `classification_counts`, `headline` (the count object), `by_province_events:{"East Java":{...count object...},...}`, `events_snapshot_id`, `rule_version`, `evidence_coverage`. Use `events_snapshot_id` to match the event snapshot; its older `snapshot_id` remains the raw-source snapshot. The unfiltered per-province map is omitted from filtered `/fire-events` results.

Rules are in `engine/fire_qa_params.yaml`: inclusive pair links1km/48h with transitive union-find, approximate375m persistence cell and at least5 distinct UTC days, volcano proximity5km. Confidence is an uncalibrated heuristic. Seven cited GVP volcanoes are an explicitly incomplete list. Current national service has no peat/vegetation mask and no industrial OSM cache; unknown land cover stays `tidak_pasti`, and every event records `OSM industri tidak tersedia`. No new Overpass request is issued. The classifier supports supplied positive evidence for future adapters but does not fabricate it from pilot village coordinates.

Invalid day/class/time returns422 `{"error":{"code":"invalid","message":"Permintaan tidak valid"}}`. Unavailable FIRMS returns503 `{"error":{"code":"unavailable","message":"Data belum tersedia"}}`, never an invented zero. `partial` and `stale` propagate source coverage/cache state. Live views poll4s; existing source/snapshot caches prevent provider fetches per poll. Low model risk alone does not establish rainy season; the Live summary says `Risiko rendah, pemantauan tetap berjalan.` until seasonal evidence exists.

## Realtime phone capability preflight
Before reserving call budget or creating a real non-diagnostic Realtime call, the server reads Twilio account type. Trial returns {"call_id":null,"status":"suppressed","reason":"twilio_trial_media_streams_unavailable"}; failed/unknown account lookup returns the same shape with twilio_account_capability_unavailable. Only Full proceeds to the unchanged consent/recipient/autonomy checks. TwiML-only and browser Realtime paths are independent. This lookup never exposes account credentials. Twilio documents Stream as blocked on trial: https://www.twilio.com/docs/usage/trials/try-out-voice#blocked-verbs . Upgrade alone does not authorize another call or reset consumed UAT receipts.
