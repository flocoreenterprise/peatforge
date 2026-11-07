from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime,timedelta,timezone
from io import StringIO
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlencode,urlsplit
import xml.etree.ElementTree as ET
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app
from .channels import ChannelUnavailable
from .cli_voice import main as voice_main
from .mock_engine import VILLAGE_ID
from .scout import WIB
from .tools import seed_mock
from .voice import SID_TTL,VoiceSettings,sign_session,twilio_signature,verify_session


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env=self.enterContext(patch.dict('os.environ',{
            'PUBLIC_DEMO':'false','PEATFORGE_LIVE_CHANNELS':'true','PEATFORGE_DATA_SOURCE':'fixture',
            'DEMO_PIN':'123456','SESSION_SECRET':'test-session-secret-longer-than-32-characters',
            'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'b'*32,'TWILIO_FROM_NUMBER':'+12025550123',
            'PUBLIC_BASE_URL':'https://voice.test','UAT_PHONE_NUMBER':'+6281234567890',
            'CONTACT_ENCRYPTION_KEY':Fernet.generate_key().decode(),'PHONE_HASH_SALT':'h'*32,
            'SMS_GATEWAY_URL':'https://gateway.test/3rdparty/v1','SMS_GATEWAY_USER':'fake','SMS_GATEWAY_PASS':'fake'}))
        self.engine=EngineService(Ingest())
        self.app=create_app(db_path=Path(self.temp.name)/'private.sqlite3',public_demo=False,engine=self.engine)
        self.client=self.enterContext(TestClient(self.app,base_url='http://localhost'))
        seed_mock(self.app.state.database)
        self.app.state.channels.contacts().bind('r1','081234567890',consent=True)
        self.now=datetime(2026,11,7,3,tzinfo=timezone.utc)
        self.voice=self.app.state.voice
        self.voice.clock=lambda:self.now.timestamp()
        self.app.state.scout.clock=lambda:self.now
        self.requests=[]
        self.sms=[]
        self.provider_sid='CA'+'c'*32
        self.voice.transport=httpx.MockTransport(lambda request:self.requests.append(request) or httpx.Response(201,json={'sid':self.provider_sid}))
        self.app.state.channels.gateway.transport=httpx.MockTransport(lambda request:self.sms.append(request) or httpx.Response(202,json={'id':'fake-sms'}))
        self.client.post('/api/auth/login',json={'pin':'123456'})

    def call(self,diagnostic=False,level='TINGGI'):
        original=self.app.state.tools.get_village_status
        def status(*args,**kwargs):
            return original(*args,**kwargs).model_copy(update={'level':level})
        with patch.object(self.app.state.tools,'get_village_status',side_effect=status):
            return self.voice.call('r1',diagnostic)

    def sid(self,diagnostic=False):
        self.app.state.warden.reset_for_alert('r1','replay',9,'t_voice_test')
        return sign_session(self.voice.settings,'r1',diagnostic=diagnostic,now=self.voice.clock())

    def callback(self,kind,sid, *,turn=None,speech=None,signature=None,params=None):
        path='/api/voice/twilio/'+('status' if kind=='status' else 'twiml/'+kind)
        query={'sid':sid}
        if turn is not None:
            query['turn']=turn
        body=params or {}
        if speech is not None:
            body={**body,'SpeechResult':speech}
        headers={} if signature is None else {'X-Twilio-Signature':signature}
        return self.client.post(path+'?'+urlencode(query),data=body,headers=headers)

    def test_strong_sid_stateless_ttl_tampering_and_legacy_rejection(self):
        sid=self.sid()
        parts=sid.split('.')
        self.assertEqual((len(parts[3]),len(parts[5])),(32,64))
        independent=VoiceSettings(auth_token='b'*32)
        self.assertEqual(verify_session(independent,sid,now=self.voice.clock())['responder_id'],'r1')
        self.assertIsNone(verify_session(independent,sid,now=self.voice.clock()+SID_TTL+1))
        self.assertIsNone(verify_session(independent,sid[:-1]+('0' if sid[-1]!='0' else '1'),now=self.voice.clock()))
        self.assertIsNone(verify_session(independent,'r1-deadbeefdeadbeef',now=self.voice.clock()))
        self.assertIsNone(verify_session(VoiceSettings(),sid,now=self.voice.clock()))

    def test_signature_full_public_url_raw_query_sorted_form_and_wrong_header(self):
        sid=self.sid()
        path='/api/voice/twilio/twiml/start'
        query='sid='+sid+'&label=space+one'
        params={'CallSid':['CA'+'d'*32],'AccountSid':['AC'+'a'*32]}
        signature=twilio_signature('b'*32,'https://voice.test'+path+'?'+query,params)
        valid=self.client.post(path+'?'+query,data={key:value[0] for key,value in params.items()},headers={'X-Twilio-Signature':signature,'Host':'spoof.test'})
        self.assertEqual(valid.status_code,200)
        self.assertEqual(self.callback('start',sid,signature='wrong').status_code,403)
        self.assertEqual(self.callback('start',sid,signature='').status_code,403)
        self.assertEqual(self.callback('start',sid).status_code,200)
        self.assertEqual(self.callback('start','r1-deadbeefdeadbeef').status_code,403)
        self.voice.settings.auth_token=''
        self.assertEqual(self.callback('start',sid).status_code,403)

    def test_twiml_pause_first_greeting_inside_gather_and_escaped_xml(self):
        result=self.callback('start',self.sid())
        self.assertEqual(result.status_code,200)
        self.assertIn('application/xml',result.headers['content-type'])
        root=ET.fromstring(result.text)
        self.assertEqual(root[0].tag,'Pause')
        gather=root.find('Gather')
        self.assertEqual(gather.attrib['input'],'speech')
        self.assertEqual(gather.attrib['language'],'id-ID')
        self.assertIn('uji coba Peatforge',gather.find('Say').text)
        self.assertNotIn('BPBD',result.text)
        self.assertIn('actionOnEmptyResult',gather.attrib)

    def test_normal_voice_loop_uses_warden_planner_and_voice_transcripts(self):
        sid=self.sid()
        self.assertEqual(self.callback('start',sid).status_code,200)
        for turn,speech,expected in ((0,'enam satu senti','asap'),(1,'terlihat asap','Kira-kira'),(2,'kanal empat','Saya baca ulang')):
            response=self.callback('gather',sid,turn=turn,speech=speech)
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn(expected,response.text)
        self.assertEqual(self.client.get('/api/observations').json()['items'],[])
        saved=self.callback('gather',sid,turn=3,speech='YA')
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertIsNotNone(ET.fromstring(saved.text).find('Hangup'))
        observations=self.client.get('/api/observations').json()['items']
        self.assertEqual(len(observations),1)
        self.assertEqual((observations[0]['wt_cm'],observations[0]['smoke'],observations[0]['day']),(61,True,9))
        self.assertTrue(self.client.get('/api/actions').json()['items'])
        messages=self.client.get('/api/responders/r1/messages').json()['items']
        self.assertTrue(all(item['channel']=='voice' for item in messages))
        self.assertEqual(self.callback('gather',sid,turn=3,speech='YA').status_code,200)
        self.assertEqual(len(self.client.get('/api/observations').json()['items']),1)

    def test_bye_never_confirms_and_three_empty_turns_hang_up(self):
        sid=self.sid()
        for turn,speech in ((0,'61'),(1,'terlihat asap'),(2,'kanal 4')):
            self.callback('gather',sid,turn=turn,speech=speech)
        bye=self.callback('gather',sid,turn=3,speech='matiin ya')
        self.assertIsNotNone(ET.fromstring(bye.text).find('Hangup'))
        self.assertEqual(self.client.get('/api/observations').json()['items'],[])
        fresh=self.sid()
        for turn in range(3):
            result=self.callback('gather',fresh,turn=turn,speech='')
        self.assertIsNotNone(ET.fromstring(result.text).find('Hangup'))

    def test_metres_plausibility_and_embedded_smoke_yes_do_not_save(self):
        sid=self.sid()
        unclear=self.callback('gather',sid,turn=0,speech='5 meter')
        self.assertIn('Mohon perjelas',unclear.text)
        self.assertEqual(self.client.get('/api/observations').json()['items'],[])
        self.callback('gather',sid,turn=1,speech='0.61 meter')
        self.callback('gather',sid,turn=2,speech='ASAP YA')
        self.callback('gather',sid,turn=3,speech='parit empat')
        self.assertEqual(self.client.get('/api/observations').json()['items'],[])

    def test_rest_shape_no_timeout_and_shared_daily_cap_including_diagnostic(self):
        result=self.call(diagnostic=True,level='RENDAH')
        self.assertEqual(result['status'],'queued')
        self.assertEqual(len(self.requests),1)
        form=parse_qs(self.requests[0].content.decode())
        self.assertEqual(set(form),{'To','From','Url','StatusCallback'})
        self.assertNotIn('Timeout',form)
        self.assertTrue(self.requests[0].headers['Authorization'].startswith('Basic '))
        self.assertTrue(result['call_id'].startswith('vc_'))
        self.assertEqual(self.call()['reason'],'call_cooldown')
        self.now+=timedelta(seconds=31)
        self.assertEqual(self.call(level='EKSTREM')['reason'],'daily_call_limit')
        original=self.app.state.tools.get_village_status
        with patch.object(self.app.state.tools,'get_village_status',side_effect=lambda *a,**k:original(*a,**k).model_copy(update={'level':'EKSTREM'})):
            self.assertEqual(self.app.state.scout.place_call('r1')['reason'],'daily_call_limit')
        self.assertEqual(len(self.requests),1)

    def test_concurrent_call_requests_reserve_one_provider_attempt(self):
        original=self.app.state.tools.get_village_status
        with patch.object(self.app.state.tools,'get_village_status',side_effect=lambda *a,**k:original(*a,**k).model_copy(update={'level':'TINGGI'})):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda unused:self.voice.call('r1'),range(2)))
        self.assertEqual(len(self.requests),1)
        self.assertEqual(sum(result['status']=='queued' for result in results),1)

    def test_policy_quiet_and_chosen_contact_gate_without_provider(self):
        self.now=datetime(2026,11,7,15,tzinfo=timezone.utc)
        self.assertEqual(self.call(diagnostic=True,level='RENDAH')['reason'],'quiet_hours')
        self.assertEqual(len(self.requests),0)
        self.voice.settings.uat_phone='+6289999999999'
        with self.assertRaises(ChannelUnavailable):
            self.call(diagnostic=True)
        self.assertEqual(len(self.requests),0)

    def test_public_and_force_mock_never_contact_provider(self):
        self.app.state.channels.settings.force_mock=True
        self.assertEqual(self.call()['status'],'mock')
        self.assertEqual(len(self.requests),0)
        self.assertFalse(self.voice.ready())
        self.assertEqual(self.voice.mode(),'mock')

    def test_explicit_chosen_normal_uat_bypasses_only_risk_and_binds_purpose(self):
        original=self.app.state.tools.get_village_status
        with patch.object(self.app.state.tools,'get_village_status',side_effect=lambda *a,**k:original(*a,**k).model_copy(update={'level':'RENDAH'})):
            self.assertEqual(self.voice.call('r1')['status'],'suppressed')
            result=self.voice.call('r1',uat=True)
        self.assertEqual(result['status'],'queued')
        form=parse_qs(self.requests[0].content.decode())
        sid=parse_qs(urlsplit(form['Url'][0]).query)['sid'][0]
        identity=verify_session(self.voice.settings,sid,now=self.voice.clock())
        self.assertTrue(identity['uat'])
        self.assertFalse(identity['diagnostic'])
        self.assertEqual(self.voice.call('r1',uat=True)['reason'],'call_cooldown')

    def test_replay_extreme_never_bypasses_current_live_low_risk(self):
        self.app.state.warden.reset_for_alert('r1','replay',9,'t_replay_live_policy')
        original=self.app.state.tools.get_village_status
        def status(village_id,mode='live',day=0):
            return original(village_id,mode,day).model_copy(update={'level':'EKSTREM' if mode=='replay' else 'RENDAH'})
        with patch.object(self.app.state.tools,'get_village_status',side_effect=status):
            normal=self.voice.call('r1')
            self.assertEqual(normal['status'],'suppressed')
            self.assertEqual(normal['reason'],'voice_below_high')
            self.assertEqual(len(self.requests),0)
            self.assertEqual(self.voice.call('r1',uat=True)['status'],'queued')
        form=parse_qs(self.requests[0].content.decode())
        sid=parse_qs(urlsplit(form['Url'][0]).query)['sid'][0]
        session=self.app.state.database.get(self.voice._key(sid))
        self.assertEqual((session['mode'],session['day']),('replay',9))

    def test_quiet_exception_uses_live_level_not_replay_for_voice_and_sms(self):
        self.app.state.warden.reset_for_alert('r1','replay',9,'t_quiet_live_policy')
        self.now=datetime(2026,11,7,15,tzinfo=timezone.utc)
        original=self.app.state.tools.get_village_status
        levels={'replay':'EKSTREM','live':'RENDAH'}
        def status(village_id,mode='live',day=0):
            return original(village_id,mode,day).model_copy(update={'level':levels[mode]})
        with patch.object(self.app.state.tools,'get_village_status',side_effect=status):
            self.assertEqual(self.voice.call('r1',uat=True)['reason'],'quiet_hours')
            self.assertEqual(self.client.post('/api/responders/r1/send',json={'text':'uji coba'}).json()['status'],'suppressed')
            self.assertEqual((len(self.requests),len(self.sms)),(0,0))
            levels.update(replay='RENDAH',live='EKSTREM')
            self.assertEqual(self.voice.call('r1')['status'],'queued')
            self.assertEqual(self.client.post('/api/responders/r1/send',json={'text':'uji coba'}).json()['status'],'queued')
        self.assertEqual((len(self.requests),len(self.sms)),(1,1))

    def test_status_only_logs_safe_metadata_and_sid_scope(self):
        self.call()
        form=parse_qs(self.requests[0].content.decode())
        sid=parse_qs(urlsplit(form['Url'][0]).query)['sid'][0]
        result=self.callback('status',sid,params={'CallSid':self.provider_sid,'CallStatus':'completed','CallDuration':'42','To':'+6281234567890'})
        self.assertEqual(result.status_code,200)
        trace=self.client.get('/api/trace').text
        self.assertTrue(sid not in trace and self.provider_sid not in trace and '6281234567890' not in trace,'Private call values leaked to trace')
        self.assertIn('duration_seconds',trace)
        self.assertEqual(self.callback('status',sid,params={'CallSid':'CA'+'d'*32,'CallStatus':'completed'}).status_code,403)

    def test_diagnostic_collects_five_rounds_without_warden_save(self):
        sid=self.sid(diagnostic=True)
        start=self.callback('start',sid)
        self.assertEqual(ET.fromstring(start.text).find('Gather').attrib['language'],'en-US')
        for turn in range(5):
            result=self.callback('gather',sid,turn=turn,speech='AIR 61 ASAP YA')
        self.assertIsNotNone(ET.fromstring(result.text).find('Hangup'))
        self.assertEqual(self.client.get('/api/observations').json()['items'],[])

    def test_cli_config_and_consent_provision_are_environment_only_no_network(self):
        output=StringIO()
        with patch.dict('os.environ',{'PEATFORGE_DB_PATH':str(Path(self.temp.name)/'cli.sqlite3')}),patch('httpx.Client',side_effect=AssertionError('No network in check/provision')),redirect_stdout(output):
            self.assertEqual(voice_main(['check']),1)
            self.assertEqual(voice_main(['provision-uat']),1)
            self.assertEqual(voice_main(['provision-uat','--consent']),0)
            self.assertEqual(voice_main(['check']),0)
        self.assertTrue('configured' in output.getvalue())
        self.assertTrue('6281234567890' not in output.getvalue() and 'b'*32 not in output.getvalue(),'CLI leaked private configuration')

    def test_missing_sms_gateway_is_mock_while_twilio_remains_ready(self):
        self.app.state.channels.settings.gateway_url=''
        self.app.state.channels.settings.gateway_user=''
        self.app.state.channels.settings.gateway_pass=''
        self.assertFalse(self.app.state.channels.settings.force_mock)
        self.assertTrue(self.voice.ready())
        config=self.client.get('/api/config').json()
        self.assertEqual((config['outbound'],config['voice_mode']),('mock','live'))
        self.assertEqual(self.app.state.channels.send('r1','Uji coba SMS')['status'],'mock')
        self.assertEqual(len(self.sms),0)

    def test_cli_uat_pair_is_one_diagnostic_then_normal_and_no_budget_reset(self):
        first=self.voice.call('r1',diagnostic=True,cli_uat_pair=True)
        self.assertEqual(first['status'],'queued')
        self.assertEqual(self.voice.call('r1',uat=True)['status'],'suppressed')
        self.assertEqual(self.voice.call('r1',uat=True,cli_uat_pair=True)['reason'],'call_cooldown')
        self.now+=timedelta(seconds=31)
        second=self.voice.call('r1',uat=True,cli_uat_pair=True)
        self.assertEqual(second['status'],'queued')
        self.assertEqual(self.voice.call('r1',uat=True,cli_uat_pair=True)['reason'],'uat_pair_complete')
        self.now+=timedelta(seconds=31)
        self.assertEqual(self.voice.call('r1',uat=True)['reason'],'daily_call_limit')
        self.assertEqual(len(self.requests),2)
        self.assertEqual(self.app.state.database.get('call-policy:r1')['count'],2)

    def test_uat_pair_requires_chosen_contact_sequence_and_is_not_http_exposed(self):
        self.assertEqual(self.voice.call('r1',uat=True,cli_uat_pair=True)['reason'],'uat_pair_requires_diagnostic')
        with self.app.state.database.connect() as connection:
            connection.execute('INSERT INTO responders(id,village_id,name) VALUES(?,?,?)',('r2',VILLAGE_ID,'Other contact'))
        self.app.state.channels.contacts().bind('r2','081299999999',consent=True)
        with self.assertRaises(ChannelUnavailable):
            self.voice.call('r2',diagnostic=True,cli_uat_pair=True)
        self.assertEqual(self.client.post('/api/voice/twilio/call',json={'responder_id':'r1','diagnostic':True,'cli_uat_pair':True}).status_code,422)
        self.assertEqual(len(self.requests),0)
        self.assertIsNone(self.app.state.database.get('call-policy:r1'))

    def test_concurrent_second_uat_pair_allows_only_one_additional_post(self):
        self.assertEqual(self.voice.call('r1',diagnostic=True,cli_uat_pair=True)['status'],'queued')
        self.now+=timedelta(seconds=31)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda unused:self.voice.call('r1',uat=True,cli_uat_pair=True),range(2)))
        self.assertEqual(sum(item['status']=='queued' for item in results),1)
        self.assertEqual(len(self.requests),2)

    def test_status_report_is_readonly_chosen_filtered_and_transcript_redacted(self):
        self.app.state.channels.record_voice('r1','Telepon +6281234567890 lalu buka https://private.test?sid=secret',direction='inbound')
        reads=[]
        def response(request):
            reads.append(request)
            return httpx.Response(200,json={'calls':[{'sid':self.provider_sid,'to':'+6281234567890','status':'failed','error_code':21215},
                {'sid':'CA'+'d'*32,'to':'+6289999999999','status':'completed'}]})
        self.voice.transport=httpx.MockTransport(response)
        report=self.voice.status_report('r1')
        self.assertEqual(len(report['calls']),1)
        self.assertEqual(report['calls'][0],{'call_sid':self.provider_sid,'status':'failed','error_code':21215})
        self.assertEqual(reads[0].method,'GET')
        self.assertEqual(reads[0].url.params['To'],'+6281234567890')
        printed=json.dumps(report)
        self.assertTrue('6281234567890' not in printed and 'private.test' not in printed and 'sid=secret' not in printed)
        self.assertIsNone(self.app.state.database.get('call-policy:r1'))

    def test_realtime_call_has_carrier_duration_cap_without_trial_timeout(self):
        with patch.dict('os.environ',{'VOICE_ENGINE':'realtime','OPENAI_API_KEY':'sk-test-not-a-real-provider-key'}):
            self.assertEqual(self.voice.call('r1',uat=True)['status'],'queued')
        form=parse_qs(self.requests[-1].content.decode())
        self.assertEqual(form.get('TimeLimit'),['180'])
        self.assertNotIn('Timeout',form)

    def test_stream_repair_is_cli_only_chosen_and_one_use_without_carrier_cap(self):
        from .events import record
        today=self.now.astimezone(WIB).date().isoformat()
        self.app.state.database.set('call-policy:r1',{'date':today,'count':3,'last_call':self.now.timestamp()-61})
        self.app.state.database.set('uat-pair:r1',{'date':today,'count':2,'phase':'complete'})
        self.app.state.database.set('uat-realtime:r1',{'date':today,'reserved_at':self.now.timestamp()-61})
        record(self.app.state.database,'Warden','call_requested',trace_id=None,input={'responder_id':'r1'},output={'call_id':'previous','status':'queued'})
        self.app.state.database.set('voice-session:previous',{'responder_id':'r1','call_id':'previous','status':'queued','provider_status':'completed'})
        with patch.dict('os.environ',{'VOICE_ENGINE':'realtime','OPENAI_API_KEY':'sk-offline-test'}):
            for kwargs in ({'cli_stream_repair':True},{'cli_stream_repair':True,'cli_realtime_uat':True},
                           {'cli_stream_repair':True,'cli_realtime_uat':True,'server_limit_only':True,'cli_retry_rejected':True}):
                with self.assertRaises(ChannelUnavailable):
                    self.voice.call('r1',**kwargs)
            result=self.voice.call('r1',cli_realtime_uat=True,server_limit_only=True,cli_stream_repair=True)
            self.assertEqual(result['status'],'queued')
            self.assertEqual(self.voice.call('r1',cli_realtime_uat=True,server_limit_only=True,cli_stream_repair=True)['status'],'suppressed')
        self.assertEqual(len(self.requests),1)
        self.assertNotIn('TimeLimit',parse_qs(self.requests[0].content.decode()))
        self.assertEqual(self.app.state.database.get('call-policy:r1')['count'],4)
        self.assertEqual(self.client.post('/api/voice/twilio/call',json={'responder_id':'r1','cli_stream_repair':True}).status_code,422)

    def test_failed_call_retains_only_safe_provider_error_code(self):
        self.voice.transport=httpx.MockTransport(lambda request:httpx.Response(400,json={'code':21215,'message':'private provider details','more_info':'https://private.test'}))
        self.assertEqual(self.voice.call('r1',uat=True)['status'],'failed')
        with self.app.state.database.connect() as connection:
            saved=[json.loads(row['value']) for row in connection.execute("SELECT value FROM kv WHERE key LIKE 'voice-session:%'")]
        self.assertEqual(saved[0]['error_code'],21215)
        self.assertTrue('private provider details' not in json.dumps(saved))


if __name__=='__main__':
    unittest.main()
