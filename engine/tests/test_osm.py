import json
import tempfile
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from engine.osm import OSMCanals,parse_waterways
from engine.cache import DataUnavailable,DiskCache
from engine.ingest import Ingest
from engine.service import EngineService
from engine.api import create_router


def payload():
    way=dict(type='way',id=123,tags={'waterway':'canal','name':'Synthetic mapped canal'},
             geometry=[{'lat':-3.1,'lon':114.1},{'lat':-3.11,'lon':114.12}])
    return {'elements':[way,way.copy(),dict(way,id=124,tags={'waterway':'stream'})]}


class OSMTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)

    def test_real_geometry_transform_dedupe_and_honest_spacing(self):
        calls=[]
        def fetch(url,timeout):
            calls.append(url)
            return json.dumps(payload())
        osm=OSMCanals(DiskCache(self.temp.name,fetcher=fetch))
        result=osm.feature_collection()
        self.assertTrue(result['available'])
        self.assertEqual(result['feature_count'],1)
        self.assertEqual(result['features'][0]['geometry']['coordinates'][0],[114.1,-3.1])
        self.assertEqual(result['features'][0]['properties']['waterway'],'canal')
        self.assertFalse(result['spacing']['available'])
        self.assertIsNone(result['spacing']['median_spacing_m'])
        self.assertEqual(result['spacing']['model_spacing_m'],400)
        self.assertIn('not historical2019',' '.join(result['assumptions']))
        result['features'].clear()
        self.assertEqual(len(osm.feature_collection()['features']),1)
        self.assertEqual(len(calls),1)

    def test_malformed_or_partial_geometry_unavailable(self):
        for data in [{'remark':'timeout','elements':[]},{'elements':[{'type':'way','id':1,'tags':{'waterway':'drain'}}]}]:
            with self.assertRaises(ValueError):
                parse_waterways(json.dumps(data))
        def fail(*args):
            raise OSError('synthetic provider outage')
        result=OSMCanals(DiskCache(self.temp.name,fetcher=fail,retries=0)).feature_collection()
        self.assertFalse(result['available'])
        self.assertEqual(result['features'],[])
        self.assertIsNone(result['spacing']['median_spacing_m'])

    def test_stale_disk_cache_and_fixture_api(self):
        cache=DiskCache(self.temp.name,fetcher=lambda *args:json.dumps(payload()),clock=lambda:0)
        original=OSMCanals(cache).feature_collection()
        cache.clock=lambda:90000
        cache.fetcher=lambda *args: (_ for _ in ()).throw(OSError('offline'))
        cache.retries=0
        stale=OSMCanals(cache).feature_collection()
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['features'],original['features'])
        service=EngineService(Ingest())
        app=FastAPI();app.include_router(create_router(service),prefix='/api')
        response=TestClient(app).get('/api/canals/osm')
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['available'])
        self.assertEqual(response.json()['type'],'FeatureCollection')
        self.assertEqual(service.hydraulic.spacing_m,400)


if __name__=='__main__':
    unittest.main()
