import copy
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.api import create_router
from engine.fire_qa import PARAMS, VOLCANOES, classify_events, distance_km, headline
from engine.national import NationalService

NOW=datetime(2026,11,7,6,tzinfo=timezone.utc)


def row(identity, *, lat=0, lon=0, hours=0, frp=10, satellite='N20'):
    return dict(id=identity,lat=lat,lon=lon,detected_at=(NOW-timedelta(hours=hours)).isoformat(),
                frp=frp,satellite=satellite,province='Test',source='fixture')


class FireQATests(unittest.TestCase):
    def test_both_inclusive_boundaries_and_transitivity(self):
        degree=math.degrees(1/PARAMS['earth_radius_km'])
        records=[row('a',lon=0,hours=96),row('b',lon=degree,hours=48),row('c',lon=2*degree)]
        self.assertAlmostEqual(distance_km(records[0],records[1]),1)
        events=classify_events(records)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['detection_count'],3)
        self.assertEqual(events[0]['first_seen'],records[0]['detected_at'])
        self.assertEqual(events[0]['last_seen'],records[-1]['detected_at'])
        self.assertEqual(len(classify_events([records[0],row('far',lon=degree*1.0001,hours=48)])),2)
        self.assertEqual(len(classify_events([records[0],row('late',lon=degree,hours=47.999)])),2)

    def test_metadata_dedupe_order_and_no_unsupported_land_cover(self):
        a=row('a',hours=1,frp=None)
        b=row('b',frp=25,satellite='N21')
        event=classify_events([b,a,a])[0]
        self.assertEqual(event['classification'],'tidak_pasti')
        self.assertFalse(event['confirmed'])
        self.assertEqual(event['max_frp'],25)
        self.assertEqual(event['satellites'],['N20','N21'])
        self.assertEqual(event['detection_ids'],['a','b'])
        self.assertEqual(event['id'],classify_events([a,b])[0]['id'])
        self.assertIn('OSM industri tidak tersedia',event['reasons'])
        self.assertEqual(headline([event])['land_event_count'],0)

    def test_persistence_distinct_days_not_repeated_detections(self):
        repeated=[row(str(i),hours=i/10) for i in range(10)]
        self.assertEqual(classify_events(repeated)[0]['classification'],'tidak_pasti')
        persistent=[row(str(i),hours=i*24) for i in range(5)]
        candidate=classify_events(persistent)[0]
        self.assertEqual(candidate['classification'],'industri')
        self.assertEqual(candidate['persistence_days'],5)
        self.assertEqual(candidate['confidence_label'],'LOW')
        self.assertTrue(any('bukan pabrik terverifikasi' in r for r in candidate['reasons']))
        self.assertEqual(classify_events(persistent[:4])[0]['classification'],'tidak_pasti')
        # A short display window still uses full feed context for persistence.
        self.assertEqual(classify_events(persistent[:1],context=persistent)[0]['classification'],'industri')

    def test_small_verified_gvp_list_and_precedence(self):
        volcano=VOLCANOES[0]
        hot=[row(str(i),lat=volcano['lat'],lon=volcano['lon'],hours=i*24) for i in range(5)]
        result=classify_events(hot)[0]
        self.assertEqual(result['classification'],'gunung_api')
        self.assertEqual(result['sources'][-1]['url'],volcano['url'])
        self.assertEqual(headline([result])['excluded_volcano_detection_count'],5)
        self.assertEqual(classify_events([row('away',lat=-7,lon=110)])[0]['classification'],'tidak_pasti')

    def test_optional_osm_evidence_and_positive_land_cover_only(self):
        detection=row('heat')
        osm=dict(available=True,items=[dict(lat=0,lon=0,tags={'landuse':'industrial'},url='https://www.openstreetmap.org/way/1')])
        event=classify_events([detection],osm_cache=osm)[0]
        self.assertEqual(event['classification'],'industri')
        self.assertEqual(event['confidence_label'],'MED')
        self.assertNotIn('OSM industri tidak tersedia',event['reasons'])
        for tag in ('works','flare'):
            osm['items'][0]['tags']={'man_made':tag}
            self.assertEqual(classify_events([detection],osm_cache=osm)[0]['classification'],'industri')
        source={'name':'synthetic coverage fixture','url':'https://example.test/coverage'}
        for key in ('peat','vegetation'):
            evidence=lambda r:{key:True,'source':source}
            self.assertEqual(classify_events([detection],land_evidence=evidence)[0]['classification'],
                             'gambut' if key=='peat' else 'lahan_vegetasi')
        self.assertEqual(classify_events([detection],land_evidence=lambda r:{'peat':False,'vegetation':False})[0]['classification'],'tidak_pasti')
        records=[detection,row('other',lon=.001)]
        partial=lambda r:{'peat':True,'source':source} if r['id']=='heat' else None
        self.assertEqual(classify_events(records,land_evidence=partial)[0]['classification'],'tidak_pasti')

    def test_api_filters_overview_compatibility_and_snapshots_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'provinces.geojson'
            path.write_text(json.dumps({'features':[{'properties':{'name':'Test'},'geometry':
                {'type':'Polygon','coordinates':[[[-1,-1],[2,-1],[2,2],[-1,2],[-1,-1]]]}}]}))
            records=[row('a'),row('b',lon=.001),row('c',lon=1)]
            class Provider:
                def hotspots(self):
                    return dict(items=copy.deepcopy(records),available=True,source='fixture',stale=True,
                                partial=True,data_as_of=NOW.isoformat(),assumptions=[])
            provider=Provider()
            backend=SimpleNamespace(ingest=provider,clock=lambda:NOW.timestamp())
            national=NationalService(provider,clock=backend.clock,boundaries_path=path)
            app=FastAPI()
            with patch('engine.api.NationalService',return_value=national):
                app.include_router(create_router(backend),prefix='/api')
                client=TestClient(app)
                response=client.get('/api/fire-events?class=tidak_pasti&province=Test&days=3')
                self.assertEqual(response.status_code,200)
                body=response.json()
                self.assertEqual(body['raw_count'],3)
                self.assertEqual(body['event_count'],2)
                self.assertTrue(body['stale'])
                self.assertTrue(body['partial'])
                self.assertEqual(body['rule_version'],'fire-qa-v1')
                self.assertTrue(body['snapshot_id'].startswith('fire-qa-v1-'))
                self.assertEqual({i for e in body['items'] for i in e['detection_ids']},{'a','b','c'})
                self.assertEqual(client.get('/api/fire-events?class=industri').json()['event_count'],0)
                self.assertEqual(client.get('/api/fire-events?since=2026-11-08T00:00:00Z').json()['items'],[])
                self.assertEqual(client.get('/api/fire-events?class=madeup').status_code,422)
                self.assertEqual(client.get('/api/fire-events?since=2026-11-07').status_code,422)
                overview=client.get('/api/national/overview?days=3').json()
                self.assertEqual(overview['headline']['land_event_count'],0)
                self.assertEqual(overview['headline']['uncertain_event_count'],2)
                self.assertIsNone(overview['headline']['peat_event_count'])
                self.assertIn('2 kejadian belum diketahui',overview['headline_sentence'])
                self.assertFalse(overview['evidence_coverage']['volcano_list_complete'])
                self.assertEqual(overview['by_province'][0]['raw_count'],3)
                raw=client.get('/api/national/hotspots?days=3').json()
                self.assertEqual(raw['items'],[])
                self.assertEqual(raw['by_province'],{'Test':3})
                self.assertEqual(raw['total_matched'],3)
                self.assertEqual(raw['raw_count'],3)
                self.assertEqual(raw['event_count'],2)
                self.assertEqual(raw['events_snapshot_id'],body['snapshot_id'])
                original=body['snapshot_id']
                with patch.dict(PARAMS,cluster_hours=47):
                    fresh=NationalService(provider,clock=backend.clock,boundaries_path=path)
                    self.assertNotEqual(fresh.fire_events(3)['snapshot_id'],original)


if __name__=='__main__':
    unittest.main()
