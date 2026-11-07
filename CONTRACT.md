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
- `source`: fixture|modelled|measured; `stale`, `data_as_of`, `assumptions` accompany computed results.
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
  In this explicit public demo, operator dependency permits access, engine uses fixtures, outbound adapters are mocked,
  and storage is a separate fixture-only database. Return public_demo:true and render a visible simulation badge.
  Never expose real responder contacts, secrets or provider-backed actions in public demo even if credentials exist.
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
  Missing signature accepted only with valid signed sid; fail closed without token. Sid `<rid>-<nonce8hex><hmac8hex>`;
  HMAC-SHA256(TWILIO_AUTH_TOKEN, `sid:<rid>:<nonce>`) first 8 hex per requested B6a; short MAC is demo-only risk to report.
- No full phone numbers or raw secrets in logs/HTTP errors/trace. Encrypt contacts, hash with salt, display last four only.
- All credentials entered through scripts/set-secrets.ps1. Public UI never gets provider keys.
- Outbound channels in mock mode unless configured. Fixture simulation is explicitly labelled; 401 never activates mocks.
- LLMs only rewrite wording. Reject changed numeric tokens, URLs or excessive length; offline templates are default/fallback.
- Quiet hours 21:00-05:00 WIB except EKSTREM; voice TINGGI+ only; max one call/responder/day except EKSTREM;
  hard 30 s call cooldown applies always. Field missions require human approval.
- `hour_override` and level override are replay/mock demo only. They cannot bypass live channel policy.

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
| GET /api/responders | O | village_id? | {"items":[Responder]} |
| GET /api/responders/{id}/field-link | O | none | {"field_path":"/f/<opaque-token>"} (never log) |
| GET /api/responders/{id}/messages | O | after? | {"items":[Message]} |
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
| POST /api/voice/twilio/call | O | {"responder_id":"r1","diagnostic":false} | {"call_id":"<provider-id>","status":"queued"} |
| POST /api/voice/twilio/twiml/start | S | sid query + Twilio form | application/xml Response: Pause then greeting inside Gather |
| POST /api/voice/twilio/twiml/gather | S | sid query + SpeechResult form | application/xml readback/Gather/Hangup |
| POST /api/voice/twilio/status | S | sid query + CallStatus/CallDuration | {"accepted":true} |
| WS /api/voice/stream | S | signed sid + Twilio media frames | bidirectional audio; optional B6b |
| GET /api/national/hotspots | O | days=1..7 | {"items":[Hotspot],"by_province":{},"snapshot_id":"s1"} (optional) |
| GET /api/national/risk | O | none | GeoJSON coarse risk cells (optional) |
| GET /api/national/summary | O | none | {"headline":"...","snapshot_id":"s1","by_province":[]} (optional) |
| GET /api/fire-events | O | relevant=true | {"items":[],"snapshot_id":"s1","unavailable_layers":[]} (optional A7/C8) |
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
| PUBLIC_DEMO | true/false; blank/unset defaults true per user; true = open fixture-only demo, no real channels |
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

Must-haves: deterministic groundwater/PFVI, honest Days Bought, full alert -> AIR -> readback -> YA -> proposal -> approve -> follow-up,
PIN auth, replay. Then B6a. Optional: national/QA, 3D, Realtime, Telegram, backtest, expanded trace/glossary/anonymity.
Auditor after blocks, security after boundaries, manual cloud/tunnel screenshots at 1440/1024/390 after web screens.
Missing cloud access is an open risk, never a passed manual QA claim. Persist tests/QA results in each task report.
No live external communication during build until provider setup and the chosen test recipient are confirmed.
