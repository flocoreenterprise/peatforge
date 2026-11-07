"""B1 acceptance uses temporary state and no external effects."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from engine.service import EngineService
from engine.ingest import Ingest
from .app import RUNTIME_DIR, create_app, public_demo_setting
from .brain import OfflineBrain, safe_rewrite
from .db import Database
from .mock_engine import VILLAGE_ID
from .models import ObservationInput, SimulationRequest, Status
from .tools import ToolService, engine_backend, seed_mock


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'state.sqlite3')
        self.db.initialize()
        seed_mock(self.db)
        self.tools = ToolService(self.db)

    def test_schema_and_reopen_persistence(self):
        self.db.set('value', {'state': 'confirm'})
        self.assertEqual(Database(self.db.path).get('value'), {'state': 'confirm'})
        with self.db.connect() as connection:
            tables = {row['name'] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'villages', 'landmarks', 'forecasts', 'responders', 'messages', 'observations', 'actions', 'agent_events', 'kv'} <= tables)

    def test_fixture_facts_and_nullable_benefit(self):
        status = self.tools.get_village_status(VILLAGE_ID, 'replay', 5)
        self.assertEqual(status.source, 'fixture')
        self.assertEqual(status.data_as_of[:10], '2019-06-20')
        self.assertEqual(len(self.tools.get_forecast(VILLAGE_ID, 3)), 3)
        result = self.tools.simulate_intervention(VILLAGE_ID, SimulationRequest(type='canal_block', targets=['K4']))
        self.assertIsNone(result.days_bought)
        self.assertEqual(result.reason, 'fixture_no_simulation')
        self.assertFalse(self.tools.get_hotspots(VILLAGE_ID)['available'])

    def test_invalid_numbers_and_context_rejected(self):
        raw = self.tools.get_village_status(VILLAGE_ID).model_dump()
        for value in (float('nan'), float('inf'), -1, 301):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Status.model_validate({**raw, 'pfvi': value})
        with self.assertRaises(ValueError):
            self.tools.get_village_status(VILLAGE_ID, day=62)

    def test_landmark_aliases_scoping_and_unknown(self):
        for text in ('kanal 4', 'parit empat', 'dekat kanal empat'):
            self.assertEqual(self.tools.resolve_landmark(VILLAGE_ID, text).landmark_id, 'K4')
        self.assertEqual(self.tools.resolve_landmark(VILLAGE_ID, 'sumur dua').landmark_id, 'W2')
        self.assertIsNone(self.tools.resolve_landmark('other-village', 'kanal 4').landmark_id)
        self.assertIsNone(self.tools.resolve_landmark(VILLAGE_ID, 'zebra').landmark_id)

    def test_confirmed_only_and_retained_pre_update(self):
        raw = dict(village_id=VILLAGE_ID, responder_id='r1', wt_cm=61, anonymous=True, mode='replay')
        with self.assertRaises(ValueError):
            self.tools.log_field_observation(ObservationInput(**raw))
        result = self.tools.log_field_observation(ObservationInput(**raw, confirmed=True))
        with self.db.connect() as connection:
            row = connection.execute('SELECT * FROM observations').fetchone()
        self.assertEqual(json.loads(row['pre_update_model'])['wt_cm'], 45)
        self.assertEqual(row['anonymous'], 1)
        self.assertFalse(result['recalibrated'])
        self.assertEqual(result['reason'], 'replay_skips_recalibration')

    def test_mock_notify_and_fail_closed_real_channels(self):
        self.assertEqual(self.tools.notify('r1', 'Cek kanal 4')['status'], 'mock')
        with self.assertRaises(KeyError):
            self.tools.notify('missing', 'test')
        real_tools = ToolService(self.db, mock=False, engine=self.tools.engine)
        with self.assertRaises(RuntimeError):
            real_tools.notify('r1', 'test')

    def test_real_import_failure_does_not_fallback(self):
        with patch('agents.tools.importlib.import_module', side_effect=ImportError('missing')):
            with self.assertRaises(ImportError):
                engine_backend(mock=False)

    def test_numeric_url_and_length_rewrite_guard(self):
        original = 'Air 61 cm dekat K4: https://example.test/f/43'
        self.assertEqual(safe_rewrite(original, 'Mohon cek 61 cm dekat K4: https://example.test/f/43'), 'Mohon cek 61 cm dekat K4: https://example.test/f/43')
        for text in (original.replace('61', '62'), original.replace('61', '-61'), original.replace('61', '.61'), original.replace('/43', '/44'), original.replace('example.test', 'other.test'), original + 'x' * 300, ''):
            self.assertEqual(safe_rewrite(original, text), original)
        self.assertEqual(safe_rewrite('Buka /f/opaque', 'Buka /f/changed'), 'Buka /f/opaque')
        self.assertEqual(OfflineBrain().mode, 'offline')

    def test_application_health_uses_temporary_database(self):
        with TestClient(create_app(db_path=Path(self.temp.name) / 'app.sqlite3')) as client:
            self.assertEqual(client.get('/api/health').json(), {'status': 'ok'})
            self.assertEqual(client.get('/api/responders').status_code, 200)

    def test_public_demo_default_and_mock_outbound_independent_source(self):
        for value in (None, '', '  ', 'true', 'TRUE'):
            self.assertTrue(public_demo_setting(value))
        self.assertFalse(public_demo_setting('false'))
        with self.assertRaises(ValueError):
            public_demo_setting('maybe')
        with patch.dict('os.environ', {'PUBLIC_DEMO': '', 'PEATFORGE_ENGINE_MODE': 'real', 'PEATFORGE_DB_PATH': ''}):
            app = create_app(mock=False)
        self.assertTrue(app.state.public_demo)
        self.assertEqual(app.state.config['mode'], 'mock')
        self.assertEqual(app.state.config['source'], 'live')
        self.assertEqual(app.state.database_path, (RUNTIME_DIR / 'demo.sqlite3').resolve())

    def test_protected_default_path_and_false_config(self):
        with patch.dict('os.environ', {'PUBLIC_DEMO': 'false', 'PEATFORGE_ENGINE_MODE': 'mock', 'PEATFORGE_DB_PATH': ''}):
            app = create_app()
        self.assertFalse(app.state.config['public_demo'])
        self.assertEqual(app.state.database_path, (RUNTIME_DIR / 'state.sqlite3').resolve())

    def test_public_demo_env_override_cannot_point_to_live_default(self):
        with patch.dict('os.environ', {'PUBLIC_DEMO': 'true', 'PEATFORGE_DB_PATH': str(RUNTIME_DIR / 'state.sqlite3')}):
            with self.assertRaises(ValueError):
                create_app()

    def test_database_scopes_isolate_custom_paths(self):
        protected_path = Path(self.temp.name) / 'protected.sqlite3'
        with TestClient(create_app(db_path=protected_path, public_demo=False)):
            pass
        with self.assertRaises(ValueError):
            with TestClient(create_app(db_path=protected_path, public_demo=True)):
                pass
        demo_path = Path(self.temp.name) / 'demo.sqlite3'
        with TestClient(create_app(db_path=demo_path, public_demo=True)):
            pass
        with self.assertRaises(ValueError):
            with TestClient(create_app(db_path=demo_path, public_demo=False)):
                pass
        # An unmarked legacy database with responders is also rejected.
        with self.assertRaises(ValueError):
            with TestClient(create_app(db_path=self.db.path, public_demo=True)):
                pass

    def test_public_demo_uses_computed_engine_with_fixture_input(self):
        with patch('agents.tools.importlib.import_module', side_effect=AssertionError('must not import')):
            engine = EngineService(Ingest())
            with TestClient(create_app(db_path=Path(self.temp.name) / 'public.sqlite3', public_demo=True, engine=engine)) as client:
                self.assertTrue(client.app.state.tools.mock)
                self.assertIs(client.app.state.tools.engine, engine)
                self.assertEqual(client.app.state.tools.get_village_status(VILLAGE_ID).source, 'fixture')


if __name__ == '__main__':
    unittest.main()
