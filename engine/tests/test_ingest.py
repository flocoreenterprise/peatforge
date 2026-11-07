import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from engine.cache import AuthenticationError, DataUnavailable, DiskCache, cache_key
from engine.ingest import FIXTURES, Ingest, parse_hotspots, parse_weather
from engine.villages import canals, get_village, hamlets, list_villages, wells

VILLAGE = '62.11.01.2002'
WEATHER = (FIXTURES / 'weather.json').read_text(encoding='utf-8-sig')
HOTSPOTS = (FIXTURES / 'hotspots.csv').read_text(encoding='utf-8-sig')


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_sha256_redacts_query_and_path_credentials(self):
        self.assertEqual(cache_key('https://x/a?api_key=SECRET&z=1'), cache_key('https://x/a?api_key=OTHER&z=1'))
        self.assertEqual(cache_key('https://x/api/area/csv/SECRET/source'), cache_key('https://x/api/area/csv/OTHER/source'))
        self.assertEqual(len(cache_key('https://x')), 64)

    def test_refresh_failure_uses_stale_validated_cache(self):
        calls = []
        def fetch(url, timeout):
            calls.append(timeout)
            if len(calls) > 1:
                raise OSError('offline')
            return WEATHER
        now = [100]
        cache = DiskCache(self.temp.name, fetcher=fetch, retries=1, sleeper=lambda _: None, clock=lambda: now[0])
        self.assertFalse(cache.get('https://weather', parse_weather)['stale'])
        self.assertTrue(cache.get('https://weather', parse_weather)['cached'])
        now[0] = 5000
        stale = cache.get('https://weather', parse_weather)
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['data'][0]['soil_moisture_pct'], 31)
        self.assertEqual(calls, [10, 10, 10])
        self.assertEqual(len(list(Path(self.temp.name).glob('*.json'))), 1)

    def test_invalid_refresh_does_not_poison_cache(self):
        cache = DiskCache(self.temp.name, fetcher=lambda *_: WEATHER, clock=lambda: 0)
        cache.get('https://weather', parse_weather)
        cache.fetcher = lambda *_: '{}'
        cache.clock = lambda: 4000
        self.assertTrue(cache.get('https://weather', parse_weather)['stale'])

    def test_auth_never_uses_cache_or_fixture(self):
        cache = DiskCache(self.temp.name, fetcher=lambda *_: HOTSPOTS, clock=lambda: 0)
        cache.get('https://hotspots', parse_hotspots)
        def reject(*_):
            raise HTTPError('https://provider/secret', 401, 'no', {}, None)
        cache.fetcher = reject
        cache.clock = lambda: 4000
        with self.assertRaises(AuthenticationError) as ctx:
            cache.get('https://hotspots', parse_hotspots)
        self.assertNotIn('secret', str(ctx.exception))

    def test_missing_and_corrupt_cache_are_unavailable(self):
        cache = DiskCache(self.temp.name, fetcher=lambda *_: (_ for _ in ()).throw(OSError()), retries=0)
        with self.assertRaises(DataUnavailable):
            cache.get('https://empty', parse_weather)
        (Path(self.temp.name) / (cache_key('https://empty') + '.json')).write_text('corrupt')
        with self.assertRaises(DataUnavailable):
            cache.get('https://empty', parse_weather)


