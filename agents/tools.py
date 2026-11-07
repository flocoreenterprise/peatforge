"""Server-only tools. The brain receives these facts, never computes them."""
from difflib import SequenceMatcher
import importlib
import json
import re
from typing import Any, Protocol
from .db import Database, identifier, utc_now
from .mock_engine import MockEngine, VILLAGE_ID
from .models import Channel, ForecastPoint, Landmark, Mode, ObservationInput, Simulation, SimulationRequest, Status
from .events import record


class Engine(Protocol):
    def village_status(self, village_id: str, mode: str = 'live', day: int = 0) -> dict[str, Any]: ...
    def forecast(self, village_id: str, days: int = 14, mode: str = 'live', day: int = 0) -> list[dict[str, Any]]: ...
    def simulate(self, village_id: str, request: dict[str, Any]) -> dict[str, Any]: ...
    def get_canals(self, village_id: str) -> list[dict[str, Any]]: ...
    def get_resources(self, village_id: str) -> dict[str, Any]: ...
    def get_hotspots(self, village_id: str, hours: int = 48, radius_km: float = 5, mode: str = 'live', day: int = 0) -> dict[str, Any]: ...


def engine_backend(mock: bool = True) -> Engine:
    # Explicit real mode never silently falls back, including import/401 failures.
    return MockEngine() if mock else importlib.import_module('engine.service')


def _context(mode: Mode, day: int) -> None:
    if mode not in ('live', 'replay') or not 0 <= day <= 61:
        raise ValueError('Invalid mode/day')


