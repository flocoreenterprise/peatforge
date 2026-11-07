"""Main service skeleton. Operator routers will mount behind shared auth in B8."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from fastapi import FastAPI
from .brain import OfflineBrain
from .db import Database
from .tools import ToolService, seed_mock

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


def create_app(*, db_path: str | Path | None = None, mock: bool | None = None,
               public_demo: bool | None = None) -> FastAPI:
    public_demo = public_demo_setting(os.environ.get('PUBLIC_DEMO')) if public_demo is None else public_demo
    # Until the engine fixture-input adapter exists, public demo never imports real sources.
    engine_mode = os.environ.get('PEATFORGE_ENGINE_MODE', 'mock').strip() or 'mock'
    if engine_mode not in ('mock', 'real'):
        raise ValueError('PEATFORGE_ENGINE_MODE must be mock or real')
    mock = True if public_demo else (engine_mode == 'mock' if mock is None else mock)
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
        if mock:
            seed_mock(database)
        app.state.database = database
        app.state.tools = ToolService(database, mock=mock)
        app.state.brain = OfflineBrain()
        yield

    app = FastAPI(title='Peatforge', lifespan=lifespan)
    app.state.public_demo = public_demo
    app.state.database_path = path
    app.state.config = dict(mode='mock' if mock else 'real', public_demo=public_demo,
                            poll_ms=4000, field_poll_ms=3000, voice_available=False)

    @app.get('/api/health')
    def health() -> dict[str, str]:
        return {'status': 'ok'}

    return app


app = create_app()