class IngestTests(unittest.TestCase):
    def test_fixture_default_never_network(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network prohibited')):
            ingest = Ingest()
            weather = ingest.weather(VILLAGE)
            self.assertEqual(len(weather['items']), 14)
            self.assertEqual(weather['source'], 'fixture')
            self.assertEqual(ingest.hotspots()['items'][0]['source'], 'fixture')
            self.assertFalse(ingest.bmkg(VILLAGE)['available'])
            archive = ingest.weather(VILLAGE, start=date(2019, 6, 15), end=date(2019, 8, 15))
            self.assertEqual(len(archive['items']), 62)
            self.assertIn('Synthetic', archive['assumptions'][0])
            self.assertFalse(ingest.archive_hotspots(date(2019, 6, 15), date(2019, 6, 20))['available'])

    def test_weather_archive_range_is_bounded_before_fetch(self):
        start = date(2019, 1, 1)
        self.assertEqual(len(Ingest().weather(VILLAGE, start=start,
                                             end=start + timedelta(days=365))['items']), 366)
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp, fetcher=lambda *_: self.fail('Range rejection must precede network'))
            for ingest in (Ingest(), Ingest(cache, mode='live')):
                with self.assertRaisesRegex(ValueError, 'at most 366 days'):
                    ingest.weather(VILLAGE, start=start, end=start + timedelta(days=366))

    def test_seed_features_are_unverified_and_isolated(self):
        villages = list_villages()
        self.assertEqual(len(villages), 5)
        self.assertTrue(all(v['source'] == 'fixture' and not v['verified'] for v in villages))
        villages[0]['name'] = 'mutated'
        self.assertEqual(get_village(VILLAGE)['name'], 'Talio')
        self.assertEqual(len(canals(VILLAGE)), 6)
        self.assertEqual(len(wells(VILLAGE)), 4)
        self.assertIn('parit empat', hamlets(VILLAGE)[0]['aliases'])
        with self.assertRaises(KeyError):
            get_village('bad')

    def test_weather_missing_value_is_null_not_zero(self):
        payload = json.loads(WEATHER)
        payload['daily']['precipitation_sum'][0] = None
        payload['hourly'] = {}
        result = parse_weather(json.dumps(payload))
        self.assertIsNone(result[0]['rain_mm'])
        self.assertIsNone(result[0]['soil_moisture_pct'])
        payload['daily']['precipitation_sum'][0] = float('nan')
        with self.assertRaises(ValueError):
            parse_weather(json.dumps(payload))

    def test_forecast_depth_weighted_soil_proxy(self):
        payload = json.loads(WEATHER)
        payload['hourly'] = {'time': ['2019-06-15T00:00'],
                             'soil_moisture_0_to_1cm': [0.1],
                             'soil_moisture_1_to_3cm': [0.2],
                             'soil_moisture_3_to_9cm': [0.3]}
        self.assertAlmostEqual(parse_weather(json.dumps(payload))[0]['soil_moisture_pct'], 100 * 1.7 / 7)

    def test_hotspot_time_and_confidence(self):
        row = parse_hotspots(HOTSPOTS)[0]
        self.assertEqual(row['detected_at'], '2019-06-15T00:30:00Z')
        self.assertEqual(row['confidence'], 'nominal')
        self.assertEqual(row['frp'], 8.2)
        with self.assertRaises(ValueError):
            parse_hotspots('<html>error</html>')

    def test_live_two_failures_error_partial_success_labelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fail(*_):
                raise OSError('offline')
            cache = DiskCache(tmp, fetcher=fail, retries=0)
            ingest = Ingest(cache, mode='live')
            with self.assertRaises(DataUnavailable):
                ingest.hotspots()
            cache.fetcher = lambda url, _: HOTSPOTS if 'noaa-20-' in url else fail()
            result = ingest.hotspots()
            self.assertTrue(result['partial'])
            self.assertEqual(len(result['items']), 1)
            self.assertFalse(result['stale'])

    def test_live_urls_and_archive_five_day_chunks_without_network(self):
        urls = []
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(url, timeout):
                urls.append(url)
                return HOTSPOTS if 'firms' in url else WEATHER
            ingest = Ingest(DiskCache(tmp, fetcher=fetch), mode='live')
            weather = ingest.weather(VILLAGE, start=date(2019, 6, 15), end=date(2019, 6, 25))
            self.assertEqual(weather['source'], 'modelled')
            self.assertIn('archive-api.open-meteo.com', urls[0])
            self.assertIn('timezone=Asia%2FJakarta', urls[0])
            result = ingest.archive_hotspots(date(2019, 6, 15), date(2019, 6, 25), map_key='synthetic-test-key')
            archive_urls = [u for u in urls if 'firms' in u]
            self.assertEqual(len(archive_urls), 3)
            self.assertTrue(archive_urls[0].endswith('/5/2019-06-15'))
            self.assertTrue(archive_urls[-1].endswith('/1/2019-06-25'))
            self.assertFalse(result['partial'])
            for path in Path(tmp).glob('*'):
                self.assertNotIn('synthetic-test-key', path.name)
                self.assertNotIn('synthetic-test-key', path.read_text())

    def test_bmkg_cached_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            ingest = Ingest(DiskCache(tmp, fetcher=lambda *_: '{"data":[{"cuaca":[]}]}'), mode='live')
            self.assertEqual(ingest.bmkg(VILLAGE)['source'], 'modelled')


if __name__ == '__main__':
    unittest.main()
