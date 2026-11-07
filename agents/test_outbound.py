from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import patch
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app
from .channels import ChannelAdapter,ChannelUnavailable,Inbound
from .mock_engine import VILLAGE_ID
from .tools import seed_mock


class OutboundTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.dict('os.environ',{'DEMO_PIN':'123456','SESSION_SECRET':'test-only-long-session-secret-value',
            'PEATFORGE_LIVE_CHANNELS':'true','CONTACT_ENCRYPTION_KEY':Fernet.generate_key().decode(),'PHONE_HASH_SALT':'h'*32,
            'SMS_GATEWAY_URL':'https://gateway.test/3rdparty/v1','SMS_GATEWAY_USER':'fake','SMS_GATEWAY_PASS':'fake'}))
        self.app=create_app(db_path=Path(self.temp.name)/'state.sqlite3',public_demo=False,engine=EngineService(Ingest()))
        self.client=self.enterContext(TestClient(self.app,base_url='http://localhost'))
        self.client.post('/api/auth/login',json={'pin':'123456'})
        seed_mock(self.app.state.database)
        self.channels=self.app.state.channels
        self.channels.contacts().bind('r1','081234567890',consent=True)
        self.requests=[]
        self.channels.gateway.transport=httpx.MockTransport(lambda request:self.requests.append(request) or httpx.Response(202,json={'id':'fake'}))
        self.app.state.voice.clock=lambda:datetime(2026,11,7,3,tzinfo=timezone.utc).timestamp()

    def propose(self,depth=61):
        self.app.state.warden.reset_for_alert('r1','replay',9,'t_outbound')
        for text in (f'AIR {depth} ASAP YA dekat kanal 4','YA'):
            self.channels.inbound(Inbound(responder_id='r1',channel='web',text=text,mode='replay',day=9))
        return next(item for item in self.app.state.planner.list()['items'] if item['status']=='proposed')

    def test_live_approval_commits_then_sends_once_and_preserves_operator_status(self):
        action=self.propose()
        result=self.client.post('/api/actions/'+action['id']+'/approve',json={})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual((result.json()['status'],result.json()['followup_status'],result.json()['approved_by']),('approved','queued','operator'))
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.client.post('/api/actions/'+action['id']+'/approve',json={}).json()['followup_status'],'queued')
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.client.post('/api/actions/'+action['id']+'/complete',json={'note':'Field completion confirmed'}).json()['status'],'done')
        self.assertEqual(self.client.post('/api/actions/'+action['id']+'/approve',json={}).json()['status'],'done')
        other=self.propose(62)
        self.assertEqual(self.client.post('/api/actions/'+other['id']+'/reject',json={'reason':'Need evidence'}).json()['status'],'rejected')
        self.assertEqual(len(self.requests),1)

    def test_unknown_provider_outcome_not_retried_and_progress_not_claimed(self):
        def timeout(request):
            self.requests.append(request)
            raise httpx.ReadTimeout('Outcome unknown')
        self.channels.gateway.transport=httpx.MockTransport(timeout)
        action=self.propose()
        first=self.client.post('/api/actions/'+action['id']+'/approve',json={})
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['status'],'approved')
        self.assertEqual(first.json()['followup_status'],'unknown')
        second=self.client.post('/api/actions/'+action['id']+'/approve',json={})
        self.assertEqual(second.json()['followup_status'],'unknown')
        self.assertEqual(len(self.requests),1)
        progress=self.app.state.planner.progress('r1')
        self.assertTrue(progress['steps'][3]['done'])
        self.assertFalse(progress['steps'][4]['done'])

    def test_missing_provider_fails_closed_without_fake_followup(self):
        action=self.propose()
        self.channels.settings.gateway_url=''
        result=self.client.post('/api/actions/'+action['id']+'/approve',json={})
        self.assertEqual(result.json()['status'],'approved')
        self.assertEqual(result.json()['followup_status'],'failed')
        self.assertEqual(len(self.requests),0)
        self.assertFalse(self.app.state.planner.progress('r1')['steps'][4]['done'])

    def test_concurrent_approval_reservation_avoids_duplicate_external_posts(self):
        entered,release=Event(),Event()
        def handler(request):
            self.requests.append(request)
            entered.set()
            release.wait(5)
            return httpx.Response(202,json={'id':'fake'})
        self.channels.gateway.transport=httpx.MockTransport(handler)
        action=self.propose()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.app.state.planner.transition,action['id'],'approve')
            self.assertTrue(entered.wait(3))
            second=pool.submit(self.app.state.planner.transition,action['id'],'approve')
            try:
                duplicate=second.result(timeout=3)
                self.assertEqual(duplicate['status'],'approved')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3)['followup_status'],'queued')
        self.assertEqual(len(self.requests),1)
        stored=next(item for item in self.app.state.planner.list()['items'] if item['id']==action['id'])
        self.assertEqual(stored['followup_status'],'queued')

    def test_pending_outbox_can_resume_after_commit_but_ambiguous_attempt_cannot(self):
        with self.app.state.database.connect() as connection:
            message=self.channels.enqueue('r1','test','sms',idempotency_key='m_resumable',connection=connection)
        recovered=ChannelAdapter(self.app.state.database,self.channels.settings,transport=self.channels.gateway.transport)
        recovered.outbound_policy=self.channels.outbound_policy
        self.assertEqual(recovered.dispatch(message)['status'],'queued')
        self.assertEqual(recovered.dispatch(message)['status'],'queued')
        self.assertEqual(len(self.requests),1)

    def test_manual_sms_policy_consent_length_and_config_metadata(self):
        self.app.state.warden.reset_for_alert('r1','replay',9,'t_manual')
        result=self.client.post('/api/responders/r1/send',json={'text':'Uji coba Peatforge'})
        self.assertEqual(result.json()['status'],'queued')
        self.assertEqual(self.client.post('/api/responders/r1/send',json={'text':'x'*301}).status_code,422)
        self.app.state.voice.clock=lambda:datetime(2026,11,7,15,tzinfo=timezone.utc).timestamp()
        suppressed=self.client.post('/api/responders/r1/send',json={'text':'Uji coba'})
        self.assertEqual(suppressed.json()['status'],'suppressed')
        self.assertEqual(len(self.requests),1)
        self.channels.settings.force_mock=True
        config=self.client.get('/api/config').json()
        self.assertEqual(config['outbound'],'mock')

    def test_followup_and_cli_style_dispatch_enforce_live_quiet_policy(self):
        action=self.propose()
        self.app.state.voice.clock=lambda:datetime(2026,11,7,15,tzinfo=timezone.utc).timestamp()
        response=self.client.post('/api/actions/'+action['id']+'/approve',json={})
        self.assertEqual(response.json()['status'],'approved')
        self.assertEqual(response.json()['followup_status'],'suppressed')
        self.assertFalse(self.app.state.planner.progress('r1')['steps'][4]['done'])
        with self.app.state.database.connect() as connection:
            message=self.channels.enqueue('r1','manual CLI-style SMS','sms',connection=connection)
        self.assertEqual(self.channels.dispatch(message)['status'],'suppressed')
        self.assertEqual(len(self.requests),0)


if __name__=='__main__':
    unittest.main()
