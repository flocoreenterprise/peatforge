from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app
from .channels import ChannelUnavailable
from .events import record
from .mock_engine import VILLAGE_ID
from .scout import decision


class ScoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine=EngineService(Ingest())
        self.app=create_app(db_path=Path(self.temp.name)/'state.sqlite3',public_demo=True,engine=self.engine,background_scout=False)
        self.client=self.enterContext(TestClient(self.app))
        self.now=datetime(2026,11,7,3,tzinfo=timezone.utc) # 10 WIB
        self.app.state.scout.clock=lambda:self.now
        self.real_status=self.app.state.tools.get_village_status

    def statuses(self,level):
        def status(village_id,mode='live',day=0):
            return self.real_status(village_id,mode,day).model_copy(update={'level':level})
        return patch.object(self.app.state.tools,'get_village_status',side_effect=status)

    def tick(self,day=0,mode='replay'):
        result=self.client.post('/api/scout/tick',json={'mode':mode,'day':day})
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def test_crossings_idempotency_persistence_and_contexts(self):
        with self.statuses('SEDANG'):
            self.assertEqual(self.tick()['notified'],0)
        with self.statuses('TINGGI'):
            self.assertEqual(self.tick(1)['notified'],5)
            self.assertEqual(self.tick(1)['notified'],0)
        with self.statuses('EKSTREM'):
            self.assertEqual(self.tick(2)['notified'],5)
        with self.statuses('TINGGI'):
            self.assertEqual(self.tick(3)['notified'],5) # One decrease SMS each.
        with self.app.state.database.connect() as connection:
            rows=list(connection.execute("SELECT channel,status,body FROM messages WHERE direction='outbound'"))
        self.assertEqual(len(rows),15)
        self.assertTrue(all(row['channel']=='sms' and row['status']=='mock' for row in rows))
        self.assertEqual(self.app.state.database.get('scout:replay:'+VILLAGE_ID)['notified_level'],'TINGGI')
        self.assertIsNone(self.app.state.database.get('scout:live:'+VILLAGE_ID))

    def test_rewind_resets_baseline_without_alerts_then_rearms(self):
        with self.statuses('EKSTREM'):
            self.assertEqual(self.tick(9)['notified'],5)
        with self.statuses('SEDANG'):
            result=self.tick(0)
            self.assertTrue(result['rewound'])
            self.assertEqual(result['notified'],0)
            self.assertEqual(self.tick(0)['notified'],0)
        with self.statuses('TINGGI'):
            self.assertEqual(self.tick(3)['notified'],5)

    def test_quiet_hours_pending_retries_and_extreme_exception(self):
        self.now=datetime(2026,11,7,15,tzinfo=timezone.utc) # 22 WIB
        with self.statuses('TINGGI'):
            result=self.tick()
            self.assertEqual(result['notified'],0)
            self.assertEqual(result['suppressed'],5)
            self.assertIsNone(self.app.state.database.get('scout:replay:'+VILLAGE_ID).get('notified_level'))
            self.now+=timedelta(hours=12)
            self.assertEqual(self.tick()['notified'],5)
        self.now=datetime(2026,11,8,15,tzinfo=timezone.utc)
        with self.statuses('EKSTREM'):
            self.assertEqual(self.tick(1)['notified'],5)

    def test_send_failure_does_not_mark_sent_retry_is_once(self):
        real_send=self.app.state.channels.send
        with self.statuses('TINGGI'):
            with patch.object(self.app.state.channels,'send',side_effect=ChannelUnavailable('temporary')):
                self.assertEqual(self.tick()['notified'],0)
            self.assertTrue(self.app.state.database.get('scout:replay:'+VILLAGE_ID)['pending'])
            self.assertEqual(self.tick()['notified'],5)
            self.assertEqual(self.tick()['notified'],0)

    def test_voice_daily_limit_extreme_and_hard_cooldown(self):
        with self.statuses('TINGGI'):
            first=self.app.state.scout.place_call('r1','replay',0)
            self.assertEqual(first['status'],'mock')
            self.assertEqual(self.app.state.scout.place_call('r1','replay',0)['reason'],'call_cooldown')
            self.now+=timedelta(seconds=31)
            self.assertEqual(self.app.state.scout.place_call('r1','replay',0)['reason'],'daily_call_limit')
        with self.statuses('EKSTREM'):
            self.assertEqual(self.app.state.scout.place_call('r1','replay',0)['reason'],'daily_call_limit')
            self.now+=timedelta(days=1)
            self.assertEqual(self.app.state.scout.place_call('r1','replay',0)['status'],'mock')
            self.assertEqual(self.app.state.scout.place_call('r1','replay',0)['reason'],'call_cooldown')
        with self.statuses('SEDANG'):
            self.assertEqual(self.app.state.scout.place_call('r1','replay',0)['reason'],'voice_below_high')
        quiet=datetime(2026,11,7,15,tzinfo=timezone.utc)
        self.assertEqual(decision('sms','TINGGI',quiet),(False,'quiet_hours'))
        self.assertEqual(decision('sms','EKSTREM',quiet),(True,'allowed'))

    def test_background_live_cannot_reset_active_replay_conversation(self):
        with self.statuses('TINGGI'):
            self.tick(9)
        response=self.client.post('/api/demo/inbound',json={'responder_id':'r1','text':'AIR 61 ASAP YA'})
        self.assertEqual(response.json()['state'],'confirm')
        with self.statuses('EKSTREM'):
            result=self.app.state.scout.tick('live',0,background=True)
        self.assertEqual(result['reason'],'active_replay_context')
        state=self.app.state.database.get('warden:r1')
        self.assertEqual((state['mode'],state['day'],state['state']),('replay',9,'confirm'))

    def test_manual_policy_override_labels_and_actual_facts_unchanged(self):
        self.now=datetime(2026,11,7,15,tzinfo=timezone.utc)
        expected=self.engine.village_status(VILLAGE_ID,'replay',9)
        blocked=self.client.post('/api/events/risk-changed',json={'village_id':VILLAGE_ID,'mode':'replay','day':9})
        if expected['level']!='EKSTREM':
            self.assertEqual(blocked.json()['notified'],0)
        permitted=self.client.post('/api/events/risk-changed',json={'village_id':VILLAGE_ID,'mode':'replay','day':9,'hour_override':10})
        self.assertEqual(permitted.json()['notified'],1)
        self.assertEqual(permitted.json()['level'],expected['level'])
        policy=self.client.get('/api/policy').json()
        self.assertEqual(policy['call_cooldown_seconds'],30)

    def test_actual_trace_chain_is_structured_and_redacts_unapproved_fields(self):
        alert=self.client.post('/api/events/risk-changed',json={'village_id':VILLAGE_ID,'mode':'replay','day':9,'hour_override':10}).json()
        for text in ('AIR 61 ASAP YA dekat kanal 4','YA'):
            self.assertEqual(self.client.post('/api/demo/inbound',json={'responder_id':'r1','text':text}).status_code,200)
        action=self.client.get('/api/actions').json()['items'][0]
        self.client.post('/api/actions/'+action['id']+'/approve',json={})
        record(self.app.state.database,'Warden','redaction_test',trace_id=alert['trace_id'],
               input={'phone':'+6281234567890','token':'secret-token','text':'raw text','mode':'replay'},output={'secret':'hidden'})
        response=self.client.get('/api/trace',params={'trace_id':alert['trace_id']})
        agents={item['agent'] for item in response.json()['items']}
        self.assertTrue({'Scout','Warden','Verifier','Planner','Operator'}<=agents)
        self.assertNotIn('6281234567890',response.text)
        self.assertNotIn('secret-token',response.text)
        self.assertNotIn('raw text',response.text)
        self.assertTrue(all(set(item)=={'id','agent','event','input','output','tool_calls','trace_id','created_at'} for item in response.json()['items']))
        plan=next(item for item in response.json()['items'] if item['agent']=='Planner')
        self.assertEqual(plan['tool_calls'][0]['arguments']['delta_hc_cm'],60)
        self.assertEqual(plan['tool_calls'][0]['result']['wt_gain_cm'],action['benefit']['wt_gain_cm'])

    def test_adaptation_retains_snapshot_and_is_idempotent(self):
        from .models import ObservationInput
        before=self.app.state.tools.get_village_status(VILLAGE_ID)
        observation=ObservationInput(village_id=VILLAGE_ID,responder_id='r1',wt_cm=before.wt_cm+5,confirmed=True)
        first=self.app.state.tools.log_field_observation(observation,observation_id='adaptive-test')
        self.assertTrue(first['recalibrated'])
        self.assertEqual(first['old']['wt_cm'],before.wt_cm)
        again=self.app.state.tools.log_field_observation(observation,observation_id='adaptive-test')
        self.assertEqual(first,again)
        with self.app.state.database.connect() as connection:
            snapshot=json.loads(connection.execute('SELECT pre_update_model FROM observations WHERE id=?',('adaptive-test',)).fetchone()['pre_update_model'])
        self.assertEqual(snapshot['wt_cm'],before.wt_cm)
        outlier=self.app.state.tools.log_field_observation(ObservationInput(village_id=VILLAGE_ID,responder_id='r1',wt_cm=140,confirmed=True))
        self.assertFalse(outlier['recalibrated'])
        self.assertEqual(outlier['reason'],'discrepancy_requires_verification')
        invalid=self.client.post('/api/observations',json={'village_id':VILLAGE_ID,'responder_id':'r1','wt_cm':55,'confirmed':False})
        self.assertEqual(invalid.status_code,422)

    def test_protected_scout_forces_mock_and_operator_routes_fail_closed(self):
        self.app.state.channels.settings.public_demo=False
        self.app.state.channels.settings.gateway_url='https://must-not-call.test'
        self.app.state.channels.settings.gateway_user='configured'
        self.app.state.channels.settings.gateway_pass='configured'
        with patch.object(self.app.state.channels.gateway,'send',side_effect=AssertionError('External send forbidden')):
            result=self.app.state.scout.manual(VILLAGE_ID,'replay',9,hour_override=10)
        self.assertEqual(result['notified'],1)
        private=create_app(db_path=Path(self.temp.name)/'private.sqlite3',public_demo=False,engine=self.engine)
        with TestClient(private) as client:
            self.assertEqual(client.get('/api/policy').status_code,401)
            self.assertEqual(client.get('/api/trace').status_code,401)
            self.assertEqual(client.post('/api/scout/tick',json={'mode':'replay','day':9}).status_code,401)
        self.assertFalse(private.state.config['scout_background'])

    def test_inflight_old_context_tick_cannot_reset_new_context(self):
        original=self.app.state.tools.get_village_status
        switched=[]
        def status(village_id,mode='live',day=0):
            if not switched:
                self.app.state.database.set('scout:active-context',{'mode':'live','day':0,'updated_at':self.now.timestamp()})
                switched.append(True)
            return original(village_id,mode,day).model_copy(update={'level':'EKSTREM'})
        with patch.object(self.app.state.tools,'get_village_status',side_effect=status):
            result=self.tick(9)
        self.assertEqual(result['reason'],'stale_context')
        self.assertEqual(result['notified'],0)
        self.assertIsNone(self.app.state.database.get('warden:r1'))


if __name__=='__main__':
    unittest.main()
