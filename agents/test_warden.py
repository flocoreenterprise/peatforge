import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app
from .channels import ChannelUnavailable, FIXTURE_SENDER
from .mock_engine import VILLAGE_ID
from .warden import parse_report


class WardenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = EngineService(Ingest())
        self.app = create_app(db_path=Path(self.temp.name) / 'state.sqlite3', public_demo=True, engine=self.engine)
        self.client = self.enterContext(TestClient(self.app))

    def send(self, text, mode='replay', day=9):
        response = self.client.post('/api/demo/inbound', json=dict(responder_id='r1', text=text, mode=mode, day=day))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def observations(self):
        with self.app.state.database.connect() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM observations')]

    def test_readback_then_standalone_confirmation_replay_persists(self):
        result = self.send('AIR 61 ASAP YA dekat kanal 4')
        self.assertEqual(result['state'], 'confirm')
        self.assertEqual(self.observations(), [])
        self.assertIn('61 cm', result['items'][-1]['body'])
        self.assertEqual(self.send('matiin ya')['state'], 'confirm')
        self.assertEqual(self.observations(), [])
        saved = self.send('YA', mode='live', day=0)
        self.assertEqual(saved['state'], 'saved')
        rows = self.observations()
        self.assertEqual(len(rows), 1)
        observation = json.loads(rows[0]['data'])
        self.assertEqual((observation['wt_cm'], observation['smoke'], observation['landmark_id']), (61, True, 'K4'))
        self.assertEqual((observation['mode'], observation['day']), ('replay', 9))
        self.assertEqual(json.loads(rows[0]['pre_update_model'])['day'], 9)
        self.send('YA')
        self.assertEqual(len(self.observations()), 1)

    def test_replacement_no_and_clarification_clear_old_report(self):
        self.send('AIR 61 ASAP YA')
        self.send('AIR 72 ASAP TIDAK')
        self.send('YA')
        observation = json.loads(self.observations()[0]['data'])
        self.assertEqual((observation['wt_cm'], observation['smoke']), (72, False))
        self.send('AIR 55')
        self.assertEqual(self.send('TIDAK')['state'], 'idle')
        self.send('YA')
        self.assertEqual(len(self.observations()), 1)
        self.send('AIR 44')
        self.assertEqual(self.send('tinggi air kurang lebih 5 m')['state'], 'idle')
        self.send('YA')
        self.assertEqual(len(self.observations()), 1)

    def test_units_words_and_plausibility(self):
        for text, expected in [('AIR 0.61 m', 61), ('AIR 0,72 meter', 72), ('AIR enam satu senti', 61), ('AIR tujuh puluh dua senti', 72), ('AIR satu meter', 100), ('AIR nol koma enam satu meter', 61)]:
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_report(text)[0]['wt_cm'], expected)
        for text in ('AIR 151 cm', 'AIR -5 cm', 'AIR 5 m', 'AIR banyak', 'ASAP mungkin'):
            with self.subTest(text=text):
                self.assertTrue(parse_report(text)[1])

    def test_alert_resets_pending_and_uses_engine_facts(self):
        self.send('AIR 61')
        expected = self.engine.village_status(VILLAGE_ID, 'replay', 7)
        result = self.client.post('/api/events/risk-changed', json=dict(village_id=VILLAGE_ID, mode='replay', day=7, level_to='EKSTREM', hour_override=10))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['level'], expected['level'])
        self.send('YA')
        self.assertEqual(self.observations(), [])
        self.send('AIR 54', mode='replay', day=7)
        self.send('YA')
        self.assertEqual(json.loads(self.observations()[0]['data'])['day'], 7)

    def test_state_survives_application_restart(self):
        self.send('AIR 61', day=5)
        restarted = create_app(db_path=self.app.state.database_path, public_demo=True, engine=self.engine)
        with TestClient(restarted) as client:
            response = client.post('/api/demo/inbound', json={'responder_id': 'r1', 'text': 'YA'})
            self.assertEqual(response.json()['state'], 'saved')
        self.assertEqual(json.loads(self.observations()[0]['data'])['day'], 5)

    def test_safe_bounded_inputs_and_private_mode_fail_closed(self):
        for payload in ({'responder_id': 'r1', 'text': 'x'*501}, {'responder_id': 'unknown', 'text': 'test'}, {'responder_id': 'r1', 'text': 'AIR 61', 'day': 62}):
            self.assertIn(self.client.post('/api/demo/inbound', json=payload).status_code, (404, 422))
        private = create_app(db_path=Path(self.temp.name) / 'private.sqlite3', public_demo=False, engine=self.engine)
        with TestClient(private) as client:
            for method, path, payload in [('GET','/api/config',None), ('GET','/api/responders',None), ('GET','/api/villages',None), ('POST','/api/demo/inbound',{'responder_id':'r1','text':'YA'})]:
                self.assertEqual(client.request(method, path, json=payload).status_code, 401)
        self.assertEqual(self.client.get('/api/config').json()['source'], 'fixture')

    def test_engine_routes_and_tools_share_identical_instance(self):
        expected = self.app.state.tools.get_village_status(VILLAGE_ID, 'replay', 9).model_dump()
        result = self.client.get(f'/api/villages/{VILLAGE_ID}/status?mode=replay&day=9')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), expected)
        self.assertIs(self.app.state.tools.engine, self.engine)

    def test_unsupported_anonymity_and_calls_do_not_save(self):
        for text in ('ANONIM', 'TELP', 'ini siapa', 'terima kasih ya'):
            self.send(text)
        self.assertEqual(self.observations(), [])
        with self.app.state.database.connect() as connection:
            statuses = {row['status'] for row in connection.execute("SELECT status FROM messages WHERE direction='outbound'")}
        self.assertEqual(statuses, {'mock'})

    def test_sms_and_demo_share_confirmation_pipeline(self):
        self.app.state.channels.settings.webhook_secret = 's' * 32
        alert = self.client.post('/api/events/risk-changed', json={'village_id': VILLAGE_ID, 'mode': 'replay', 'day': 5})
        self.assertEqual(alert.status_code, 200)
        for message_id, text in [('sms-report', 'AIR 61 ASAP YA dekat kanal 4'), ('sms-confirm', 'YA')]:
            payload = {'event': 'sms:received', 'payload': {'sender': FIXTURE_SENDER, 'message': text, 'messageId': message_id}}
            response = self.client.post('/api/channels/sms/inbound?key=' + 's'*32, json=payload)
            self.assertEqual(response.json(), {'accepted': True, 'duplicate': False})
        rows = self.observations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]['data'])['day'], 5)
        self.assertEqual(self.client.post('/api/channels/sms/inbound?key=' + 's'*32, json=payload).json()['duplicate'], True)
        self.assertEqual(len(self.observations()), 1)

    def _sms(self, message_id, text):
        self.app.state.channels.settings.webhook_secret = 's' * 32
        return self.client.post('/api/channels/sms/inbound?key=' + 's'*32, json={
            'event': 'sms:received', 'payload': {'sender': FIXTURE_SENDER, 'message': text, 'messageId': message_id}})

    def test_transient_callback_failure_retries_same_id_once_with_original_context(self):
        self.send('AIR 61 ASAP YA', day=5)
        original = self.app.state.tools.log_field_observation
        with patch.object(self.app.state.tools, 'log_field_observation', side_effect=ChannelUnavailable('transient')):
            failed = self._sms('retry-confirm', 'YA')
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(self.observations(), [])
        # Changed delivery contents/context cannot replace the receipt on retry.
        self.app.state.warden.reset_for_alert('r1', 'replay', 12, 'new-alert')
        self.send('AIR 77', day=12)
        with patch.object(self.app.state.tools, 'log_field_observation', wraps=original) as save:
            self.assertEqual(self._sms('retry-confirm', 'YA changed').status_code, 200)
            self.assertEqual(save.call_count, 1)
            self.assertTrue(self._sms('retry-confirm', 'YA').json()['duplicate'])
            self.assertEqual(save.call_count, 1)
        self.assertEqual(len(self.observations()), 1)
        observation = json.loads(self.observations()[0]['data'])
        self.assertEqual((observation['wt_cm'], observation['day']), (61, 5))
        self.assertEqual(self.app.state.database.get('warden:r1')['pending']['wt_cm'], 77)

    def test_failed_after_confirm_hook_resumes_without_duplicate_observation(self):
        self.send('AIR 61', day=7)
        calls = []
        def hook(observation, saved):
            calls.append(saved['observation_id'])
            if len(calls) == 1:
                raise ChannelUnavailable('temporary hook failure')
        self.app.state.warden.after_confirm = hook
        self.assertEqual(self._sms('hook-retry', 'YA').status_code, 503)
        self.assertEqual(len(self.observations()), 1)
        self.assertEqual(self.app.state.database.get('warden:r1')['state'], 'saved')
        self.assertEqual(self._sms('hook-retry', 'YA').status_code, 200)
        self.assertEqual(len(self.observations()), 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertTrue(self._sms('hook-retry', 'YA').json()['duplicate'])
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
