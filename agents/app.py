"""Main service skeleton. Operator routers will mount behind shared auth in B8."""
from contextlib import asynccontextmanager, suppress
import asyncio
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import Field
from engine.api import create_router
from engine.cache import DataUnavailable
from engine.ingest import Ingest
from engine.service import EngineService
from engine.villages import list_villages
from .brain import OfflineBrain
from .channels import ChannelAdapter, ChannelSettings, ChannelUnavailable, Inbound, verify_webhook
from .db import Database, identifier
from .models import Level, Mode, Model, ObservationInput
from .tools import ToolService, seed_mock
from .warden import Warden, message_items
from .planner import Planner
from .scout import POLICY, Scout
from .events import list_events, record
from .auth import AuthSettings, COOKIE_NAME, OperatorAuth, SESSION_SECONDS, peer_ip, secure_cookie
from .field import FieldService
from .security import SecurityHeaders
from .voice import VoiceService,VoiceSettings
from .realtime import Access,BrowserVoice,Settings as BrowserSettings
from .scout import decision,sms_policy
from urllib.parse import parse_qs

RUNTIME_DIR = Path(__file__).resolve().parent.parent / 'runtime' / 'agents'


def public_demo_setting(value: str | None) -> bool:
    normalized = (value or '').strip().lower()
    if normalized in ('', 'true'):
        return True
    if normalized == 'false':
        return False
    raise ValueError('PUBLIC_DEMO must be true or false')


def _database_scope(database: Database, public_demo: bool) -> None:
    """Reject reuse across boundaries, including custom paths and legacy live state."""
    expected = 'public_demo' if public_demo else 'protected'
    with database.connect() as connection:
        row = connection.execute("SELECT value FROM kv WHERE key='database_scope'").fetchone()
        if row and json.loads(row['value']) != expected:
            raise ValueError('Database belongs to another runtime scope')
        if public_demo and not row:
            for table in ('villages', 'landmarks', 'forecasts', 'responders', 'messages', 'observations', 'actions', 'agent_events', 'kv'):
                if connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                    raise ValueError('Public demo requires a fresh or scoped fixture database')
        connection.execute("INSERT OR IGNORE INTO kv(key,value) VALUES('database_scope',?)", (json.dumps(expected),))


def require_operator(request: Request) -> None:
    request.app.state.auth.require(request)


class DemoInbound(Model):
    responder_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)
    mode: Mode | None = None
    day: int | None = Field(default=None, ge=0, le=61)


class DemoAlert(Model):
    village_id: str = Field(min_length=1, max_length=80)
    mode: Mode = 'live'
    day: int = Field(default=0, ge=0, le=61)
    level_to: Level | None = None
    hour_override: int | None = Field(default=None, ge=0, le=23)


class ActionNote(Model):
    reason: str = Field(default='', max_length=500)
    note: str = Field(default='', max_length=500)


class EmptyInput(Model):
    pass


class ScoutTick(Model):
    mode: Mode = 'live'
    day: int = Field(default=0,ge=0,le=61)


class FieldSend(Model):
    text: str = Field(min_length=1,max_length=500)
    anonymous: Literal[False] = False


class VoiceCall(Model):
    responder_id:str=Field(min_length=1,max_length=80)
    diagnostic:bool=False
    uat:bool=False


class OperatorSend(Model):
    text:str=Field(min_length=1,max_length=300)
    channel:Literal['sms','web']='sms'


class BrowserStart(Model):
    responder_id:str|None=Field(default=None,min_length=1,max_length=80)
    mode:Mode|None=None
    day:int|None=Field(default=None,ge=0,le=61)


class BrowserTranscript(Model):
    session_id:str=Field(min_length=1,max_length=80)
    event_id:str=Field(min_length=1,max_length=128)
    role:Literal['user','assistant']
    text:str=Field(min_length=1,max_length=500)


