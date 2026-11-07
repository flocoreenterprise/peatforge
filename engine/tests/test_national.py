import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from pathlib import Path

from engine.cache import DataUnavailable
from engine.national import NationalService, ProvinceIndex

NOW=datetime(2026,11,7,4,tzinfo=timezone.utc).timestamp()


def ring(x,y,size=4):
    return [[x,y],[x+size,y],[x+size,y+size],[x,y+size],[x,y]]


def feature(name,coordinates,kind='Polygon'):
    return dict(type='Feature',properties={'shapeName':name},geometry={'type':kind,'coordinates':coordinates})


class Provider:
    def __init__(self,items,**metadata):
        self.items=items;self.metadata=metadata;self.calls=0
    def hotspots(self):
        self.calls+=1
        return dict(items=self.items,available=True,source='measured',stale=False,
                    data_as_of='2026-11-07T04:00:00Z',**self.metadata)


def detection(identity,lat,lon,age=0):
    return dict(id=identity,lat=lat,lon=lon,detected_at=datetime.fromtimestamp(NOW-age*86400,timezone.utc).isoformat(),
                source='measured',confidence='nominal',frp=10)


class NationalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'synthetic.geojson'
        # A has an interior hole. B is an archipelago with a shared outer border.
        features=[feature('A',[ring(0,0),ring(1,1,1)]),
                  feature('B',[[ring(4,0)],[ring(20,0)]],kind='MultiPolygon'),
                  feature('Triangle',[[[30,0],[34,0],[30,4],[30,0]]])]
        self.path.write_text(json.dumps(dict(type='FeatureCollection',features=features)))
        self.index=ProvinceIndex(self.path)

    def test_true_polygon_holes_multipolygon_and_bbox_rejection(self):
        self.assertEqual(self.index.province_for(.5,.5),'A')
        self.assertIsNone(self.index.province_for(1.5,1.5))
        self.assertEqual(self.index.province_for(2,21),'B')
        self.assertIsNone(self.index.province_for(3.5,33.5))
        self.assertEqual(self.index.province_for(.5,30.5),'Triangle')
        self.assertIsNone(self.index.province_for(50,50))

    def test_boundaries_hole_edges_and_shared_tie_assigned_once(self):
        self.assertEqual(self.index.province_for(0,0),'A')
        self.assertEqual(self.index.province_for(1.5,1),'A')
        self.assertEqual(self.index.province_for(2,4),'A')
        self.assertIsNone(self.index.province_for(float('nan'),0))

    def test_counts_time_dedupe_all_matching_detections_and_unmatched(self):
        recent=detection('a',.5,.5,.5)
        source=Provider([recent,recent.copy(),detection('b',1,21,2),
                         detection('old',.5,.5,8),detection('future',.5,.5,-1),
                         detection('regional',10,50,.5),detection('hole',1.5,1.5,.5)])
        service=NationalService(source,clock=lambda:NOW,boundaries_path=self.path)
        one=service.hotspots(1)
        self.assertEqual(one['total_matched'],1)
        self.assertEqual(one['by_province']['A'],1)
        self.assertEqual(one['regional_total'],3)
        self.assertEqual(one['unmatched_regional_count'],2)
        seven=service.hotspots(7)
        self.assertEqual(seven['total_matched'],2)
        self.assertEqual(seven['by_province']['B'],1)
        self.assertEqual(sum(seven['by_province'].values()),seven['total_matched'])
        self.assertEqual(source.calls,1)

    def test_snapshot_cache_expiry_provenance_partial_and_copy_isolation(self):
        now=[NOW]
        provider=Provider([detection('a',.5,.5)],partial=True,unavailable_sources=1)
        service=NationalService(provider,clock=lambda:now[0],boundaries_path=self.path)
        first=service.hotspots(1)
        self.assertTrue(first['partial'])
        self.assertEqual(first['unavailable_sources'],1)
        self.assertEqual(first['data_as_of'],'2026-11-07T04:00:00Z')
        first['items'].clear()
        with patch.object(service._index,'province_for',side_effect=AssertionError('Do not classify every poll')):
            now[0]+=299
            self.assertEqual(service.hotspots(1)['total_matched'],1)
            self.assertEqual(service.hotspots(7)['total_matched'],1)
        self.assertEqual(provider.calls,1)
        now[0]+=1
        self.assertEqual(service.hotspots(1)['total_matched'],1)
        self.assertEqual(provider.calls,2)

    def test_all_provider_failures_raise_not_empty_success(self):
        class Failed:
            def hotspots(self):
                raise DataUnavailable('Providers unavailable')
        with self.assertRaises(DataUnavailable):
            NationalService(Failed(),boundaries_path=self.path).hotspots(1)
        for days in [0,8,True,1.5]:
            with self.assertRaises(ValueError):
                NationalService(Provider([]),boundaries_path=self.path).hotspots(days)
        with self.assertRaises(ValueError):
            NationalService(Provider([]),cache_seconds=4)

    def test_real_downloaded_data_basic_sanity(self):
        actual=ProvinceIndex()
        self.assertEqual(len(actual.names),34)
        self.assertEqual(actual.province_for(-3.1222040246,114.0981588862),'Central Kalimantan')
        self.assertEqual(actual.province_for(-8.65,115.22),'Bali')
        self.assertIsNone(actual.province_for(13.75,100.5))


if __name__=='__main__':
    unittest.main()
