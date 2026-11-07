"""Provider adapters. Fixture mode is the default; live requires explicit selection.

No environment files are read. Pass configured archive credentials explicitly.
"""
from __future__ import annotations

import csv
import io
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, quote

from .cache import AuthenticationError, DataUnavailable, DiskCache
from .villages import get_village

UTC = timezone.utc
FIRMS = 'https://firms.modaps.eosdis.nasa.gov'
LIVE_URLS = [f'{FIRMS}/data/active_fire/noaa-{n}-viirs-c2/csv/J{j}_VIIRS_C2_SouthEast_Asia_7d.csv'
             for n, j in ((20, 1), (21, 2))]
FIXTURES = Path(__file__).parent / 'fixtures'


def finite(value):
    if value is None or value == '':
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite provider value')
    return result


def parse_weather(body: str) -> list[dict]:
    payload = json.loads(body.lstrip("\ufeff"))
    daily, hourly = payload['daily'], payload.get('hourly', {})
    dates = daily['time']
    if not dates:
        raise ValueError('Empty weather response')
    soil = {}
    times = hourly.get('time', [])
    values = hourly.get('soil_moisture_0_to_7cm', [])
    if not values and times:
        # Forecast default models expose 0-1, 1-3 and 3-9 cm layers.
        # Approximate 0-7 cm assuming uniform moisture within the 3-9 cm layer.
        layers = [hourly.get(f'soil_moisture_{depth}cm', [])
                  for depth in ('0_to_1', '1_to_3', '3_to_9')]
        if all(len(layer) == len(times) for layer in layers):
            values = [None if any(v is None for v in triple) else
                      sum(finite(v) * w for v, w in zip(triple, (1, 2, 4))) / 7
                      for triple in zip(*layers)]
    if len(times) != len(values):
        raise ValueError('Mismatched soil arrays')
    for stamp, value in zip(times, values):
        value = finite(value)
        if value is not None:
            if not 0 <= value <= 1:
                raise ValueError('Invalid volumetric moisture')
            soil.setdefault(stamp[:10], []).append(value * 100)
    fields = ('precipitation_sum', 'et0_fao_evapotranspiration', 'temperature_2m_mean')
    if any(len(daily[k]) != len(dates) for k in fields):
        raise ValueError('Mismatched weather arrays')
    rows = []
    for i, day in enumerate(dates):
        date.fromisoformat(day)
        rain, et0, temperature = [finite(daily[k][i]) for k in fields]
        if any(v is not None and v < 0 for v in (rain, et0)):
            raise ValueError('Negative water forcing')
        sm = soil.get(day, [])
        rows.append({'date': day, 'rain_mm': rain, 'et0_mm': et0,
                     'temperature_c': temperature, 'soil_moisture_pct': sum(sm)/len(sm) if sm else None,
                     'missing_reason': '; '.join(label for label, value in
                         [('Rain unavailable', rain), ('ET0 unavailable', et0),
                          ('Temperature unavailable', temperature),
                          ('Mineral-soil moisture unavailable', 1 if sm else None)]
                         if value is None) or None})
    return rows


def parse_hotspots(body: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(body.lstrip("\ufeff")))
    if not {'latitude', 'longitude', 'acq_date', 'acq_time', 'confidence', 'frp'}.issubset(reader.fieldnames or []):
        raise ValueError('Invalid FIRMS CSV headers')
    rows = []
    for row in reader:
        lat, lon = finite(row['latitude']), finite(row['longitude'])
        if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError('Invalid hotspot position')
        stamp = datetime.strptime(row['acq_date'] + row['acq_time'].zfill(4), '%Y-%m-%d%H%M').replace(tzinfo=UTC)
        satellite = row.get('satellite', 'unknown')
        frp = finite(row['frp'])
        if frp is not None and frp < 0:
            raise ValueError('Negative FRP')
        rows.append({'id': f'{satellite}:{stamp.isoformat()}:{lat}:{lon}', 'lat': lat, 'lon': lon,
                     'detected_at': stamp.isoformat().replace('+00:00', 'Z'),
                     'confidence': {'l': 'low', 'n': 'nominal', 'h': 'high'}.get(row['confidence'].lower(), row['confidence']),
                     'frp': frp, 'satellite': satellite, 'source': 'measured'})
    return rows


def parse_bmkg(body: str) -> dict:
    value = json.loads(body.lstrip("\ufeff"))
    if not isinstance(value, dict) or not isinstance(value.get('data'), list) or not value['data']:
        raise ValueError('Invalid BMKG response')
    return value


def _stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat().replace('+00:00', 'Z')


