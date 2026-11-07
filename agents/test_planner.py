from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from engine.cache import DataUnavailable, DiskCache
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app
from .channels import ChannelUnavailable, FIXTURE_SENDER
from .mock_engine import VILLAGE_ID
from .planner import followup_text, has_benefit, verify_benefit


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = EngineService(Ingest())
        self.app = create_app(db_path=Path(self.temp.name)/'state.sqlite3', public_demo=True, engine=self.engine)
        self.client = self.enterContext(TestClient(self.app))

    def send(self, text, day=9):
        result = self.client.post('/api/demo/inbound', json={'responder_id':'r1','text':text,'mode':'replay','day':day})
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def report(self, depth=61, day=9):
        self.send(f'AIR {depth} ASAP YA dekat kanal 4',day)
        self.send('YA',day)
        return self.client.get('/api/actions').json()['items']

    def test_full_mock_loop_approval_once_and_reject(self):
        alert = self.client.post('/api/events/risk-changed', json={'village_id':VILLAGE_ID,'mode':'replay','day':9})
        self.assertEqual(alert.status_code,200)
        readback = self.send('AIR 61 ASAP YA dekat kanal 4')
        self.assertEqual(readback['state'],'confirm')
        self.assertEqual(self.client.get('/api/actions').json()['items'],[])
        self.send('YA')
        actions = self.client.get('/api/actions').json()['items']
        self.assertGreaterEqual(len(actions),1)
        self.assertLessEqual(len(actions),2)
        action = next(item for item in actions if item['type']=='canal_block')
        self.assertEqual(action['simulation_request']['delta_hc_cm'],60)
        expected = self.engine.simulate(VILLAGE_ID,action['simulation_request'])
        self.assertEqual(action['days_bought'],expected['days_bought'])
        self.assertEqual(action['benefit']['wt_gain_cm'],expected['wt_gain_cm'])
        self.assertEqual(action['verifier']['decision'],'forward_with_flag')
        self.assertEqual(action['verifier']['checks'][2]['mode'],'replay')
        for unused in range(2):
            approved = self.client.post('/api/actions/'+action['id']+'/approve',json={})
            self.assertEqual(approved.status_code,200,approved.text)
            self.assertEqual(approved.json()['status'],'approved')
        messages = self.client.get('/api/responders/r1/messages').json()['items']
        followups = [item for item in messages if item['id']=='m_followup_'+action['id']]
        self.assertEqual(len(followups),1)
        self.assertIn('jika dilaksanakan',followups[0]['body'])
        self.assertNotIn('aman',followups[0]['body'].lower())
        progress = self.client.get('/api/demo/progress?responder_id=r1').json()
        self.assertTrue(all(step['done'] for step in progress['steps']))
        self.assertEqual((progress['mode'],progress['day']),('replay',9))
        newer = self.report(depth=62)
        proposed = next(item for item in newer if item['status']=='proposed')
        rejected = self.client.post('/api/actions/'+proposed['id']+'/reject',json={'reason':'Perlu bukti'})
        self.assertEqual(rejected.json()['status'],'rejected')
        self.assertEqual(self.client.post('/api/actions/'+proposed['id']+'/approve',json={}).status_code,409)

    def test_discrepancy_hold_creates_visible_verification_item(self):
        with patch.object(self.engine,'simulate',side_effect=AssertionError('Hold should not plan field action')):
            actions = self.report(depth=140)
        self.assertEqual(len(actions),1)
        action = actions[0]
        self.assertEqual((action['kind'],action['type'],action['status']),('verify','dispatch_verify','proposed'))
        self.assertEqual(action['verifier']['decision'],'hold')
        self.assertGreater(action['verifier']['checks'][1]['difference_cm'],30)
        self.assertEqual(self.client.post('/api/actions/'+action['id']+'/approve',json={}).json()['status'],'approved')

    def test_expiry_and_superseding_only_proposed(self):
        first = self.report()[0]
        second = self.report(depth=62)
        all_items = self.client.get('/api/actions?include_old=true').json()['items']
        self.assertEqual(next(item for item in all_items if item['id']==first['id'])['status'],'superseded')
        self.assertGreater(self.client.get('/api/actions').json()['hidden_count'],0)
        future = datetime.now(timezone.utc)+timedelta(hours=7)
        self.app.state.planner.clock = lambda: future
        active = self.client.get('/api/actions').json()
        self.assertEqual(active['items'],[])
        self.assertTrue(all(item['status'] in ('expired','superseded') for item in self.client.get('/api/actions?include_old=true').json()['items']))

    def test_null_lower_bound_zero_and_exact_benefit_guard(self):
        result = {'days_bought':None,'at_least_days':4,'reason':'scenario_beyond_horizon','wt_gain_cm':3.25,'pfvi_reduction':5.5}
        self.assertTrue(verify_benefit(result.copy(),result))
        self.assertFalse(verify_benefit({**result,'days_bought':0},result))
        self.assertTrue(has_benefit(result))
        self.assertTrue(has_benefit({**result,'at_least_days':None}))
        self.assertFalse(has_benefit(dict(days_bought=0,at_least_days=None,wt_gain_cm=0,pfvi_reduction=0)))
        action = dict(kind='proposal',days_bought=None,at_least_days=4,benefit={'wt_gain_cm':3.25,'pfvi_reduction':5.5})
        self.assertIn('setidaknya 4 hari',followup_text(action))
        self.assertLessEqual(len(followup_text(action)),300)

    def test_partial_hook_failure_does_not_duplicate_actions(self):
        self.send('AIR 61 ASAP YA dekat kanal 4')
        process = self.app.state.planner.process
        attempts = []
        def hook(observation,saved):
            ids = process(observation,saved)
            attempts.append(ids)
            if len(attempts)==1:
                raise ChannelUnavailable('retry')
            return ids
        self.app.state.warden.after_confirm = hook
        self.app.state.channels.settings.webhook_secret='s'*32
        payload={'event':'sms:received','payload':{'sender':FIXTURE_SENDER,'message':'YA','messageId':'partial-plan'}}
        url='/api/channels/sms/inbound?key='+'s'*32
        self.assertEqual(self.client.post(url,json=payload).status_code,503)
        self.assertEqual(self.client.post(url,json=payload).status_code,200)
        self.assertEqual(attempts[0],attempts[1])
        self.assertEqual(len(self.client.get('/api/observations').json()['items']),1)
        self.assertEqual(len(self.client.get('/api/actions?include_old=true').json()['items']),len(attempts[0]))

    def test_browser_retry_resumes_saved_hook_with_new_receipt(self):
        self.send('AIR 61 ASAP YA dekat kanal 4')
        process=self.app.state.planner.process
        calls=[]
        def hook(observation,saved):
            calls.append(saved['observation_id'])
            if len(calls)==1:
                raise ChannelUnavailable('temporary planner failure')
            return process(observation,saved)
        self.app.state.warden.after_confirm=hook
        failed=self.client.post('/api/demo/inbound',json={'responder_id':'r1','text':'YA','mode':'replay','day':9})
        self.assertEqual(failed.status_code,503)
        self.send('YA')
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(len(self.client.get('/api/observations').json()['items']),1)
        self.assertGreater(len(self.client.get('/api/actions').json()['items']),0)

    def test_actual_cached_replay_computes_one_day_in_temporary_state(self):
        cache_path=Path(__file__).resolve().parent.parent/'runtime'/'engine'/'http'
        def offline(url,timeout):
            raise OSError('Network disabled for cached integration check')
        engine=EngineService(Ingest(DiskCache(cache_path,fetcher=offline,retries=0),mode='live'))
        try:
            status=engine.village_status(VILLAGE_ID,'replay',9)
        except DataUnavailable:
            self.skipTest('Actual provider cache unavailable; no network fallback')
        app=create_app(db_path=Path(self.temp.name)/'cached.sqlite3',public_demo=True,engine=engine)
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/events/risk-changed',json={'village_id':VILLAGE_ID,'mode':'replay','day':9}).status_code,200)
            for text in ('AIR 61 ASAP YA dekat kanal 4','YA'):
                response=client.post('/api/demo/inbound',json={'responder_id':'r1','text':text,'mode':'replay','day':9})
                self.assertEqual(response.status_code,200,response.text)
            action=next(item for item in client.get('/api/actions').json()['items'] if item['type']=='canal_block')
            self.assertEqual(status['source'],'modelled')
            self.assertEqual(action['days_bought'],1)
            self.assertEqual(client.post('/api/actions/'+action['id']+'/approve',json={}).json()['status'],'approved')
            followups=[item for item in client.get('/api/responders/r1/messages').json()['items'] if item['id']=='m_followup_'+action['id']]
            self.assertEqual(len(followups),1)
            self.assertIn('1 hari',followups[0]['body'])

    def test_static_assets_spa_and_path_boundaries(self):
        dist=Path(self.temp.name)/'dist'
        (dist/'assets').mkdir(parents=True)
        (dist/'index.html').write_text('<html>demo</html>',encoding='utf-8')
        (dist/'assets'/'app.js').write_text('export {}',encoding='utf-8')
        app=create_app(db_path=Path(self.temp.name)/'static.sqlite3',public_demo=True,engine=self.engine,web_dist=dist)
        with TestClient(app) as client:
            self.assertEqual(client.get('/').status_code,200)
            self.assertEqual(client.get('/village/example').text,'<html>demo</html>')
            self.assertIn('application/javascript',client.get('/assets/app.js').headers['content-type'])
            for path in ('/api/missing','/.env','/AGENTS.md','/engine/service.py','/%2e%2e/AGENTS.md','/assets/missing.js'):
                self.assertEqual(client.get(path).status_code,404,path)

    def test_omitted_context_inherits_alert_and_progress_resets_for_next_run(self):
        for day in (9,12):
            alert=self.client.post('/api/events/risk-changed',json={'village_id':VILLAGE_ID,'mode':'replay','day':day})
            self.assertEqual(alert.status_code,200)
            progress=self.client.get('/api/demo/progress?responder_id=r1').json()
            self.assertEqual([step['done'] for step in progress['steps']],[True,False,False,False,False])
            self.assertEqual((progress['mode'],progress['day']),('replay',day))
            for text in ('AIR 61 ASAP YA dekat kanal 4','YA'):
                response=self.client.post('/api/demo/inbound',json={'responder_id':'r1','text':text})
                self.assertEqual(response.status_code,200,response.text)
            progress=self.client.get('/api/demo/progress?responder_id=r1').json()
            action=next(item for item in self.client.get('/api/actions').json()['items'] if item['id']==progress['action_id'])
            self.assertEqual((action['mode'],action['day']),('replay',day))
            self.assertEqual(self.client.post('/api/actions/'+action['id']+'/approve',json={}).status_code,200)
            self.assertTrue(all(step['done'] for step in self.client.get('/api/demo/progress?responder_id=r1').json()['steps']))

    def test_original_confirmation_retry_keeps_original_alert_trace(self):
        original_alert=self.client.post('/api/events/risk-changed',json={'village_id':VILLAGE_ID,'mode':'replay','day':9}).json()
        self.send('AIR 61 ASAP YA dekat kanal 4')
        process=self.app.state.planner.process
        with patch.object(self.app.state.warden,'after_confirm',side_effect=ChannelUnavailable('temporary')):
            self.app.state.channels.settings.webhook_secret='s'*32
            payload={'event':'sms:received','payload':{'sender':FIXTURE_SENDER,'message':'YA','messageId':'original-trace'}}
            url='/api/channels/sms/inbound?key='+'s'*32
            self.assertEqual(self.client.post(url,json=payload).status_code,503)
        new_alert=self.client.post('/api/events/risk-changed',json={'village_id':VILLAGE_ID,'mode':'replay','day':12}).json()
        self.assertNotEqual(original_alert['trace_id'],new_alert['trace_id'])
        self.send('AIR 62 ASAP YA dekat kanal 4',day=12)
        self.assertEqual(self.client.post(url,json=payload).status_code,200)
        actions=self.client.get('/api/actions').json()['items']
        self.assertTrue(actions)
        self.assertTrue(all(action['trace_id']==original_alert['trace_id'] and action['day']==9 for action in actions))
        progress=self.client.get('/api/demo/progress?responder_id=r1').json()
        self.assertEqual([step['done'] for step in progress['steps']],[True,False,False,False,False])

    def test_live_and_replay_proposals_do_not_supersede_each_other(self):
        replay=self.report()
        replay_ids={item['id'] for item in replay}
        for text in ('AIR 61 ASAP YA dekat kanal 4','YA'):
            response=self.client.post('/api/demo/inbound',json={'responder_id':'r1','text':text,'mode':'live','day':0})
            self.assertEqual(response.status_code,200,response.text)
        items=self.client.get('/api/actions?include_old=true').json()['items']
        self.assertTrue(any(item['mode']=='live' and item['status']=='proposed' for item in items))
        self.assertTrue(all(item['status']=='proposed' for item in items if item['id'] in replay_ids))


if __name__=='__main__':
    unittest.main()