class BrowserTool(Model):
    session_id:str=Field(min_length=1,max_length=80)
    call_id:str=Field(min_length=1,max_length=128)
    name:str=Field(min_length=1,max_length=80)
    arguments:dict=Field(default_factory=dict)


class BrowserEnd(Model):
    session_id:str=Field(min_length=1,max_length=80)


class BrowserSimulate(BrowserStart):
    session_id:str|None=Field(default=None,min_length=1,max_length=80)
    event_id:str=Field(min_length=1,max_length=128)
    text:str=Field(min_length=1,max_length=500)


def create_app(*, db_path: str | Path | None = None, mock: bool | None = None,
               public_demo: bool | None = None, engine: EngineService | None = None,
               data_source: str | None = None, web_dist: Path | None = None,
               background_scout: bool | None = None) -> FastAPI:
    public_demo = public_demo_setting(os.environ.get('PUBLIC_DEMO')) if public_demo is None else public_demo
    source = data_source or os.environ.get('PEATFORGE_DATA_SOURCE', '').strip() or 'live'
    if source not in ('live', 'fixture'):
        raise ValueError('PEATFORGE_DATA_SOURCE must be live or fixture')
    default_path = RUNTIME_DIR / ('demo.sqlite3' if public_demo else 'state.sqlite3')
    configured_path = os.environ.get('PEATFORGE_DB_PATH', '').strip()
    path = Path(db_path if db_path is not None else configured_path or default_path).resolve()
    forbidden_path = (RUNTIME_DIR / ('state.sqlite3' if public_demo else 'demo.sqlite3')).resolve()
    if path == forbidden_path:
        raise ValueError('Database path crosses the public/protected boundary')
    # Explicit temporary DB injection must never read/mutate real demo adaptation.
    # Custom runtime DBs carry their own state sidecar; standard scopes stay separate.
    state_path=(None if db_path is not None else
                RUNTIME_DIR.parent/'engine'/('demo-adaptive.json' if public_demo else 'protected-adaptive.json')
                if path==default_path.resolve() else path.with_suffix('.adaptive.json'))
    injected_engine=engine is not None
    engine = engine if engine is not None else EngineService(Ingest() if source == 'fixture' else None,state_path=state_path)
    source = engine.ingest.mode
    background_enabled=(not injected_engine and db_path is None and path==default_path.resolve() and source=='live') if background_scout is None else background_scout

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(path)
        database.initialize()
        _database_scope(database, public_demo)
        if public_demo:
            seed_mock(database)
            with database.connect() as connection:
                for index, village in enumerate(list_villages(), 1):
                    connection.execute('INSERT OR IGNORE INTO responders(id,village_id,name) VALUES(?,?,?)',
                        (f'r{index}', village['id'], 'Responder demo ' + village['name']))
                    for canal in engine.get_canals(village['id']):
                        connection.execute('INSERT OR IGNORE INTO landmarks(id,village_id,name,aliases,source) VALUES(?,?,?,?,?)',
                            (canal['id'], village['id'], canal['name'], json.dumps(canal.get('aliases', [])), 'fixture'))
        app.state.database = database
        app.state.engine = engine
        app.state.tools = ToolService(database, mock=public_demo, engine=engine)
        app.state.channels = ChannelAdapter(database, ChannelSettings.from_environment(public_demo=public_demo))
        app.state.config['outbound']='mock' if app.state.channels.settings.force_mock else 'configured_only'
        app.state.tools.channels = app.state.channels
        app.state.warden = Warden(database, app.state.tools, app.state.channels)
        app.state.channels.on_inbound = app.state.warden.handle
        app.state.channels.prepare_inbound = app.state.warden.prepare
        app.state.planner = Planner(database, app.state.tools, app.state.channels)
        app.state.warden.after_confirm = app.state.planner.process
        app.state.scout=Scout(database,app.state.tools,app.state.channels,app.state.warden)
        app.state.warden.on_call=app.state.scout.place_call
        app.state.field=FieldService(database,app.state.channels)
        app.state.voice=VoiceService(database,app.state.tools,app.state.channels,app.state.warden,VoiceSettings.from_environment())
        app.state.browser_voice=BrowserVoice(database,app.state.tools,app.state.channels,app.state.voice,BrowserSettings.from_environment())
        app.state.channels.outbound_policy=lambda responder,channel:sms_policy(database,app.state.tools,responder,channel,
                                            datetime.fromtimestamp(app.state.voice.clock(),timezone.utc))
        app.state.brain = OfflineBrain()
        async def run_scout():
            while True:
                await asyncio.sleep(4)
                try:
                    await asyncio.to_thread(app.state.scout.tick,'live',0,background=True)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    record(database,'Scout','background_unavailable',trace_id=None,output={'reason':'unavailable'},event_id='e_scout_background_unavailable')
        task=asyncio.create_task(run_scout()) if background_enabled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title='Peatforge', lifespan=lifespan)
    app.add_middleware(SecurityHeaders)
    app.state.auth=OperatorAuth(AuthSettings.from_environment(),public_demo)
    app.state.public_demo = public_demo
    app.state.database_path = path
    app.state.engine=engine
    app.state.config = dict(mode='mock' if public_demo else 'protected', public_demo=public_demo,
                            source=source, engine='deterministic', outbound='mock',
                            poll_ms=4000, field_poll_ms=3000, voice_available=False,scout_background=background_enabled)
    app.state.config['browser_voice_supported']=True

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValueError)
    @app.exception_handler(TypeError)
    @app.exception_handler(RecursionError)
    async def invalid_handler(request, exception):
        return JSONResponse({'error': {'code': 'invalid', 'message': 'Permintaan tidak valid'}}, status_code=422)

    @app.exception_handler(DataUnavailable)
    @app.exception_handler(ChannelUnavailable)
    async def unavailable_handler(request, exception):
        return JSONResponse({'error': {'code': 'unavailable', 'message': 'Data belum tersedia'}}, status_code=503)

    @app.exception_handler(KeyError)
    async def missing_handler(request, exception):
        return JSONResponse({'error': {'code': 'not_found', 'message': 'Data tidak ditemukan'}}, status_code=404)

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def auth_handler(request, exception):
        if exception.status_code==405 and request.url.path.startswith('/api/field/'):
            return JSONResponse({'error':{'code':'not_found','message':'Data tidak ditemukan'}},status_code=404)
        code = {401: 'unauthorized', 403: 'forbidden', 409: 'conflict', 404: 'not_found',429:'rate_limited',503:'unavailable'}.get(exception.status_code, 'invalid')
        message={401:'Akses ditolak',403:'Akses ditolak',404:'Data tidak ditemukan',429:'Terlalu banyak permintaan',503:'Layanan belum tersedia'}.get(exception.status_code,'Status tidak mengizinkan perubahan')
        return JSONResponse({'error': {'code': code, 'message': message}}, status_code=exception.status_code)

    app.include_router(create_router(service=engine), prefix='/api', dependencies=[Depends(require_operator)])
    operator = APIRouter(prefix='/api', dependencies=[Depends(require_operator)])

    @operator.get('/config')
    def config():
        return {**app.state.config,'outbound':'mock' if app.state.channels.settings.force_mock or public_demo else 'configured_only',
                'voice_available':app.state.voice.ready(),'voice_mode':app.state.voice.mode()}

    @operator.get('/responders')
    def responders(village_id: str | None = None):
        with app.state.database.connect() as connection:
            rows = connection.execute('SELECT id,village_id,name,role,tier,phone_last4,consent_at,last_contacted_at FROM responders WHERE (? IS NULL OR village_id=?)', (village_id, village_id)).fetchall()
        return {'items': [dict(row) for row in rows]}

    @operator.get('/responders/{responder_id}/messages')
    def messages(responder_id: str, after: str | None = None):
        with app.state.database.connect() as connection:
            if not connection.execute('SELECT 1 FROM responders WHERE id=?', (responder_id,)).fetchone():
                raise KeyError('Unknown responder')
        return {'items': message_items(app.state.database, responder_id, after)}

    @operator.get('/responders/{responder_id}/field-link')
    def field_link(responder_id:str):
        return {'field_path':app.state.field.link(responder_id)}

    @operator.post('/responders/{responder_id}/send')
    def operator_send(responder_id:str,body:OperatorSend):
        status,context=app.state.voice._context(responder_id)
        mock=app.state.channels.settings.public_demo or app.state.channels.settings.force_mock
        policy_status=status if mock else app.state.tools.get_village_status(status.village_id,'live',0)
        allowed,reason=decision('sms',policy_status.level,datetime.fromtimestamp(app.state.voice.clock(),timezone.utc))
        if not allowed:
            return {'message_id':None,'status':'suppressed','reason':reason}
        with app.state.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            message_id=app.state.channels.enqueue(responder_id,body.text,body.channel,trace_id=context.get('trace_id'),connection=connection)
        result=app.state.channels.dispatch(message_id)
        record(app.state.database,'Warden','operator_message',trace_id=context.get('trace_id'),input={'responder_id':responder_id,'channel':body.channel},output=result)
        return result

    @operator.post('/voice/twilio/call')
    def voice_call(body:VoiceCall):
        return app.state.voice.call(body.responder_id,body.diagnostic,uat=body.uat)

    @operator.post('/demo/inbound')
    def demo_inbound(body: DemoInbound):
        if not public_demo:
            raise HTTPException(403, 'Demo only')
        if body.mode == 'live' and body.day not in (None, 0):
            raise ValueError('Invalid live day')
        return app.state.channels.inbound(Inbound(responder_id=body.responder_id, channel='web', text=body.text, mode=body.mode, day=body.day))

    @operator.post('/events/risk-changed')
    def risk_changed(body: DemoAlert):
        return app.state.scout.manual(body.village_id,body.mode,body.day,hour_override=body.hour_override,level_override=body.level_to)

    @operator.post('/scout/tick')
    def scout_tick(body: ScoutTick):
        return app.state.scout.tick(body.mode,body.day)

    @operator.get('/policy')
    def policy():
        return POLICY

    @operator.get('/trace')
    def trace(trace_id: str | None=Query(None,max_length=80),after: str | None=Query(None,max_length=40)):
        return {'items':list_events(app.state.database,trace_id,after)}

    @operator.post('/observations')
    def observation(body: ObservationInput):
        return app.state.tools.log_field_observation(body)

    @operator.get('/actions')
    def actions(village_id: str | None = None, include_old: bool = False):
        return app.state.planner.list(village_id, include_old)

    @operator.post('/actions/{action_id}/approve')
    def approve(action_id: str, body: EmptyInput):
        return app.state.planner.transition(action_id, 'approve')

    @operator.post('/actions/{action_id}/reject')
    def reject(action_id: str, body: ActionNote):
        return app.state.planner.transition(action_id, 'reject', body.reason)

    @operator.post('/actions/{action_id}/complete')
    def complete(action_id: str, body: ActionNote):
        return app.state.planner.transition(action_id, 'complete', body.note)

    @operator.get('/demo/progress')
    def progress(responder_id: str):
        return app.state.planner.progress(responder_id)

    @operator.get('/observations')
    def observations(village_id: str | None = None):
        with app.state.database.connect() as connection:
            rows = connection.execute('SELECT id,data,pre_update_model,created_at FROM observations WHERE (? IS NULL OR village_id=?) ORDER BY created_at DESC LIMIT 200', (village_id,village_id)).fetchall()
        return {'items': [{'id': row['id'], **json.loads(row['data']), 'pre_update_model': json.loads(row['pre_update_model']), 'created_at': row['created_at']} for row in rows]}

    app.include_router(operator)

    @app.get('/api/health')
    def health() -> dict[str, str]:
        return {'status': 'ok'}

    @app.post('/api/auth/login')
    async def login(request:Request):
        auth=app.state.auth
        if not auth.attempts.allow(peer_ip(request)):
            raise HTTPException(429,'Login rate limit')
        if public_demo:
            return {'authenticated':True,'public_demo':True}
        if not auth.settings.configured():
            raise HTTPException(503,'Authentication unavailable')
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>1024:
                raise HTTPException(401,'Invalid login')
        try:
            body=json.loads(raw)
        except (ValueError,TypeError,RecursionError):
            raise HTTPException(401,'Invalid login') from None
        if not isinstance(body,dict) or set(body)!={'pin'} or not auth.check_pin(body['pin']):
            raise HTTPException(401,'Invalid login')
        response=JSONResponse({'authenticated':True,'public_demo':False})
        response.set_cookie(COOKIE_NAME,auth.issue(),max_age=SESSION_SECONDS,
                            expires=datetime.fromtimestamp(auth.clock()+SESSION_SECONDS,timezone.utc),
                            httponly=True,secure=secure_cookie(request),samesite='strict',path='/')
        return response

    @app.post('/api/auth/logout')
    def logout(request:Request):
        response=JSONResponse({'authenticated':False,'public_demo':public_demo})
        response.delete_cookie(COOKIE_NAME,path='/',httponly=True,secure=secure_cookie(request),samesite='strict')
        return response

    @app.get('/api/auth/session')
    def session(request:Request):
        return {'authenticated':app.state.auth.authenticated(request),'public_demo':public_demo}

    @app.get('/api/field/{token}/messages')
    def field_messages(token:str,after:str|None=Query(None,max_length=40)):
        return app.state.field.messages(token,after)

    def browser_access(request:Request,body=None,session_id=None):
        if 'x-field-token' in request.headers:
            token=request.headers.get('X-Field-Token','')
            responder=app.state.field.responder(token) # Invalid field credential never falls through to operator.
            owner='field:'+hashlib.sha256(token.encode()).hexdigest()
            state=app.state.database.get('warden:'+responder['id'],{})
            return Access(owner,responder['id'],responder['village_id'],state.get('mode','live'),state.get('day',0),True)
        require_operator(request)
        owner='operator:public' if public_demo else 'operator:'+hashlib.sha256(request.cookies.get(COOKIE_NAME,'').encode()).hexdigest()
        if session_id:
            session=app.state.database.get(app.state.browser_voice._key(session_id))
            if not session:
                raise HTTPException(404,'Unknown voice session')
            if session['owner']!=owner:
                raise HTTPException(403,'Voice scope mismatch')
            return Access(owner,session['responder_id'],session['village_id'],session['mode'],session['day'])
        responder_id=body.responder_id if body is not None else None
        if not responder_id:
            raise HTTPException(422,'Responder required')
        with app.state.database.connect() as connection:
            row=connection.execute('SELECT village_id FROM responders WHERE id=?',(responder_id,)).fetchone()
        if not row:
            raise KeyError('Unknown responder')
        state=app.state.database.get('warden:'+responder_id,{})
        mode=body.mode if body.mode is not None else state.get('mode','live')
        day=body.day if body.day is not None else state.get('day',0)
        if mode=='live' and day!=0:
            raise HTTPException(422,'Live context requires day zero')
        return Access(owner,responder_id,row['village_id'],mode,day)

    @app.post('/api/voice/session')
    async def browser_session(request:Request,body:BrowserStart):
        access=browser_access(request,body)
        return await asyncio.to_thread(app.state.browser_voice.mint,access)

    @app.post('/api/voice/transcript')
    def browser_transcript(request:Request,body:BrowserTranscript):
        access=browser_access(request,session_id=body.session_id)
        return app.state.browser_voice.transcript(access,body.session_id,body.event_id,body.role,body.text)

    @app.post('/api/voice/tool')
    def browser_tool(request:Request,body:BrowserTool):
        access=browser_access(request,session_id=body.session_id)
        return app.state.browser_voice.tool(access,body.session_id,body.call_id,body.name,body.arguments)

    @app.post('/api/voice/end')
    def browser_end(request:Request,body:BrowserEnd):
        access=browser_access(request,session_id=body.session_id)
        return app.state.browser_voice.end(access,body.session_id)

    @app.post('/api/voice/simulate')
    def browser_simulate(request:Request,body:BrowserSimulate):
        access=browser_access(request,body,body.session_id)
        return app.state.browser_voice.simulate(access,body.text,body.event_id,body.session_id)

    @app.post('/api/field/{token}/send')
    def field_send(token:str,body:FieldSend):
        return app.state.field.send(token,body.text)

    async def voice_callback(request:Request,kind:str):
        raw_query=request.scope.get('query_string',b'')
        path=request.scope['path']
        signature=request.headers.get('X-Twilio-Signature')
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>65536:
                raise HTTPException(413,'Voice payload too large')
        try:
            params=parse_qs(raw.decode('utf-8'),keep_blank_values=True,max_num_fields=80)
        except (ValueError,UnicodeError):
            raise HTTPException(422,'Invalid voice payload') from None
        def process():
            service=app.state.voice
            sid,identity,query=service.authenticate(path,raw_query,params,signature)
            if kind=='status':
                return service.status(sid,identity,params)
            xml=service.start(sid,identity) if kind=='start' else service.gather(sid,identity,query,params)
            return Response(xml,media_type='application/xml')
        return await asyncio.to_thread(process)

    @app.post('/api/voice/twilio/twiml/start')
    async def voice_start(request:Request):
        return await voice_callback(request,'start')

    @app.post('/api/voice/twilio/twiml/gather')
    async def voice_gather(request:Request):
        return await voice_callback(request,'gather')

    @app.post('/api/voice/twilio/status')
    async def voice_status(request:Request):
        return await voice_callback(request,'status')

    @app.post('/api/channels/sms/inbound')
    async def sms_inbound(request: Request):
        # Strip the credential query before server access logging sees the scope.
        key = request.query_params.get('key')
        request.scope['query_string'] = b''
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536:
                return JSONResponse({'error': {'code': 'invalid', 'message': 'Invalid gateway event'}}, status_code=413)
        channels = request.app.state.channels
        if not verify_webhook(channels.settings, key, bytes(raw), request.headers.get('X-Signature'), request.headers.get('X-Timestamp')):
            return JSONResponse({'error': {'code': 'forbidden', 'message': 'Webhook authentication required'}}, status_code=403)
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError('Invalid event')
            return await asyncio.to_thread(channels.sms_inbound,event)
        except (ValueError, TypeError, RecursionError):
            return JSONResponse({'error': {'code': 'invalid', 'message': 'Invalid gateway event'}}, status_code=422)

    dist = (web_dist if web_dist is not None else Path(__file__).resolve().parent.parent / 'web' / 'dist').resolve()
    mime = {'.js': 'application/javascript', '.mjs': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml',
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.ico': 'image/x-icon',
            '.woff': 'font/woff', '.woff2': 'font/woff2', '.webp': 'image/webp', '.html': 'text/html'}

    @app.get('/{requested_path:path}')
    def frontend(requested_path: str):
        def missing():
            return JSONResponse({'error': {'code': 'not_found', 'message': 'Tidak ditemukan'}}, status_code=404)
        if requested_path == 'api' or requested_path.startswith('api/') or '\\' in requested_path:
            return missing()
        if any(part.startswith('.') for part in Path(requested_path).parts):
            return missing()
        candidate = (dist / requested_path).resolve()
        if not candidate.is_relative_to(dist):
            return missing()
        if candidate.is_file():
            if candidate.suffix.lower() not in mime:
                return missing()
            return FileResponse(candidate, media_type=mime[candidate.suffix.lower()])
        if candidate.suffix or requested_path.startswith('assets/'):
            return missing()
        index = (dist / 'index.html').resolve()
        return FileResponse(index, media_type='text/html') if index.is_relative_to(dist) and index.is_file() else missing()

    return app


app = create_app()
