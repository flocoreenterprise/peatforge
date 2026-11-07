"""Main service skeleton. Operator routers will mount behind shared auth in B8."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from engine.api import create_router
from engine.cache import DataUnavailable
from engine.ingest import Ingest
from engine.service import EngineService
from engine.villages import list_villages
from .brain import OfflineBrain
from .channels import ChannelAdapter, ChannelSettings, ChannelUnavailable, Inbound, verify_webhook
from .db import Database, identifier
from .models import Level, Mode, Model
from .tools import ToolService, seed_mock
from .warden import Warden, message_items

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
    if not request.app.state.public_demo:
        raise HTTPException(401, 'Operator authentication unavailable')


class DemoInbound(Model):
    responder_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)
    mode: Mode = 'live'
    day: int = Field(default=0, ge=0, le=61)


class DemoAlert(Model):
    village_id: str = Field(min_length=1, max_length=80)
    mode: Mode = 'live'
    day: int = Field(default=0, ge=0, le=61)
    level_to: Level | None = None
    hour_override: int | None = Field(default=None, ge=0, le=23)


def create_app(*, db_path: str | Path | None = None, mock: bool | None = None,
               public_demo: bool | None = None, engine: EngineService | None = None,
               data_source: str | None = None) -> FastAPI:
    public_demo = public_demo_setting(os.environ.get('PUBLIC_DEMO')) if public_demo is None else public_demo
    source = data_source or os.environ.get('PEATFORGE_DATA_SOURCE', '').strip() or 'live'
    if source not in ('live', 'fixture'):
        raise ValueError('PEATFORGE_DATA_SOURCE must be live or fixture')
    # Auth and channels do not select environmental inputs. No network at construction.
    engine = engine if engine is not None else EngineService(Ingest() if source == 'fixture' else None)
    source = engine.ingest.mode
    default_path = RUNTIME_DIR / ('demo.sqlite3' if public_demo else 'state.sqlite3')
    configured_path = os.environ.get('PEATFORGE_DB_PATH', '').strip()
    path = Path(db_path if db_path is not None else configured_path or default_path).resolve()
    forbidden_path = (RUNTIME_DIR / ('state.sqlite3' if public_demo else 'demo.sqlite3')).resolve()
    if path == forbidden_path:
        raise ValueError('Database path crosses the public/protected boundary')

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
        app.state.tools.channels = app.state.channels
        app.state.warden = Warden(database, app.state.tools, app.state.channels)
        app.state.channels.on_inbound = app.state.warden.handle
        app.state.channels.prepare_inbound = app.state.warden.prepare
        app.state.brain = OfflineBrain()
        yield

    app = FastAPI(title='Peatforge', lifespan=lifespan)
    app.state.public_demo = public_demo
    app.state.database_path = path
    app.state.config = dict(mode='mock' if public_demo else 'protected', public_demo=public_demo,
                            source=source, engine='deterministic', outbound='mock' if public_demo else 'configured_only',
                            poll_ms=4000, field_poll_ms=3000, voice_available=False)

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
    async def auth_handler(request, exception):
        return JSONResponse({'error': {'code': 'unauthorized' if exception.status_code == 401 else 'forbidden', 'message': 'Akses ditolak'}}, status_code=exception.status_code)

    app.include_router(create_router(service=engine), prefix='/api', dependencies=[Depends(require_operator)])
    operator = APIRouter(prefix='/api', dependencies=[Depends(require_operator)])

    @operator.get('/config')
    def config():
        return app.state.config

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

    @operator.post('/demo/inbound')
    def demo_inbound(body: DemoInbound):
        if not public_demo:
            raise HTTPException(403, 'Demo only')
        if body.mode == 'live' and body.day != 0:
            raise ValueError('Invalid live day')
        return app.state.channels.inbound(Inbound(responder_id=body.responder_id, channel='web', text=body.text, mode=body.mode, day=body.day))

    @operator.post('/events/risk-changed')
    def risk_changed(body: DemoAlert):
        if not public_demo:
            raise HTTPException(403, 'Demo only')
        status = app.state.tools.get_village_status(body.village_id, body.mode, body.day)
        if status.pfvi is None or status.wt_cm is None:
            raise DataUnavailable('Alert facts unavailable')
        trace_id = identifier('t')
        with app.state.database.connect() as connection:
            rows = connection.execute('SELECT id FROM responders WHERE village_id=?', (body.village_id,)).fetchall()
        for row in rows:
            app.state.warden.reset_for_alert(row['id'], body.mode, body.day, trace_id)
            text = f'Simulasi notifikasi. Tingkat model {status.level}; indeks {status.pfvi:g}; air {status.wt_cm:g} cm (perkiraan model, belum diukur). Balas AIR dalam cm dan ASAP YA atau TIDAK.'
            app.state.channels.send(row['id'], text, 'sms', trace_id=trace_id)
        return {'trace_id': trace_id, 'notified': len(rows), 'suppressed': 0, 'mock': True,
                'level': status.level, 'source': status.source, 'mode': body.mode, 'day': body.day,
                'assumptions': ['Pemicu demo manual; override tidak mengubah fakta model']}

    app.include_router(operator)

    @app.get('/api/health')
    def health() -> dict[str, str]:
        return {'status': 'ok'}

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
            return channels.sms_inbound(event)
        except (ValueError, TypeError, RecursionError):
            return JSONResponse({'error': {'code': 'invalid', 'message': 'Invalid gateway event'}}, status_code=422)

    return app


app = create_app()
