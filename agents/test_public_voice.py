import httpx
import unittest
from unittest.mock import patch
from .test_public_reports import PublicReportTests
from .test_demo_voice import Timer


class PublicVoiceTests(unittest.TestCase):
    start=PublicReportTests.start
    headers=PublicReportTests.headers
    def setUp(self):
        self.enterContext(patch.dict('os.environ',{'DEMO_PUBLIC':'1','OPENAI_API_KEY':'sk-fake-test-key'}))
        PublicReportTests.setUp(self)
        self.service=self.app.state.browser_voice
        self.service.clock=lambda:10000
        self.service.timer_factory=Timer
        self.service.transport=httpx.MockTransport(lambda request:httpx.Response(201,text='v=0\r\ns=answer',headers={'Location':'/v1/realtime/calls/rtc_test'}) if request.url.path.endswith('/calls') else httpx.Response(200,json={'value':'ek_test','expires_at':10060}))
    def test_public_voice_scope_transcript_and_server_cap(self):
        self.assertEqual(self.client.post('/api/public/voice/session').status_code,401)
        first,second=self.start(),self.start()
        headers=self.headers(first)
        result=self.client.post('/api/public/voice/session',headers=headers,json={'responder_id':'r1'}).json()
        self.assertNotIn('client_secret',result)
        self.assertEqual(result['max_seconds'],600)
        sid=result['session_id']
        connect={'session_id':sid,'sdp':'v=0\r\ns=offer'}
        self.assertEqual(self.client.post('/api/public/voice/connect',headers=self.headers(second),json=connect).status_code,403)
        self.assertEqual(self.client.post('/api/public/voice/connect',headers=headers,json=connect).status_code,200)
        response=self.client.post('/api/public/voice/transcript',headers=headers,json={'session_id':sid,'event_id':'one','role':'user','text':'parit kering'})
        self.assertEqual(response.status_code,200,response.text)
        response=self.client.post('/api/public/voice/tool',headers=headers,json={'session_id':sid,'call_id':'tool-one','name':'get_village_status','arguments':{}})
        self.assertEqual(response.status_code,200,response.text)
        thread=self.client.get('/api/public/messages',headers=headers).json()
        self.assertTrue(any(row['body']=='parit kering' for row in thread['items']))
        self.assertEqual(self.client.post('/api/public/voice/end',headers=headers,json={'session_id':sid}).status_code,200)
    def test_missing_key_503(self):
        token=self.start()
        self.service.settings.key=''
        self.assertEqual(self.client.post('/api/public/voice/session',headers=self.headers(token)).status_code,503)