class ToolService:
    def __init__(self, database: Database, *, mock: bool = True, engine: Engine | None = None):
        self.db = database
        self.mock = mock
        self.channels = None
        self.engine = engine if engine is not None else engine_backend(mock)

    def get_village_status(self, village_id: str, mode: Mode = 'live', day: int = 0) -> Status:
        _context(mode, day)
        return Status.model_validate(self.engine.village_status(village_id, mode=mode, day=day))

    village_status = get_village_status

    def get_forecast(self, village_id: str, days: int = 14, mode: Mode = 'live', day: int = 0) -> list[ForecastPoint]:
        _context(mode, day)
        if not 1 <= days <= 90:
            raise ValueError('Invalid forecast horizon')
        return [ForecastPoint.model_validate(item) for item in self.engine.forecast(village_id, days=days, mode=mode, day=day)]

    forecast = get_forecast

    def simulate_intervention(self, village_id: str, request: SimulationRequest) -> Simulation:
        return Simulation.model_validate(self.engine.simulate(village_id, request.model_dump()))

    def get_canals(self, village_id: str) -> list[dict[str, Any]]:
        return self.engine.get_canals(village_id)

    def get_resources(self, village_id: str) -> dict[str, Any]:
        return self.engine.get_resources(village_id)

    def get_hotspots(self, village_id: str, hours: int = 48, radius_km: float = 5, mode: Mode = 'live', day: int = 0) -> dict[str, Any]:
        _context(mode, day)
        if not 1 <= hours <= 168 or not 0 < radius_km <= 100:
            raise ValueError('Invalid hotspot window')
        return self.engine.get_hotspots(village_id, hours=hours, radius_km=radius_km, mode=mode, day=day)

    def resolve_landmark(self, village_id: str, text: str) -> Landmark:
        normalized = ' '.join(re.findall(r'\w+', text.lower()))
        with self.db.connect() as connection:
            rows = connection.execute('SELECT * FROM landmarks WHERE village_id=?', (village_id,)).fetchall()
        best: tuple[float, Any] = (0.0, None)
        for row in rows:
            aliases = [row['id'].lower(), row['name'].lower(), *json.loads(row['aliases'])]
            score = max(1.0 if re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', normalized)
                        else SequenceMatcher(None, normalized, alias).ratio() for alias in aliases)
            if score > best[0]:
                best = score, row
        score, row = best
        if row is None or score < 0.65:
            return Landmark(landmark_id=None, name=None, lat=None, lon=None, match_score=score)
        return Landmark(landmark_id=row['id'], name=row['name'], lat=row['lat'], lon=row['lon'], match_score=score, source=row['source'])

    def log_field_observation(self, observation: ObservationInput, *, observation_id: str | None = None, trace_id: str | None = None) -> dict[str, Any]:
        if not observation.confirmed:
            raise ValueError('Standalone confirmation required')
        observation_id = observation_id or identifier('o')
        completed=self.db.get('observation-result:'+observation_id)
        if completed is not None:
            return completed
        with self.db.connect() as connection:
            existing = connection.execute('SELECT pre_update_model FROM observations WHERE id=?', (observation_id,)).fetchone()
        snapshot = json.loads(existing['pre_update_model']) if existing else self.get_village_status(observation.village_id, observation.mode, observation.day).model_dump()
        with self.db.connect() as connection:
            connection.execute('INSERT OR IGNORE INTO observations(id,village_id,responder_id,anonymous,confirmed,pre_update_model,data,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (observation_id, observation.village_id, observation.responder_id, int(observation.anonymous), 1,
                 json.dumps(snapshot, allow_nan=False), observation.model_dump_json(), utc_now()))
        reason='replay_skips_recalibration' if observation.mode=='replay' else 'adaptation_unavailable'
        difference=None if observation.wt_cm is None or snapshot.get('wt_cm') is None else abs(observation.wt_cm-snapshot['wt_cm'])
        result=dict(observation_id=observation_id,old=snapshot,new=snapshot,recalibrated=False,reason=reason,trace_id=trace_id)
        history=getattr(self.engine,'params_history',None)
        versions=history(observation.village_id) if history else []
        result['version']=versions[-1]['version'] if versions else None
        recalibrate=getattr(self.engine,'recalibrate',None)
        if observation.mode=='live' and observation.wt_cm is not None:
            if difference is not None and difference>30:
                result['reason']='discrepancy_requires_verification'
            elif recalibrate is not None:
                result={**recalibrate(observation.village_id,observation.model_dump(),observation_id),
                        'old':snapshot,'observation_id':observation_id,'trace_id':trace_id}
        elif observation.mode=='replay' and observation.wt_cm is not None and recalibrate is not None:
            result={**recalibrate(observation.village_id,observation.model_dump(),observation_id),
                    'old':snapshot,'observation_id':observation_id,'trace_id':trace_id}
        elif observation.wt_cm is None:
            result['reason']='no_water_depth'
        self.db.set('observation-result:'+observation_id,result)
        record(self.db,'Warden','observation_confirmed',trace_id=trace_id,
               input={'village_id':observation.village_id,'responder_id':observation.responder_id,'mode':observation.mode,'day':observation.day},
               output={'observation_id':observation_id,'recalibrated':result['recalibrated'],'reason':result['reason'],
                       'version':result.get('version'),'difference_cm':difference},event_id='e_observation_'+observation_id)
        return result

    def notify(self, responder_id: str, text: str, channel: Channel = 'sms', trace_id: str | None = None) -> dict[str, str]:
        if self.channels is not None:
            return self.channels.send(responder_id, text, channel, trace_id=trace_id)
        if not self.mock:
            raise RuntimeError('Channel adapter is not configured')
        if channel not in ('sms', 'voice', 'telegram', 'web') or not text.strip() or len(text) > 300:
            raise ValueError('Invalid message')
        message_id = identifier('m')
        with self.db.connect() as connection:
            if not connection.execute('SELECT 1 FROM responders WHERE id=?', (responder_id,)).fetchone():
                raise KeyError('Unknown responder')
            connection.execute('INSERT INTO messages(id,responder_id,direction,channel,body,status,trace_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                               (message_id, responder_id, 'outbound', channel, text, 'mock', trace_id, utc_now()))
        return dict(message_id=message_id, status='mock')

    send_message = notify


def seed_mock(database: Database) -> None:
    """Explicit fixture seed: no contacts, consent, tokens or public field access."""
    with database.connect() as connection:
        connection.execute('INSERT OR IGNORE INTO responders(id,village_id,name) VALUES(?,?,?)', ('r1', VILLAGE_ID, 'Responder demo'))
        for landmark_id, name, aliases in [('K4', 'Kanal empat', ['kanal 4', 'kanal empat', 'parit empat', 'parit 4']), ('W2', 'Sumur dua', ['sumur dua', 'sumur 2'])]:
            connection.execute('INSERT OR IGNORE INTO landmarks(id,village_id,name,aliases,source) VALUES(?,?,?,?,?)',
                               (landmark_id, VILLAGE_ID, name, json.dumps(aliases), 'fixture'))