class Ingest:
    def __init__(self, cache: DiskCache | None = None, *, mode: str = 'fixture'):
        if mode not in ('fixture', 'live'):
            raise ValueError('mode must be fixture or live')
        self.cache = cache
        self.mode = mode
        if mode == 'live' and cache is None:
            raise ValueError('Explicit disk cache required for live mode')

    def weather(self, village_id: str, *, start: date | None = None,
                end: date | None = None, days: int = 14) -> dict:
        village = get_village(village_id)
        if not 1 <= days <= 16:
            raise ValueError('Forecast days must be 1..16')
        if (start is None) != (end is None) or (start and end < start):
            raise ValueError('Archive requires an ordered start/end pair')
        if start and (end - start).days + 1 > 366:
            raise ValueError('Weather archive range must be at most 366 days')
        if self.mode == 'fixture':
            rows = parse_weather((FIXTURES / 'weather.json').read_text(encoding='utf-8'))
            first = start or date(2019, 6, 15)
            count = (end - first).days + 1 if end else days
            rows = [dict(rows[i % len(rows)], date=(first + timedelta(days=i)).isoformat()) for i in range(count)]
            return {'items': rows, 'source': 'fixture', 'stale': False, 'available': True,
                    'data_as_of': '2019-06-15T00:00:00Z',
                    'assumptions': ['Synthetic weather fixture; dates may be shifted; not ERA5 observations',
                                    'Mineral soil moisture proxy, not peat moisture']}
        params = {'latitude': village['lat'], 'longitude': village['lon'], 'timezone': 'Asia/Jakarta',
                  'daily': 'precipitation_sum,et0_fao_evapotranspiration,temperature_2m_mean',
                  'hourly': 'soil_moisture_0_to_7cm'}
        if start:
            host = 'https://archive-api.open-meteo.com/v1/archive'
            params.update(start_date=start.isoformat(), end_date=end.isoformat(), models='era5_land')
        else:
            host = 'https://api.open-meteo.com/v1/forecast'
            params['forecast_days'] = days
            params['hourly'] = 'soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_moisture_3_to_9cm'
        result = self.cache.get(host + '?' + urlencode(params), parse_weather)
        return {'items': result['data'], 'available': True, 'source': 'modelled',
                'stale': result['stale'], 'cached': result['cached'], 'data_as_of': _stamp(result['fetched_at']),
                'assumptions': ['Open-Meteo model forcing, not measured groundwater',
                                'Mineral soil moisture proxy, not peat moisture'] +
                               (['ERA5 historical perfect-forecast input'] if start else
                                ['Forecast 0-7 cm proxy: depth-weighted 0-1, 1-3, 3-9 cm; assumes uniform 3-9 cm layer'])}

    def bmkg(self, village_id: str) -> dict:
        village = get_village(village_id)
        if self.mode == 'fixture':
            return {'available': False, 'source': 'fixture', 'stale': False, 'data_as_of': None,
                    'reason': 'No BMKG observation in synthetic fixture', 'items': []}
        result = self.cache.get('https://api.bmkg.go.id/publik/prakiraan-cuaca?' +
                                urlencode({'adm4': village['id']}), parse_bmkg, ttl=10800)
        return {'available': True, 'source': 'modelled', 'stale': result['stale'],
                'data_as_of': _stamp(result['fetched_at']), 'items': result['data']['data'],
                'assumptions': ['Unverified seed adm4; BMKG forecasts are supplementary']}

    def hotspots(self) -> dict:
        if self.mode == 'fixture':
            rows = parse_hotspots((FIXTURES / 'hotspots.csv').read_text(encoding='utf-8'))
            for row in rows:
                row['source'] = 'fixture'
            return {'items': rows, 'available': True, 'source': 'fixture', 'stale': False,
                    'data_as_of': '2019-06-15T00:00:00Z',
                    'assumptions': ['Synthetic heat detections; not confirmed fires']}
        return self._hotspot_urls(LIVE_URLS)

    def _hotspot_urls(self, urls: list[str]) -> dict:
        rows, results, failed = {}, [], 0
        for url in urls:
            try:
                result = self.cache.get(url, parse_hotspots, ttl=900)
            except AuthenticationError:
                raise
            except DataUnavailable:
                failed += 1
                continue
            results.append(result)
            for row in result['data']:
                rows[row['id']] = row
        if not results:
            raise DataUnavailable('Hotspot sources unavailable; no usable cache')
        return {'items': sorted(rows.values(), key=lambda r: r['detected_at']), 'available': True,
                'source': 'measured', 'stale': any(r['stale'] for r in results),
                'partial': bool(failed), 'unavailable_sources': failed,
                'data_as_of': _stamp(min(r['fetched_at'] for r in results)),
                'assumptions': ['Satellite heat detections, not confirmed fires']}

    def archive_hotspots(self, start: date, end: date, *, map_key: str | None = None,
                         bbox: tuple[float, float, float, float] = (113.8, -3.5, 114.5, -2.5),
                         sources: tuple[str, ...] = ('VIIRS_NOAA20_SP',)) -> dict:
        if self.mode != 'live':
            return {'items': [], 'available': False, 'source': 'fixture', 'stale': False,
                    'data_as_of': None, 'reason': 'Historical FIRMS coverage not represented by fixtures'}
        if not map_key:
            return {'items': [], 'available': False, 'stale': False, 'data_as_of': None,
                    'reason': 'FIRMS_MAP_KEY unset'}
        if end < start or (end - start).days > 366:
            raise ValueError('Archive range must be ordered and at most 367 days')
        if not sources or any(s not in ('VIIRS_NOAA20_SP', 'VIIRS_NOAA21_NRT', 'VIIRS_NOAA21_SP') for s in sources):
            raise ValueError('Unsupported historical sensor')
        west, south, east, north = bbox
        if not all(math.isfinite(v) for v in bbox) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError('Invalid bounding box')
        urls, day = [], start
        while day <= end:
            count = min(5, (end - day).days + 1)
            for source in sources:
                urls.append(f'{FIRMS}/api/area/csv/{quote(map_key, safe="")}/{source}/' +
                            ','.join(str(v) for v in bbox) + f'/{count}/{day.isoformat()}')
            day += timedelta(days=count)
        result = self._hotspot_urls(urls)
        result['assumptions'].append('Archive sensor availability must be verified; no fabricated 2019 NOAA-21 coverage')
        return result
