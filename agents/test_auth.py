from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app
from .auth import COOKIE_NAME,SESSION_SECONDS
from .channels import ChannelSettings,ChannelUnavailable
from .mock_engine import VILLAGE_ID
from .tools import seed_mock

FAKE_PIN='123456'
FAKE_SECRET='test-only-session-secret-value-32-characters'


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env=self.enterContext(patch.dict('os.environ',{'DEMO_PIN':FAKE_PIN,'SESSION_SECRET':FAKE_SECRET,'PEATFORGE_LIVE_CHANNELS':''}))
        self.engine=EngineService(Ingest())
        self.app=create_app(db_path=Path(self.temp.name)/'protected.sqlite3',public_demo=False,engine=self.engine)
        self.client=self.enterContext(TestClient(self.app,base_url='http://localhost'))
        seed_mock(self.app.state.database)

    def login(self,client=None):
        result=(client or self.client).post('/api/auth/login',json={'pin':FAKE_PIN})
        self.assertEqual(result.status_code,200,result.text)
        return result

    def link(self,responder_id='r1'):
        response=self.client.get('/api/responders/'+responder_id+'/field-link')
        self.assertEqual(response.status_code,200)
        return response.json()['field_path'].split('/')[-1]

    def test_protected_routes_require_cookie_and_login_logout(self):
        checks=[('GET','/api/config',None),('GET','/api/villages',None),('GET',f'/api/villages/{VILLAGE_ID}/status',None),
                ('GET','/api/actions',None),('GET','/api/trace',None),('GET','/api/responders',None),
                ('GET','/api/observations',None),('GET','/api/policy',None),('POST','/api/scout/tick',{'mode':'live','day':0}),
                ('POST',f'/api/villages/{VILLAGE_ID}/simulate',{'type':'canal_block','targets':['K4']}),
                ('POST','/api/observations',{'village_id':VILLAGE_ID,'responder_id':'r1','wt_cm':55,'confirmed':True}),
                ('POST','/api/actions/unknown/approve',{})]
        for method,path,body in checks:
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method,path,json=body).status_code,401)
        self.assertEqual(self.client.get('/api/auth/session').json()['authenticated'],False)
        response=self.login()
        header=response.headers['set-cookie']
        for expected in ('HttpOnly','SameSite=strict','Max-Age=43200','Path=/'):
            self.assertTrue(expected in header,'Session cookie flags missing')
        self.assertTrue(FAKE_PIN not in header,'PIN must never be serialized in session cookie')
        self.assertEqual(self.client.get('/api/auth/session').json()['authenticated'],True)
        self.assertEqual(self.client.get('/api/config').status_code,200)
        self.assertEqual(self.client.post('/api/auth/logout').json()['authenticated'],False)
        self.assertEqual(self.client.get('/api/config').status_code,401)

    def test_missing_invalid_configuration_fails_closed_without_fallback(self):
        for pin,secret in (('',''),('12345','s'*32),('123456','short'),('１２３４５６','s'*32)):
            with self.subTest(valid_pin=len(pin)==6), patch.dict('os.environ',{'DEMO_PIN':pin,'SESSION_SECRET':secret}):
                app=create_app(db_path=Path(self.temp.name)/('invalid-'+str(len(pin))+str(len(secret))+'.sqlite3'),public_demo=False,engine=self.engine)
                with TestClient(app,base_url='http://localhost') as client:
                    self.assertEqual(client.post('/api/auth/login',json={'pin':FAKE_PIN}).status_code,503)
                    self.assertFalse(client.get('/api/auth/session').json()['authenticated'])
                    self.assertEqual(client.get('/api/config').status_code,401)

    def test_rate_limit_uses_socket_peer_not_spoofed_proxy_headers(self):
        now=[1000.0]
        self.app.state.auth.clock=lambda:now[0]
        for number in range(5):
            result=self.client.post('/api/auth/login',json={'pin':'999999'},headers={'CF-Connecting-IP':f'198.51.100.{number}','X-Forwarded-For':f'203.0.113.{number}'})
            self.assertEqual(result.status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json={'pin':FAKE_PIN},headers={'CF-Connecting-IP':'192.0.2.200'}).status_code,429)
        now[0]+=61
        self.login()

    def test_tampered_and_expired_cookie_rejected(self):
        self.login()
        token=self.client.cookies.get(COOKIE_NAME)
        self.client.cookies.clear()
        forged=token[:-1]+('0' if token[-1]!='0' else '1')
        self.client.cookies.set(COOKIE_NAME,forged)
        self.assertEqual(self.client.get('/api/config').status_code,401)
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE_NAME,token)
        self.app.state.auth.clock=lambda:float(token.split('.')[1])+SESSION_SECONDS+1
        self.assertEqual(self.client.get('/api/config').status_code,401)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_https_and_public_tunnel_cookies_are_secure_even_with_proxy_http(self):
        for url in ('https://localhost','http://demo.trycloudflare.com'):
            with self.subTest(url=url),TestClient(self.app,base_url=url) as client:
                response=self.login(client)
                self.assertTrue('Secure' in response.headers['set-cookie'],'Remote session cookie must be Secure')
        response=self.client.post('/api/auth/login',json={'pin':FAKE_PIN},headers={'X-Forwarded-Proto':'https'})
        self.assertEqual(response.status_code,200)
        self.assertFalse('Secure' in response.headers['set-cookie'],'Untrusted forwarded protocol must be ignored on local HTTP')

    def test_field_links_stable_isolated_and_never_operator_auth(self):
        self.assertEqual(self.client.get('/api/responders/r1/field-link').status_code,401)
        self.login()
        with self.app.state.database.connect() as connection:
            connection.execute('INSERT INTO responders(id,village_id,name) VALUES(?,?,?)',('r2',VILLAGE_ID,'Second demo responder'))
        first=self.link('r1')
        second=self.link('r2')
        self.assertTrue(len(first)==43 and first!=second)
        self.assertTrue(first==self.link('r1'),'Field capability must remain stable')
        self.client.cookies.clear()
        field1=self.client.get('/api/field/'+first+'/messages')
        self.assertEqual(field1.status_code,200)
        self.assertEqual(set(field1.json()['responder']),{'name','village_id'})
        report=self.client.post('/api/field/'+first+'/send',json={'text':'AIR 61 ASAP YA dekat kanal 4'})
        self.assertEqual(report.status_code,200)
        self.assertEqual(report.json()['state'],'confirm')
        foreign=self.client.get('/api/field/'+second+'/messages').json()['items']
        self.assertEqual(foreign,[])
        override=self.client.post('/api/field/'+first+'/send',json={'text':'YA','responder_id':'r2','mode':'replay','day':9})
        self.assertEqual(override.status_code,422)
        self.assertEqual(self.client.get('/api/config',headers={'Authorization':'Bearer '+first}).status_code,401)
        self.client.cookies.set(COOKIE_NAME,first)
        self.assertEqual(self.client.get('/api/trace').status_code,401)
        self.assertEqual(self.client.get('/api/responders/r2/field-link').status_code,401)
        self.assertEqual(self.client.post('/api/field/'+first+'/tools',json={'name':'arbitrary'}).status_code,404)

    def test_field_unknown_tokens_limits_and_no_token_response_leaks(self):
        self.login()
        token=self.link()
        self.client.cookies.clear()
        for fake in ('unknown','A'*43):
            response=self.client.get('/api/field/'+fake+'/messages')
            self.assertEqual(response.status_code,404)
            self.assertTrue(fake not in response.text,'Unknown capability reflected in response')
        self.assertEqual(self.client.post('/api/field/'+token+'/send',json={'text':'x'*501}).status_code,422)
        for number in range(30):
            response=self.client.post('/api/field/'+token+'/send',json={'text':'ping'})
            self.assertEqual(response.status_code,200)
            self.assertTrue(token not in response.text,'Capability leaked from field response')
        self.assertEqual(self.client.post('/api/field/'+token+'/send',json={'text':'ping'}).status_code,429)
        self.login()
        for path in ('/api/responders','/api/trace','/api/observations','/api/config'):
            response=self.client.get(path)
            self.assertTrue(token not in response.text,'Capability leaked from operator read')

    def test_field_confirmation_uses_same_pipeline_and_inherits_replay(self):
        self.login()
        token=self.link()
        self.app.state.scout.clock=lambda:__import__('datetime').datetime(2026,11,7,3,tzinfo=__import__('datetime').timezone.utc)
        self.client.post('/api/events/risk-changed',json={'village_id':VILLAGE_ID,'mode':'replay','day':9,'hour_override':10})
        self.client.cookies.clear()
        for text,state in (('AIR 61 ASAP YA dekat kanal 4','confirm'),('YA','saved')):
            response=self.client.post('/api/field/'+token+'/send',json={'text':text})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['state'],state)
        self.login()
        actions=self.client.get('/api/actions').json()['items']
        self.assertTrue(actions)
        self.assertTrue(all(action['mode']=='replay' and action['day']==9 and action['responder_id']=='r1' for action in actions))
        approved=self.client.post('/api/actions/'+actions[0]['id']+'/approve',json={})
        self.assertEqual(approved.status_code,200,approved.text)

    def test_default_public_demo_and_live_channel_flag_are_independent(self):
        with patch.dict('os.environ',{'PEATFORGE_LIVE_CHANNELS':'true','SMS_GATEWAY_URL':'https://provider.test','SMS_GATEWAY_USER':'fake','SMS_GATEWAY_PASS':'fake'}):
            public_settings=ChannelSettings.from_environment(public_demo=True)
            protected_settings=ChannelSettings.from_environment(public_demo=False)
        self.assertTrue(public_settings.force_mock)
        self.assertFalse(protected_settings.force_mock)
        self.assertTrue(ChannelSettings.from_environment(public_demo=False).force_mock)
        with patch.object(self.app.state.channels.gateway,'send',side_effect=AssertionError('No implicit provider send')):
            self.assertEqual(self.app.state.channels.send('r1','test')['status'],'mock')

    def test_security_headers_static_worker_and_log_scope_redaction(self):
        dist=Path(self.temp.name)/'dist'
        (dist/'assets').mkdir(parents=True)
        (dist/'index.html').write_text('<html>demo</html>',encoding='utf-8')
        (dist/'assets'/'worker.mjs').write_text('export {}',encoding='utf-8')
        app=create_app(db_path=Path(self.temp.name)/'headers.sqlite3',public_demo=True,engine=self.engine,web_dist=dist)
        scopes=[]
        async def capture(scope,receive,send):
            await app(scope,receive,send)
            scopes.append(scope)
        with TestClient(capture,base_url='http://localhost') as client:
            link=client.get('/api/responders/r1/field-link').json()['field_path']
            token=link.split('/')[-1]
            for path in ('/',link,'/assets/worker.mjs','/api/field/'+token+'/messages'):
                response=client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['referrer-policy'],'no-referrer')
                self.assertEqual(response.headers['x-content-type-options'],'nosniff')
                csp=response.headers['content-security-policy']
                self.assertTrue("worker-src 'self'" in csp and 'fonts.googleapis.com' in csp and 'arcgisonline.com' in csp and 'openstreetmap.org' in csp)
                if path.startswith(('/api/','/f/')):
                    self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertIn('application/javascript',client.get('/assets/worker.mjs').headers['content-type'])
            self.assertEqual(client.get('/AGENTS.md').status_code,404)
            client.get('/api/auth/session?key=fake-secret-query')
        self.assertTrue(all(token not in scope.get('path','') and token.encode() not in scope.get('raw_path',b'') for scope in scopes),'Capability leaked to ASGI log scope')
        self.assertTrue(all(not scope.get('query_string') for scope in scopes),'Query credential leaked to ASGI log scope')

    def test_explicit_temporary_database_has_no_real_adaptive_state_path(self):
        with patch('agents.app.EngineService',wraps=EngineService) as factory:
            app=create_app(db_path=Path(self.temp.name)/'isolated.sqlite3',public_demo=True,data_source='fixture')
        self.assertIsNone(factory.call_args.kwargs['state_path'])
        with TestClient(app) as client:
            before=client.get(f'/api/villages/{VILLAGE_ID}/status').json()
            response=client.post('/api/observations',json={'village_id':VILLAGE_ID,'responder_id':'r1','wt_cm':before['wt_cm']+5,'confirmed':True})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['recalibrated'])
        self.assertFalse(app.state.config['scout_background'])


if __name__=='__main__':
    unittest.main()
