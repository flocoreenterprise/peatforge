import json
import unittest
from unittest.mock import patch
import httpx
from . import test_realtime
from .demo_voice import DemoVoice
from .realtime import Access


class Timer:
    def __init__(self,seconds,callback):
        self.seconds,self.callback,self.cancelled=seconds,callback,False
    def start(self): pass
    def cancel(self): self.cancelled=True


class DemoVoiceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict('os.environ',{'DEMO_PUBLIC':'1'}))
        test_realtime.RealtimeTests.setUp(self)
        self.service.timer_factory=Timer
        self.hangups=[]
        def provider(request):
            if request.url.path.endswith('/hangup'):
                self.hangups.append(request.url.path)
                return httpx.Response(200)
            if request.url.path.endswith('/calls'):
                self.assertEqual(request.headers['authorization'],'Bearer ek_test_ephemeral')
                return httpx.Response(201,text='v=0\r\ns=answer',headers={'Location':'/v1/realtime/calls/rtc_test'})
            return httpx.Response(200,json={'value':'ek_test_ephemeral','expires_at':self.now+60})
        self.service.transport=httpx.MockTransport(provider)

    mint=test_realtime.RealtimeTests.mint
    transcript=test_realtime.RealtimeTests.transcript
    tool=test_realtime.RealtimeTests.tool

    def connect(self,sid):
        return self.client.post('/api/voice/connect',json={'session_id':sid,'sdp':'v=0\r\ns=offer'})

    def test_public_credentials_private_and_server_deadline(self):
        result=self.client.post('/api/voice/session',json={'responder_id':'r1'}).json()
        self.assertNotIn('client_secret',result)
        self.assertTrue(result['server_connect'])
        self.assertEqual(result['session_expires_at'],self.now+600)
        sid=result['session_id']
        self.assertEqual(self.connect(sid).status_code,200)
        self.assertEqual(self.connect(sid).status_code,409)
        timer=self.service.timers[sid]
        self.assertEqual(timer.seconds,600)
        timer.callback()
        self.assertEqual(self.hangups,['/v1/realtime/calls/rtc_test/hangup'])
        self.assertTrue(self.service.db.get(self.service._key(sid))['ended'])
        with self.service.db.connect() as connection:
            data=' '.join(row['value'] for row in connection.execute('SELECT value FROM kv'))
        self.assertNotIn('ek_test_ephemeral',data)
        self.assertNotIn(self.service.settings.key,data)

    def test_rolling_hour_cap_survives_service_restart(self):
        for unused in range(200):
            self.service._budget()
            self.now+=1
        response=self.client.post('/api/voice/session',json={'responder_id':'r1'})
        self.assertEqual(response.status_code,429)
        self.assertIn('Batas demo',response.text)
        another=DemoVoice(self.service.db,self.service.tools,self.service.channels,self.service.coordinator,self.service.settings,clock=lambda:self.now,timer_factory=Timer)
        with self.assertRaises(Exception) as error:
            another._budget()
        self.assertEqual(error.exception.status_code,429)
        self.now+=3600
        self.mint()

    def test_scope_checked_before_consuming_ephemeral(self):
        token=self.client.get('/api/responders/r1/field-link').json()['field_path'].split('/')[-1]
        sid=self.mint({'X-Field-Token':token})
        self.assertEqual(self.connect(sid).status_code,403)
        self.assertIn(sid,self.service.pending)
        self.assertEqual(self.client.post('/api/voice/connect',headers={'X-Field-Token':token},json={'session_id':sid,'sdp':'v=0\r\ns=offer'}).status_code,200)

    def test_expiry_during_handshake_hangs_up(self):
        sid=self.mint()
        def provider(request):
            if request.url.path.endswith('/calls'):
                self.now+=601
                return httpx.Response(201,text='v=0\r\ns=answer',headers={'Location':'/v1/realtime/calls/rtc_late'})
            self.hangups.append(request.url.path)
            return httpx.Response(200)
        self.service.transport=httpx.MockTransport(provider)
        self.assertEqual(self.connect(sid).status_code,503)
        self.assertEqual(len(self.hangups),1)

    def test_hangup_failure_blocks_further_spending(self):
        sid=self.mint()
        self.connect(sid)
        self.service.transport=httpx.MockTransport(lambda request:httpx.Response(503))
        self.client.post('/api/voice/end',json={'session_id':sid})
        self.assertTrue(self.service.db.get('demo-voice-hangup-failed'))
        self.assertEqual(self.client.post('/api/voice/session',json={'responder_id':'r2'}).status_code,429)
        self.service.transport=httpx.MockTransport(lambda request:httpx.Response(200))

    def test_missing_key_returns503_and_text_simulation_available(self):
        self.service.settings.key=''
        self.assertEqual(self.client.post('/api/voice/session',json={'responder_id':'r1'}).status_code,503)
        result=self.client.post('/api/voice/simulate',json={'responder_id':'r1','event_id':'fallback','text':'halo'})
        self.assertEqual(result.status_code,200)
