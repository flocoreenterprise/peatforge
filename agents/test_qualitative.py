import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from engine.ingest import Ingest
from engine.service import EngineService
from .app import create_app
from .models import ObservationInput
from .persona import BYE,greeting,water_category,smoke_category
from .warden import parse_report


class QualitativeCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine=EngineService(Ingest())
        self.app=create_app(db_path=Path(self.temp.name)/'state.sqlite3',public_demo=True,engine=self.engine)
        self.client=self.enterContext(TestClient(self.app))

    def send(self,text):
        response=self.client.post('/api/demo/inbound',json={'responder_id':'r1','mode':'replay','day':9,'text':text})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_categories_never_supply_numeric_water_depth(self):
        for text,category in (('air kering','kering'),('air sedikit','sedikit'),('air masih ada','masih_ada'),('air banjir','banjir')):
            facts,clarify=parse_report(text)
            self.assertFalse(clarify)
            self.assertEqual(facts['water_category'],category)
            self.assertIsNone(facts.get('wt_cm'))
        self.assertIsNone(water_category('tidak kering'))
        self.assertIsNone(water_category('tidak banjir'))
        self.assertEqual(smoke_category('tercium bau tetapi tidak terlihat asap'),'bau')
        facts,clarify=parse_report('AIR 61 cm kering ASAP YA')
        self.assertFalse(clarify)
        self.assertEqual(facts['wt_cm'],61)

    def test_qualitative_report_readback_confirmation_low_confidence_no_model_update(self):
        with patch.object(self.engine,'recalibrate',side_effect=AssertionError('No qualitative assimilation')),patch.object(self.engine,'simulate',side_effect=AssertionError('No qualitative numeric proposal')):
            report=self.send('air kering asap bau dekat Sumur Batu')
            self.assertEqual(report['state'],'confirm')
            self.assertIn('Sumur Batu',report['items'][-1]['body'])
            self.assertIn('kering',report['items'][-1]['body'])
            self.send('matiin ya')
            self.assertEqual(self.client.get('/api/observations').json()['items'],[])
            self.send('air kering asap bau dekat Sumur Batu')
            self.send('YA')
        observation=self.client.get('/api/observations').json()['items'][0]
        self.assertIsNone(observation['wt_cm'])
        self.assertIsNone(observation['landmark_id'])
        self.assertEqual(observation['place_text'],'Sumur Batu')
        self.assertEqual((observation['evidence_type'],observation['confidence_label']),('qualitative','LOW'))
        action=self.client.get('/api/actions').json()['items'][0]
        self.assertEqual(action['kind'],'verify')
        self.assertEqual(action['verifier']['confidence_label'],'LOW')
        self.assertIsNone(action['days_bought'])
        self.assertTrue(all(value is None for value in action['benefit'].values()))

    def test_strict_landmark_aliases_and_literal_bye_priority(self):
        tools=self.app.state.tools
        self.assertIsNone(tools.resolve_landmark('62.11.01.2002','Sumur Batu').landmark_id)
        self.assertEqual(tools.resolve_landmark('62.11.01.2002','parit empat').landmark_id,'K4')
        self.assertEqual(tools.resolve_landmark('62.11.01.2002','sumur dua').landmark_id,'W2')
        for text in ('matiin ya','udahan','nanti saja','lagi sibuk'):
            self.assertTrue(BYE.search(text))
        self.assertFalse(BYE.search('YA'))
        self.assertFalse(BYE.search('mati lampu di kampung'))

    def test_smell_fire_and_negation_are_not_conflated(self):
        for text,expected in (('bau terbakar','bau'),('tidak ada api, cuma bau asap','bau'),
                              ('tidak ada asap tapi terlihat api','api'),('tidak ada asap tetapi ada api','api')):
            with self.subTest(text=text):
                self.assertEqual(smoke_category(text),expected)
        self.assertNotEqual(smoke_category('tidak terlihat api'),'api')
        self.assertNotEqual(water_category('air tidak sedikit'),'sedikit')
        self.assertEqual(water_category('air tidak sedikit, malah banjir'),'banjir')

    def test_shared_dispatcher_consent_observations_free_place_fire_and_bye(self):
        voice=self.app.state.voice
        def session():
            return dict(responder_id='r1',mode='replay',day=9,trace_id='t_persona',stage='consent',turn=0,empty=0,rounds=0,report={},diagnostic=False)
        state=session()
        self.assertTrue(voice._turn(state,'tidak','decline',0)['hangup'])
        for word in ('udahan','nanti saja','lagi sibuk','matiin ya'):
            self.assertTrue(voice._turn(session(),word,'bye',0)['hangup'])
        state=session()
        with patch.object(self.engine,'recalibrate',side_effect=AssertionError('No category assimilation')),patch.object(self.engine,'simulate',side_effect=AssertionError('No category simulation')):
            for turn,text in enumerate(('YA','air tinggal sedikit','tidak ada asap tapi terlihat api','Beje milik warga')):
                response=voice._turn(state,text,'qualitative',turn)
                self.assertEqual(response['text'].count('?'),1)
                self.assertNotIn('Berapa',response['text'])
                self.assertNotIn('kedalaman',response['text'])
                if turn==2:
                    self.assertIn('Jauhi lokasi, jangan padamkan sendiri',response['text'])
            self.assertEqual(state['stage'],'confirm')
            self.assertIn('air tinggal sedikit',response['text'])
            self.assertIn('Beje milik warga',response['text'])
            self.assertEqual(self.client.get('/api/observations').json()['items'],[])
            result=voice._turn(state,'YA','qualitative',4)
            self.assertTrue(result['hangup'])
        observation=self.client.get('/api/observations').json()['items'][0]
        self.assertIsNone(observation['wt_cm'])
        self.assertEqual(observation['place_text'],'Beje milik warga')
        self.assertEqual(self.client.get('/api/actions').json()['items'][0]['priority'],'URGENT')

    def test_numeric_mapped_fire_still_requires_urgent_human_verification(self):
        response=self.send('AIR 61 ASAP terlihat api dekat kanal empat')
        self.assertIn('Jauhi lokasi, jangan padamkan sendiri',response['items'][-1]['body'])
        self.send('YA')
        actions=self.client.get('/api/actions').json()['items']
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0]['kind'],'verify')
        self.assertEqual(actions[0]['priority'],'URGENT')

    def test_generic_yes_to_smoke_question_requires_clarification(self):
        voice=self.app.state.voice
        for answer in ('ya','iya','ada'):
            state=dict(responder_id='r1',mode='replay',day=9,trace_id='t_ambiguity',stage='smoke',turn=1,empty=0,rounds=0,report={'water_category':'kering'},diagnostic=False)
            response=voice._turn(state,answer,'ambiguous',1)
            self.assertEqual(response['text'],'Apakah terlihat asap, atau hanya tercium bau?')
            self.assertEqual(state['stage'],'smoke')
            self.assertNotIn('smoke_category',state['report'])

    def test_additive_json_schema_and_future_p0a_greeting(self):
        report=ObservationInput(village_id='62.11.01.2002',responder_id='r1',water_category='kering',confirmed=True)
        self.assertIsNone(report.wt_cm)
        self.assertEqual(report.confidence_label,'LOW')
        self.assertEqual(ObservationInput(village_id=report.village_id,responder_id='r1',wt_cm=61).evidence_type,'numeric')
        text=greeting('Fadli','Talio')
        self.assertEqual(text.count('?'),1)
        self.assertIn('Ada waktu sebentar?',text)
        self.assertNotIn('Assalamualaikum',text)
        self.assertNotIn('BPBD',text)
        self.assertNotIn('Fadli +6281234567890',greeting('Fadli +6281234567890'))


if __name__=='__main__':
    unittest.main()
