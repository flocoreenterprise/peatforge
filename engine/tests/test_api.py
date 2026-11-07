import unittest
from unittest.mock import patch

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from agents.models import ForecastPoint, Simulation, Status
from engine.api import create_router
from engine.cache import DataUnavailable
from engine.ingest import Ingest
from engine.service import EngineService

V='62.11.01.2002'


def app_for(service=None,dependency=None):
    app=FastAPI()
    app.include_router(create_router(service or EngineService(Ingest())),prefix='/api',
                       dependencies=[Depends(dependency)] if dependency else [])
    return app


class APITests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app_for())

    def test_light_national_counts_keep_metadata_and_omit_points(self):
        snapshot={'items':[{'id':'synthetic-point'}],'by_province':{'Test province':3},
                  'total_matched':3,'coverage':{'units':34,'boundary_year':2017},'stale':True}
        with patch('engine.api.NationalService.hotspots',return_value=snapshot) as call:
            response=self.client.get('/api/national/hotspots?days=7')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['by_province'],snapshot['by_province'])
            self.assertEqual(response.json()['items'],[])
            self.assertTrue(response.json()['points_omitted'])
            self.assertTrue(response.json()['stale'])
            call.assert_called_once_with(7)
            for day in (0,8):
                self.assertEqual(self.client.get(f'/api/national/hotspots?days={day}').status_code,422)

    def test_read_shapes_and_replay_aliases(self):
        response=self.client.get('/api/villages')
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json()['items']),5)
        self.assertEqual(response.json()['items'][0]['source'],'provider')
        status=self.client.get(f'/api/villages/{V}/status?mode=replay&day=9')
        self.assertEqual(status.status_code,200)
        Status.model_validate(status.json())
        points=self.client.get(f'/api/villages/{V}/forecast?mode=replay&day=61').json()['items']
        self.assertEqual(len(points),14)
        for point in points:
            ForecastPoint.model_validate(point)
        for suffix in ['canals','resources','params/history']:
            self.assertEqual(self.client.get(f'/api/villages/{V}/{suffix}').status_code,200)
        self.assertEqual(self.client.get('/api/replay/meta').json()['days'],62)
        first=self.client.get('/api/replay/day/12').json()
        alias=self.client.get('/api/replay/2019/day/12').json()
        self.assertEqual(first,alias)
        self.assertEqual(self.client.get('/api/map/risk?date=2019-06-27').json()['type'],'FeatureCollection')

    def test_live_hotspot_nonzero_day_rejected_before_fetch(self):
        service=EngineService(Ingest())
        with patch.object(service.ingest,'hotspots',side_effect=AssertionError('Must reject before fetch')) as fetch:
            response=TestClient(app_for(service)).get('/api/hotspots?mode=live&day=1')
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['error']['code'],'invalid')
        fetch.assert_not_called()

    def test_simulate_strict_agent_shape(self):
        response=self.client.post(f'/api/villages/{V}/simulate',json={
            'type':'canal_block','targets':['K4'],'delta_hc_cm':60,'days':14,'mode':'replay','day':9})
        self.assertEqual(response.status_code,200)
        Simulation.model_validate(response.json())

    def test_unavailable_is_safe_503(self):
        service=EngineService(Ingest())
        def fail(*args,**kwargs):
            raise DataUnavailable('PRIVATE-DONT-PRINT')
        service.village_status=fail
        response=TestClient(app_for(service)).get(f'/api/villages/{V}/status')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json(),{'error':{'code':'unavailable','message':'Data belum tersedia'}})
        self.assertNotIn('PRIVATE',response.text)

    def test_invalid_bounds_safe_errors_and_unknown_village(self):
        for path in ['/api/replay/day/999999999999999999999','/api/replay/day/62','/api/replay/not2019/day/0',
                     f'/api/villages/{V}/status?mode=bad',f'/api/villages/{V}/forecast?days=90',
                     '/api/hotspots?radius_km=nan','/api/map/risk?date=invalid']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,422,path)
            self.assertEqual(response.json()['error']['code'],'invalid')
        self.assertEqual(self.client.get('/api/villages/bad/status').status_code,404)
        self.assertEqual(self.client.post(f'/api/villages/{V}/simulate',json={
            'type':'canal_block','targets':['K4'],'days':90,'secret':'PRIVATE'}).status_code,422)
        bad=self.client.post(f'/api/villages/{V}/simulate',content='{"type":NaN}',headers={'Content-Type':'application/json'})
        self.assertEqual(bad.status_code,422)
        self.assertNotIn('NaN',bad.text)

    def test_nonfinite_response_never_json(self):
        service=EngineService(Ingest())
        service.village_status=lambda *args: {'pfvi':float('nan')}
        response=TestClient(app_for(service)).get(f'/api/villages/{V}/status')
        self.assertEqual(response.status_code,503)
        self.assertNotIn('NaN',response.text)

    def test_hotspots_replay_unavailable_backtest_and_warmup(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('network prohibited')):
            self.assertFalse(self.client.get('/api/hotspots?mode=replay&day=9').json()['available'])
            self.assertFalse(self.client.get(f'/api/backtest?village_id={V}').json()['available'])
            self.assertEqual(self.client.post('/api/warmup?mode=replay',json={}).json(),{'ready':True,'villages':5})
            self.assertEqual(self.client.post('/api/warmup',json={'days':999}).status_code,422)

    def test_parent_dependency_covers_every_route(self):
        def deny():
            raise HTTPException(401,'auth required')
        client=TestClient(app_for(dependency=deny))
        for method,path,payload in [('GET','/api/villages',None),('GET','/api/replay/meta',None),
                                    ('POST','/api/warmup',{}),('POST',f'/api/villages/{V}/simulate',{})]:
            response=client.request(method,path,json=payload)
            self.assertEqual(response.status_code,401)


if __name__=='__main__':
    unittest.main()
