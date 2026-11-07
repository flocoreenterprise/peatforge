"""Current OSM mapped waterways near pilot reference points; not hydraulic survey."""
from __future__ import annotations

import copy
import json
import math
import threading
from urllib.parse import urlencode

from .cache import DataUnavailable,DiskCache
from .ingest import _stamp
from .villages import list_villages

SOURCE='OpenStreetMap via Overpass'
SPACING={'available':False,'median_spacing_m':None,
         'reason':'Mapped waterways do not establish parallel hydraulic control channels or surveyed spacing',
         'model_spacing_m':400,'model_spacing_source':'team assumption'}


def parse_waterways(body:str)->list[dict]:
    payload=json.loads(body.lstrip('\ufeff'))
    if not isinstance(payload,dict) or not isinstance(payload.get('elements'),list) or payload.get('remark'):
        raise ValueError('Overpass data unavailable or incomplete')
    features={}
    for element in payload['elements']:
        if element.get('type')!='way':
            continue
        tags=element.get('tags',{})
        if tags.get('waterway') not in ('canal','ditch','drain'):
            continue
        identity=element.get('id')
        if not isinstance(identity,int) or identity<=0:
            raise ValueError('Invalid OSM way identifier')
        points=element.get('geometry')
        if not isinstance(points,list) or len(points)<2:
            raise ValueError('Waterway geometry unavailable')
        coordinates=[]
        for point in points:
            lat,lon=float(point['lat']),float(point['lon'])
            if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:
                raise ValueError('Invalid waterway geometry')
            coordinates.append([lon,lat])
        features[identity]={'type':'Feature','id':f'way/{identity}',
            'geometry':{'type':'LineString','coordinates':coordinates},
            'properties':{'id':f'way/{identity}','waterway':tags['waterway'],
                          'name':tags.get('name'),'source':SOURCE,
                          'osm_url':f'https://www.openstreetmap.org/way/{identity}'}}
    return sorted(features.values(),key=lambda f:f['id'])


def unavailable_osm(reason='Current OSM geometry not represented by fixtures'):
    return {'type':'FeatureCollection','features':[],'available':False,'source':SOURCE,
            'attribution':'© OpenStreetMap contributors, ODbL1.0',
            'license':'https://www.openstreetmap.org/copyright','reason':reason,
            'data_as_of':None,'stale':False,'spacing':copy.deepcopy(SPACING)}


class OSMCanals:
    def __init__(self,cache:DiskCache):
        self.cache=DiskCache(cache.directory,fetcher=cache.fetcher,timeout=min(10,cache.timeout),retries=0,sleeper=cache.sleeper,clock=cache.clock)
        self._snapshot=None
        self._created_at=None
        self._lock=threading.RLock()

    def feature_collection(self)->dict:
        with self._lock:
            if self._snapshot is not None and (self._snapshot['available'] or self.cache.clock()-self._created_at<300):
                return copy.deepcopy(self._snapshot)
            clauses=['way(around:5000,'+str(v['lat'])+','+str(v['lon'])+')["waterway"~"^(canal|ditch|drain)$"];'
                     for v in list_villages()]
            query='[out:json][timeout:15];('+''.join(clauses)+');out body geom;'
            result=None
            for host in ('https://overpass.private.coffee/api/interpreter','https://overpass-api.de/api/interpreter'):
                try:
                    result=self.cache.get(host+'?'+urlencode({'data':query}),parse_waterways,ttl=86400)
                    break
                except DataUnavailable:
                    continue
            self._created_at=self.cache.clock()
            if result is None:
                self._snapshot=unavailable_osm('Current OSM providers unavailable; no canal geometry fabricated')
                return copy.deepcopy(self._snapshot)
            self._snapshot={'type':'FeatureCollection','features':result['data'],'available':True,
                'source':SOURCE,'attribution':'© OpenStreetMap contributors, ODbL1.0',
                'license':'https://www.openstreetmap.org/copyright','reason':None,
                'data_as_of':_stamp(result['fetched_at']),'stale':result['stale'],
                'feature_count':len(result['data']),'spacing':copy.deepcopy(SPACING),
                'coverage':{'radius_km':5,'villages':[v['id'] for v in list_villages()]},
                'assumptions':['Current OSM mapped geometry, not historical2019 canal geometry',
                               'Waterways intersecting the five5km query areas are shown as whole mapped ways',
                               'OSM completeness and positional accuracy are unverified; no surveyed hydraulic spacing inferred',
                               'The model continues to use400m team-assumed canal spacing; these lines do not recalibrate it']}
            return copy.deepcopy(self._snapshot)
