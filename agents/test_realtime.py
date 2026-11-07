import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app
from .realtime import TTL


class RealtimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.dict('os.environ',{'OPENAI_API_KEY':'sk-test-master-key-not-real',
            'OPENAI_REALTIME_MODEL':'','OPENAI_REALTIME_VOICE':'','PEATFORGE_LIVE_CHANNELS':''}))
        self.app=create_app(db_path=Path(self.temp.name)/'state.sqlite3',public_demo=True,engine=EngineService(Ingest()))
        self.client=self.enterContext(TestClient(self.app,base_url='http://localhost'))
        self.service=self.app.state.browser_voice
        self.now=10000
        self.service.clock=lambda:self.now
        self.requests=[]
        self.service.transport=httpx.MockTransport(lambda request:self.requests.append(request) or httpx.Response(200,json={'value':'ek_test_ephemeral','expires_at':self.now+60}))
        self.app.state.warden.reset_for_alert('r1','replay',9,'t_browser_test')

    def mint(self,headers=None,body=None):
        result=self.client.post('/api/voice/session',json=body or {'responder_id':'r1','mode':'replay','day':9},headers=headers or {})
        self.assertEqual(result.status_code,200,result.text)
        return result.json()['session_id']

    def transcript(self,sid,event,text,role='user',headers=None):
        return self.client.post('/api/voice/transcript',json={'session_id':sid,'event_id':event,'role':role,'text':text},headers=headers or {})

    def tool(self,sid,call,name,args=None,headers=None):
        return self.client.post('/api/voice/tool',json={'session_id':sid,'call_id':call,'name':name,'arguments':args or {}},headers=headers or {})

    def test_mint_whitelists_ephemeral_payload_and_never_persists_secret(self):
        sid=self.mint()
        self.assertEqual(len(self.requests),1)
        request=self.requests[0]
        self.assertEqual(str(request.url),'https://api.openai.com/v1/realtime/client_secrets')
        payload=json.loads(request.content)
        self.assertEqual(payload['expires_after'],{'anchor':'created_at','seconds':60})
        self.assertEqual(payload['session']['model'],'gpt-realtime-2.1')
        self.assertEqual(payload['session']['audio']['output']['voice'],'marin')
        self.assertEqual(payload['session']['audio']['input']['turn_detection'],{'type':'server_vad','create_response':False,'interrupt_response':False})
        self.assertEqual({tool['name'] for tool in payload['session']['tools']},{'get_village_status','record_report','confirm_report','end_call'})
        self.assertTrue(len(request.headers['OpenAI-Safety-Identifier'])==64)
        with self.app.state.database.connect() as connection:
            data=' '.join(row['value'] for row in connection.execute('SELECT value FROM kv'))
        self.assertTrue('ek_test_ephemeral' not in data and 'sk-test-master-key-not-real' not in data)
        self.assertTrue(sid.startswith('bv_'))

    def test_missing_key_provider_error_and_malformed_response_fail_generic503(self):
        self.service.settings.key=''
        result=self.client.post('/api/voice/session',json={'responder_id':'r1'})
        self.assertEqual(result.status_code,503)
        self.assertEqual(len(self.requests),0)
        self.service.settings.key='sk-test-master-key-not-real'
        for status,body in ((503,{'error':'sk-test-master-key-not-real'}),(200,{'value':'sk-test-master-key-not-real','expires_at':self.now+60}),
                            (200,{'value':'ek_fake','expires_at':self.now-1})):
            self.service.transport=httpx.MockTransport(lambda request:httpx.Response(status,json=body))
            response=self.client.post('/api/voice/session',json={'responder_id':'r1'})
            self.assertEqual(response.status_code,503)
            self.assertTrue('sk-test-master-key-not-real' not in response.text)

    def test_field_header_wins_forces_scope_and_invalid_never_falls_through(self):
        token=self.client.get('/api/responders/r1/field-link').json()['field_path'].split('/')[-1]
        other=self.client.get('/api/responders/r2/field-link').json()['field_path'].split('/')[-1]
        headers={'X-Field-Token':token}
        sid=self.mint(headers,{'responder_id':'r2','mode':'live','day':0})
        status=self.tool(sid,'status','get_village_status',headers=headers)
        self.assertEqual(status.json()['village_id'],'62.11.01.2002')
        self.assertEqual((status.json()['mode'],status.json()['day']),('replay',9))
        self.assertEqual(self.tool(sid,'other','get_village_status',headers={'X-Field-Token':other}).status_code,403)
        self.assertEqual(self.client.post('/api/voice/session',json={'responder_id':'r1'},headers={'X-Field-Token':'invalid'}).status_code,404)
        self.assertEqual(self.client.post('/api/voice/session',json={'responder_id':'r1'},headers={'X-Field-Token':''}).status_code,404)
        self.assertEqual(self.tool(sid,'foreign','get_village_status',{'village_id':'another'},headers).status_code,422)

    def test_field_mint_rate_five_per_minute_and_no_auto_calls_on_capabilities(self):
        token=self.client.get('/api/responders/r1/field-link').json()['field_path'].split('/')[-1]
        self.assertTrue(self.client.get('/api/config').json()['browser_voice_supported'])
        self.assertTrue(self.client.get('/api/field/'+token+'/messages').json()['browser_voice_supported'])
        self.assertEqual(len(self.requests),0)
        for unused in range(5):
            sid=self.mint({'X-Field-Token':token})
            self.assertEqual(self.client.post('/api/voice/end',json={'session_id':sid},headers={'X-Field-Token':token}).status_code,200)
        self.assertEqual(self.client.post('/api/voice/session',json={},headers={'X-Field-Token':token}).status_code,429)
        self.assertEqual(len(self.requests),5)

    def test_full_transcript_gated_tool_flow_dedupes_and_ignores_no_model_numbers(self):
        sid=self.mint()
        self.assertEqual(self.tool(sid,'premature','record_report').status_code,409)
        for index,text in enumerate(('enam satu senti','terlihat asap','kanal empat')):
            event='user'+str(index)
            self.assertTrue(self.transcript(sid,event,text).json()['accepted'])
            if index==0:
                self.assertEqual(self.tool(sid,'invented','record_report',{'wt_cm':99,'responder_id':'r2'}).status_code,422)
            response=self.tool(sid,'call'+str(index),'record_report')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(self.tool(sid,'call'+str(index),'record_report').json(),response.json())
        self.assertEqual(response.json()['stage'],'confirm')
        self.assertEqual(self.tool(sid,'no-new-user','confirm_report').status_code,409)
        self.assertEqual(self.client.get('/api/observations').json()['items'],[])
        self.assertTrue(self.transcript(sid,'readback',response.json()['text'],'assistant').json()['accepted'])
        self.assertTrue(self.transcript(sid,'confirmation','YA').json()['accepted'])
        saved=self.tool(sid,'confirm-call','confirm_report')
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(saved.json()['state'],'saved')
        self.assertEqual(self.tool(sid,'confirm-call','confirm_report').json(),saved.json())
        observations=self.client.get('/api/observations').json()['items']
        self.assertEqual(len(observations),1)
        self.assertEqual((observations[0]['wt_cm'],observations[0]['smoke'],observations[0]['day']),(61,True,9))
        self.assertTrue(self.client.get('/api/actions').json()['items'])
        self.assertTrue(self.transcript(sid,'thanks','Terima kasih. Laporan dicatat.','assistant').json()['accepted'])
        self.assertEqual(self.client.post('/api/voice/end',json={'session_id':sid}).json(),{'ended':True})
        self.assertEqual(self.client.post('/api/voice/end',json={'session_id':sid}).json(),{'ended':True})

    def test_qualitative_dispatcher_requires_readback_then_new_confirmation(self):
        sid=self.mint()
        intro=self.tool(sid,'intro','get_village_status').json()['text']
        self.assertIn('asisten AI uji coba Peatforge',intro)
        self.assertEqual(intro.count('?'),1)
        self.assertIn('Ada waktu sebentar?',intro)
        for index,text in enumerate(('YA','kering','bau terbakar','Handil milik warga')):
            self.assertTrue(self.transcript(sid,'qual-user'+str(index),text).json()['accepted'])
            response=self.tool(sid,'qual-tool'+str(index),'record_report')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['text'].count('?'),1)
            self.assertNotIn('Berapa',response.json()['text'])
        self.assertEqual(response.json()['stage'],'confirm')
        self.transcript(sid,'premature-yes','YA')
        self.assertEqual(self.tool(sid,'premature-qual','confirm_report').status_code,409)
        self.assertTrue(self.transcript(sid,'qual-readback',response.json()['text'],'assistant').json()['accepted'])
        self.assertEqual(self.tool(sid,'same-old-yes','confirm_report').status_code,409)
        self.transcript(sid,'new-qual-yes','YA')
        saved=self.tool(sid,'save-qual','confirm_report')
        self.assertEqual(saved.json()['state'],'saved')
        obs=self.client.get('/api/observations').json()['items'][0]
        self.assertIsNone(obs['wt_cm'])
        self.assertEqual(obs['smoke_category'],'bau')
        self.assertFalse(obs.get('fire'))
        self.assertEqual(obs['place_text'],'Handil milik warga')
        self.assertEqual(self.client.get('/api/actions').json()['items'][0]['verifier']['confidence_label'],'LOW')
        self.assertEqual(len(self.requests),1)

    def test_assistant_numeric_guard_rejects_invented_numbers_and_urls_without_storing(self):
        sid=self.mint()
        bad=self.transcript(sid,'bad','Air 999 cm pasti aman.','assistant')
        self.assertEqual(bad.json(),{'accepted':False,'reason':'numeric_guard'})
        self.assertEqual(self.transcript(sid,'url','Buka https://evil.test','assistant').json()['accepted'],False)
        self.transcript(sid,'user','61 senti')
        self.tool(sid,'record','record_report')
        self.assertFalse(self.transcript(sid,'unapproved','Air enam puluh satu sentimeter.','assistant').json()['accepted'])
        self.assertFalse(self.transcript(sid,'wrong','Air enam puluh dua sentimeter.','assistant').json()['accepted'])
        self.assertFalse(self.transcript(sid,'english','Water is sixty two centimeters.','assistant').json()['accepted'])
        messages=self.client.get('/api/responders/r1/messages').json()['items']
        self.assertFalse(any('999' in item['body'] or 'evil.test' in item['body'] for item in messages))
        for index,text in enumerate(('Kedalaman air seribu sentimeter.','Water depth is one thousand centimeters.','Air sejuta senti.','Air seperempat sentimeter.')):
            self.assertEqual(self.transcript(sid,'large'+str(index),text,'assistant').json(),{'accepted':False,'reason':'numeric_guard'})

    def test_offline_simulation_no_key_same_pipeline_bye_and_event_dedupe(self):
        self.service.settings.key=''
        session=None
        for index,text in enumerate(('61 senti','terlihat asap','kanal empat','YA')):
            body={'responder_id':'r1','mode':'replay','day':9,'event_id':'sim'+str(index),'text':text}
            if session:
                body['session_id']=session
            response=self.client.post('/api/voice/simulate',json=body)
            self.assertEqual(response.status_code,200,response.text)
            session=response.json()['session_id']
        self.assertEqual(response.json()['state'],'saved')
        self.assertEqual(len(self.client.get('/api/observations').json()['items']),1)
        self.assertEqual(self.client.post('/api/voice/simulate',json=body).json()['state'],'saved')
        self.assertEqual(len(self.requests),0)
        body.update(text='changed')
        self.assertEqual(self.client.post('/api/voice/simulate',json=body).status_code,409)
        bye=self.client.post('/api/voice/simulate',json={'responder_id':'r2','event_id':'bye','text':'matiin ya'})
        self.assertTrue(bye.json()['ended'])
        self.assertEqual(len(self.client.get('/api/observations').json()['items']),1)

    def test_scope_expiry_transcript_conflicts_and_microphone_policy(self):
        sid=self.mint()
        self.transcript(sid,'e','61 senti')
        self.assertTrue(self.transcript(sid,'e','61 senti').json()['duplicate'])
        self.assertEqual(self.transcript(sid,'e','99 senti').status_code,409)
        self.now+=TTL+1
        self.assertEqual(self.tool(sid,'expired','record_report').status_code,404)
        headers=self.client.get('/api/config').headers
        self.assertIn('https://api.openai.com',headers['content-security-policy'])
        self.assertIn('microphone=(self)',headers['permissions-policy'])

    def test_failed_confirm_hook_resumes_same_observation_and_proposal(self):
        from .channels import ChannelUnavailable
        sid=self.mint()
        for index,text in enumerate(('61 senti','terlihat asap','kanal empat')):
            self.transcript(sid,'u'+str(index),text)
            readback=self.tool(sid,'c'+str(index),'record_report')
        self.assertTrue(self.transcript(sid,'assistant-readback',readback.json()['text'],'assistant').json()['accepted'])
        self.transcript(sid,'confirm','YA')
        process=self.app.state.planner.process
        calls=[]
        def hook(observation,saved):
            calls.append(saved['observation_id'])
            result=process(observation,saved)
            if len(calls)==1:
                raise ChannelUnavailable('temporary')
            return result
        self.app.state.warden.after_confirm=hook
        self.assertEqual(self.tool(sid,'confirm-call','confirm_report').status_code,503)
        response=self.tool(sid,'confirm-call','confirm_report')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['state'],'saved')
        self.assertEqual(len(self.client.get('/api/observations').json()['items']),1)
        self.assertTrue(calls[0]==calls[1])

    def test_confirmation_requires_exact_validated_readback_then_new_user_turn(self):
        sid=self.mint()
        for index,text in enumerate(('61 senti','terlihat asap','kanal empat')):
            self.transcript(sid,'u'+str(index),text)
            readback=self.tool(sid,'c'+str(index),'record_report')
        self.transcript(sid,'early-yes','YA')
        self.assertEqual(self.tool(sid,'missing-readback','confirm_report').status_code,409)
        self.assertFalse(self.transcript(sid,'generic','Laporan Anda sudah saya baca.','assistant').json()['accepted'])
        self.assertFalse(self.transcript(sid,'swapped','Baca ulang: air 61 sentimeter, asap tidak ada, kanal empat.','assistant').json()['accepted'])
        self.assertEqual(self.client.get('/api/observations').json()['items'],[])
        spoken=readback.json()['text'].replace('61','enam puluh satu')
        self.assertTrue(self.transcript(sid,'correct-readback',spoken,'assistant').json()['accepted'])
        self.assertEqual(self.tool(sid,'still-old-yes','confirm_report').status_code,409)
        self.transcript(sid,'new-yes','YA')
        self.assertEqual(self.tool(sid,'valid-confirm','confirm_report').json()['state'],'saved')
        self.assertEqual(len(self.client.get('/api/observations').json()['items']),1)

    def test_simulation_cancel_then_new_report_and_private_auth_fail_closed(self):
        session=None
        for index,text in enumerate(('61','terlihat asap','kanal empat','TIDAK','70','tidak','kanal empat','YA')):
            body={'responder_id':'r1','mode':'replay','day':9,'event_id':'restart'+str(index),'text':text,'session_id':session}
            response=self.client.post('/api/voice/simulate',json=body)
            self.assertEqual(response.status_code,200,response.text)
            session=response.json()['session_id']
        observations=self.client.get('/api/observations').json()['items']
        self.assertEqual(len(observations),1)
        self.assertEqual(observations[0]['wt_cm'],70)
        private=create_app(db_path=Path(self.temp.name)/'private.sqlite3',public_demo=False,engine=EngineService(Ingest()))
        with TestClient(private,base_url='http://localhost') as client:
            self.assertEqual(client.post('/api/voice/session',json={'responder_id':'r1'}).status_code,401)
            self.assertEqual(client.post('/api/voice/simulate',json={'responder_id':'r1','event_id':'x','text':'61'}).status_code,401)


if __name__=='__main__':
    unittest.main()
