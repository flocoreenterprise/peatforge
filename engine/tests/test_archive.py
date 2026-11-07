import tempfile
import unittest
from datetime import date
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from engine.archive import FirmsArchive
from engine.cache import DiskCache
from engine.ingest import Ingest
from engine.service import EngineService
from engine.api import create_router

HEADER='latitude,longitude,acq_date,acq_time,confidence,frp,satellite\n'


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)

    def fetch(self,url,timeout):
        day=url.rsplit('/',1)[-1]
        return HEADER+f'-3.10,114.11,{day},0030,n,8.2,N\n-3.10,114.11,{day},0030,n,8.2,N\n0,0,{day},0030,n,10,N\n'

    def test_complete13chunks_union_overlap_and_cache_secret_free(self):
        calls=[]
        def fetch(url,timeout):
            calls.append(1)
            return self.fetch(url,timeout)
        archive=FirmsArchive(DiskCache(self.temp.name,fetcher=fetch),map_key='SYNTHETIC_KEY')
        result=archive.hotspots()
        self.assertEqual(len(calls),13)
        self.assertEqual(result['total_unique'],13)
        self.assertGreater(sum(result['by_village'].values()),13)
        self.assertTrue(result['date_coverage']['complete'])
        self.assertEqual(result['source'],'NASA FIRMS archive')
        self.assertEqual(len(result['date_coverage']['successful_ranges']),13)
        self.assertEqual(archive.hotspots(),result)
        self.assertEqual(len(calls),13)
        for path in Path(self.temp.name).glob('*'):
            self.assertNotIn('SYNTHETIC_KEY',path.name)
            self.assertNotIn('SYNTHETIC_KEY',path.read_text())

    def test_missingkey_unavailable_and_partial_not_fulltotal(self):
        archive=FirmsArchive(DiskCache(self.temp.name,fetcher=self.fetch),map_key='')
        self.assertFalse(archive.hotspots()['available'])
        def partial(url,timeout):
            if url.endswith('/2019-06-20'):
                raise OSError('synthetic unavailable')
            return self.fetch(url,timeout)
        result=FirmsArchive(DiskCache(self.temp.name,fetcher=partial,retries=0),map_key='test').hotspots()
        self.assertTrue(result['partial'])
        self.assertIsNone(result['total_unique'])
        self.assertEqual(result['retrieved_unique'],12)
        self.assertFalse(result['date_coverage']['complete'])

    def test_replay_window_and_summary_api(self):
        service=EngineService(Ingest())
        service.archive=FirmsArchive(DiskCache(self.temp.name,fetcher=self.fetch),map_key='test')
        full=service.archive_summary()
        filtered=service.get_hotspots(mode='replay',day=0,hours=24)
        self.assertEqual(len(filtered['items']),1)
        self.assertEqual(filtered['items'][0]['detected_at'],'2019-06-15T00:30:00Z')
        self.assertEqual(filtered['source'],'NASA FIRMS archive')
        app=FastAPI();app.include_router(create_router(service),prefix='/api')
        response=TestClient(app).get('/api/replay/hotspots/summary')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['total_unique'],13)
        self.assertNotIn('items',response.json())


if __name__=='__main__':
    unittest.main()
