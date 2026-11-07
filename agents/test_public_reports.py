from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app

class PublicReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'db.sqlite3'
        self.engine=EngineService(Ingest())
        self.app=create_app(db_path=self.path,public_demo=True,engine=self.engine)
        self.app.state.config['public_reporting_enabled']=True
        self.client=self.enterContext(TestClient(self.app))
        self.app.state.public_reports.clock=lambda:10000
        self.village=self.client.get('/api/public/villages').json()['items'][0]['id']
    def start(self):
        result=self.client.post('/api/public/sessions',json={'village_id':self.village,'consent':True})
        self.assertEqual(result.status_code,200,result.text)
        return result.json()['token']
    def headers(self,token):return {'Authorization':'Bearer '+token}
    def send(self,token,text,event):
        result=self.client.post('/api/public/messages',json={'text':text,'event_id':event},headers=self.headers(token))
        self.assertEqual(result.status_code,200,result.text)
        return result.json()
    def test_consent_scope_extra_fields_voice_disabled_and_token_not_stored_raw(self):
        self.assertEqual(self.client.post('/api/public/sessions',json={'village_id':self.village,'consent':False}).status_code,422)
        self.assertEqual(self.client.post('/api/public/sessions',json={'village_id':self.village,'consent':True,'responder_id':'r1'}).status_code,422)
        first,second=self.start(),self.start()
        self.send(first,'YA','a')
        one=self.client.get('/api/public/messages',headers=self.headers(first)).json()
        two=self.client.get('/api/public/messages',headers=self.headers(second)).json()
        self.assertGreater(len(one['items']),len(two['items']))
        self.assertEqual(self.client.get('/api/public/messages').status_code,401)
        self.assertEqual(self.client.get('/api/public/messages',headers=self.headers('a'*43)).status_code,401)
        self.assertEqual(self.client.post('/api/public/voice/session',headers=self.headers(first)).status_code,503)
        self.assertEqual(self.client.post('/api/public/voice/session').status_code,401)
        self.assertEqual(self.client.post('/api/public/messages',headers=self.headers(first),json={'text':'hi','event_id':'b','responder_id':'r1'}).status_code,422)
        with self.app.state.database.connect() as con:
            raw=' '.join(row['value'] for row in con.execute('SELECT value FROM kv'))
        self.assertNotIn(first,raw)
        self.assertNotIn(second,raw)
        self.app.state.config['public_reporting_enabled']=False
        self.assertEqual(self.client.get('/api/public/villages').status_code,404)
    def test_numeric_public_never_adapts_human_queue_approval_web_followup(self):
        token=self.start()
        with patch.object(self.engine,'recalibrate',side_effect=AssertionError('No public assimilation')),patch.object(self.engine,'simulate',side_effect=AssertionError('No public numerical proposal')):
            for i,text in enumerate(('YA','61 sentimeter','terlihat api','kanal empat')):
                result=self.send(token,text,str(i))
            self.assertEqual(result['state'],'confirm')
            self.assertEqual(self.client.get('/api/observations').json()['items'],[])
            result=self.send(token,'YA','confirm')
            self.assertEqual(self.send(token,'YA','confirm'),result)
        observation=self.client.get('/api/observations').json()['items'][0]
        self.assertEqual((observation['source'],observation['confidence_label'],observation['anonymous']),('public','LOW',True))
        action=result['actions'][0]
        self.assertEqual((action['source'],action['anonymous'],action['priority']),('public',True,'URGENT'))
        approval=self.client.post('/api/actions/'+action['id']+'/approve',json={})
        self.assertEqual(approval.status_code,200,approval.text)
        own=self.client.get('/api/public/messages',headers=self.headers(token)).json()
        self.assertTrue(any('disetujui' in item['body'] for item in own['items']))
        with self.app.state.database.connect() as con:
            followup=con.execute("SELECT channel FROM messages WHERE id=?",('m_followup_'+action['id'],)).fetchone()
        self.assertEqual(followup['channel'],'web')
    def test_persistent_caps_ignore_forwarded_ip_and_restart(self):
        self.start();self.start()
        self.assertEqual(self.client.post('/api/public/sessions',json={'village_id':self.village,'consent':True},headers={'X-Forwarded-For':'different'}).status_code,429)
        other=create_app(db_path=self.path,public_demo=True,engine=self.engine)
        other.state.config['public_reporting_enabled']=True
        with TestClient(other) as client:
            other.state.public_reports.clock=lambda:10000
            self.assertEqual(client.post('/api/public/sessions',json={'village_id':self.village,'consent':True}).status_code,429)
    def test_atomic_global_and_daily_session_budgets(self):
        from concurrent.futures import ThreadPoolExecutor
        from fastapi import HTTPException
        service=self.app.state.public_reports
        def attempt(index):
            try:
                service.start(self.village,'peer-'+str(index))
                return 200
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(attempt,range(6)))
        self.assertEqual(results.count(200),5)
        self.assertEqual(results.count(429),1)
        for index in range(15):
            service.clock=lambda i=index:10100+i*61
            service.start(self.village,'day-peer-'+str(index))
        service.clock=lambda:12000
        with self.assertRaises(HTTPException) as error:
            service.start(self.village,'last-peer')
        self.assertEqual(error.exception.status_code,429)

    def test_token_message_rate_and_expiry(self):
        token=self.start()
        import hashlib
        with self.app.state.database.connect() as con:
            for i in range(29):self.app.state.public_reports.budget(con,'token:'+hashlib.sha256(token.encode()).hexdigest(),30,60)
        self.send(token,'belum jelas','last-allowed')
        self.assertEqual(self.client.post('/api/public/messages',headers=self.headers(token),json={'text':'x','event_id':'last'}).status_code,429)
        self.app.state.public_reports.clock=lambda:10000+86401
        self.assertEqual(self.client.get('/api/public/messages',headers=self.headers(token)).status_code,401)

if __name__=='__main__':unittest.main()
