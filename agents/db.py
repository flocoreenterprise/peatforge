"""SQLite is explicit and injected; opening a module never creates runtime state."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def identifier(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex}'


SCHEMA = '''
CREATE TABLE IF NOT EXISTS villages (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS landmarks (id TEXT NOT NULL, village_id TEXT NOT NULL, name TEXT NOT NULL,
 lat REAL, lon REAL, aliases TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'fixture', PRIMARY KEY(village_id,id));
CREATE TABLE IF NOT EXISTS forecasts (village_id TEXT NOT NULL, mode TEXT NOT NULL, day INTEGER NOT NULL,
 data TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(village_id,mode,day));
CREATE TABLE IF NOT EXISTS responders (id TEXT PRIMARY KEY, village_id TEXT NOT NULL, name TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'MPA', tier INTEGER NOT NULL DEFAULT 1, phone_encrypted TEXT, phone_hash TEXT UNIQUE,
 phone_last4 TEXT, consent_at TEXT, field_token TEXT UNIQUE, last_contacted_at TEXT);
CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, responder_id TEXT NOT NULL, direction TEXT NOT NULL,
 channel TEXT NOT NULL, body TEXT NOT NULL, speech TEXT, status TEXT NOT NULL, trace_id TEXT,
 inbound_message_id TEXT UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, village_id TEXT NOT NULL, responder_id TEXT NOT NULL,
 anonymous INTEGER NOT NULL DEFAULT 0, confirmed INTEGER NOT NULL DEFAULT 0,
 pre_update_model TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, village_id TEXT NOT NULL, kind TEXT NOT NULL,
 type TEXT NOT NULL, status TEXT NOT NULL, benefit TEXT, approved_by TEXT, expires_at TEXT NOT NULL,
 observation_id TEXT, responder_id TEXT, anonymous INTEGER NOT NULL DEFAULT 0, trace_id TEXT,
 data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_events (id TEXT PRIMARY KEY, agent TEXT NOT NULL, event TEXT NOT NULL,
 input TEXT NOT NULL, output TEXT NOT NULL, tool_calls TEXT NOT NULL, trace_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS messages_responder_time ON messages(responder_id,created_at);
CREATE INDEX IF NOT EXISTS actions_village_status ON actions(village_id,status);
'''


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def get(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, allow_nan=False)
        with self.connect() as connection:
            connection.execute('INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, encoded))
