"""Light national satellite counts using source-attributed ADM1 polygons.

No national risk/peat model or fire-confirmation inference is provided.
Geometry uses actual point-in-polygon, including holes and MultiPolygons.
Bounding boxes only prune candidates. Shared border points are assigned once,
lexicographically by province name, rather than counted twice.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .cache import DataUnavailable
from .fire_qa import CLASSES, LIMITATIONS, PARAMS, VOLCANOES, classify_events, headline, stamp

DATA = Path(__file__).with_name('data') / 'indonesia-adm1-2017.geojson'
COVERAGE = {'units':34,'boundary_year':2017,'boundary_id':'IDN-ADM1-65028918',
            'current_administration':False,
            'note':'Old 34-province geography (2017), not current provincial boundaries; newer Papua provinces are not separately represented',
            'source':'geoBoundaries gbOpen / OpenStreetMap / Wambacher',
            'license':'Open Data Commons Open Database License 1.0',
            'source_url':'https://www.geoboundaries.org/api/current/gbOpen/IDN/ADM1/',
            'attribution':'Province boundaries: geoBoundaries gbOpen, OpenStreetMap contributors and Wambacher, ODbL1.0; satellite detections: NASA FIRMS NOAA-20/NOAA-21'}


def _bbox(ring):
    return min(p[0] for p in ring),min(p[1] for p in ring),max(p[0] for p in ring),max(p[1] for p in ring)


def _in_box(x,y,bounds):
    a,b,c,d=bounds
    return a-1e-10<=x<=c+1e-10 and b-1e-10<=y<=d+1e-10


def _prepare_ring(points):
    """Index exact segments by latitude; never approximate polygon membership."""
    min_y=min(p[1] for p in points)
    max_y=max(p[1] for p in points)
    scale=64/(max_y-min_y) if max_y>min_y else 0
    buckets=[[] for _ in range(65)]
    previous=points[-1]
    for current in points:
        low=max(0,min(64,int((min(previous[1],current[1])-min_y)*scale)))
        high=max(0,min(64,int((max(previous[1],current[1])-min_y)*scale)))
        for index in range(low,high+1):
            buckets[index].append((previous,current))
        previous=current
    return {'min_y':min_y,'scale':scale,'buckets':buckets}


def _ring_location(x: float,y: float,ring) -> int:
    """Return -1 outside, 0 boundary, 1 inside a lon/lat ring."""
    inside=False
    index=max(0,min(64,int((y-ring['min_y'])*ring['scale'])))
    for previous,current in ring['buckets'][index]:
        ax,ay=previous
        bx,by=current
        cross=(x-ax)*(by-ay)-(y-ay)*(bx-ax)
        if abs(cross)<=1e-10*(1+abs(bx-ax)+abs(by-ay)) and min(ax,bx)-1e-10<=x<=max(ax,bx)+1e-10 and min(ay,by)-1e-10<=y<=max(ay,by)+1e-10:
            return 0
        if (ay>y)!=(by>y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside=not inside
    return 1 if inside else -1


class ProvinceIndex:
    def __init__(self,path: str | Path=DATA):
        payload=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        self.provinces=[]
        for feature in payload['features']:
            name=feature['properties'].get('shapeName') or feature['properties'].get('name')
            geometry=feature['geometry']
            if not isinstance(name,str) or geometry['type'] not in ('Polygon','MultiPolygon'):
                raise ValueError('Named polygon provinces required')
            components=geometry['coordinates'] if geometry['type']=='MultiPolygon' else [geometry['coordinates']]
            prepared=[]
            for polygon in components:
                rings=[]
                for ring in polygon:
                    points=[(float(p[0]),float(p[1])) for p in ring]
                    if len(points)<3 or any(not math.isfinite(v) for point in points for v in point):
                        raise ValueError('Invalid province ring')
                    rings.append((_bbox(points),_prepare_ring(points)))
                if rings:
                    prepared.append(rings)
            if not prepared:
                raise ValueError('Empty province geometry')
            outer=[ring[0][0] for ring in prepared]
            overall=(min(b[0] for b in outer),min(b[1] for b in outer),max(b[2] for b in outer),max(b[3] for b in outer))
            self.provinces.append((name,overall,prepared))
        self.provinces.sort(key=lambda province:province[0])
        self.names=[p[0] for p in self.provinces]
        if len(set(self.names))!=len(self.names):
            raise ValueError('Duplicate province names')

    def province_for(self,lat: float,lon: float) -> str | None:
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:
            return None
        for name,overall,components in self.provinces:
            if not _in_box(lon,lat,overall):
                continue
            for rings in components:
                bounds,outer=rings[0]
                if not _in_box(lon,lat,bounds) or _ring_location(lon,lat,outer)<0:
                    continue
                if any(_in_box(lon,lat,b) and _ring_location(lon,lat,hole)==1 for b,hole in rings[1:]):
                    continue
                return name
        return None


class NationalService:
    def __init__(self,ingest,*,clock=time.time,cache_seconds: float=300,
                 boundaries_path: str | Path | None=None):
        if not math.isfinite(cache_seconds) or not 300<=cache_seconds<=3600:
            raise ValueError('National snapshot cache must be 300..3600 seconds')
        self.ingest=ingest
        self.clock=clock
        self.cache_seconds=cache_seconds
        self.boundaries_path=Path(boundaries_path) if boundaries_path else DATA
        self._index=None
        self._snapshot=None
        self._responses={}
        self._event_responses={}
        self._lock=threading.RLock()

    def _refresh(self):
        # Ingest is the original regional adapter, not the pilot-radius service.
        source=self.ingest.hotspots()  # Both providers failing raises, never fake zero.
        if not source.get('available',False):
            raise DataUnavailable('National hotspot providers unavailable')
        if self._index is None:
            self._index=ProvinceIndex(self.boundaries_path)
        detections={}
        invalid=0
        for item in source['items']:
            try:
                timestamp=datetime.fromisoformat(item['detected_at'].replace('Z','+00:00'))
                if timestamp.tzinfo is None:
                    raise ValueError('Timestamp must include timezone')
                stamp=timestamp.timestamp()
                lat,lon=float(item['lat']),float(item['lon'])
                if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:
                    raise ValueError('Invalid coordinates')
                identity=item['id']
                if not isinstance(identity,str):
                    raise ValueError('Invalid identity')
                detections[identity]=(dict(copy.deepcopy(item),lat=lat,lon=lon),stamp,lat,lon)
            except (ValueError,TypeError,KeyError,OverflowError):
                invalid+=1
        classified=[]
        for item,stamp,lat,lon in detections.values():
            province=self._index.province_for(lat,lon)
            classified.append((dict(item,province=province),stamp))
        signature=json.dumps({'data_as_of':source.get('data_as_of'),'items':[r[0] for r in classified]},sort_keys=True,allow_nan=False)
        self._snapshot={'created_at':self.clock(),'source':{k:copy.deepcopy(v) for k,v in source.items() if k!='items'},
                        'detections':classified,'invalid_records':invalid,
                        'snapshot_id':'national-'+hashlib.sha256(signature.encode()).hexdigest()[:16]}
        self._responses={}
        self._event_responses={}

    def hotspots(self,days: int=1) -> dict:
        if isinstance(days,bool) or not isinstance(days,int) or not 1<=days<=7:
            raise ValueError('National hotspot days must be 1..7')
        with self._lock:
            if self._snapshot is None or not 0<=self.clock()-self._snapshot['created_at']<self.cache_seconds:
                self._refresh()
            if days in self._responses:
                return copy.deepcopy(self._responses[days])
            snapshot=self._snapshot
            now=self.clock()
            regional=[row for row,stamp in snapshot['detections'] if 0<=now-stamp<=days*86400]
            matched=[row for row in regional if row['province'] is not None]
            counts={name:0 for name in self._index.names}
            for row in matched:
                counts[row['province']]+=1
            coverage=copy.deepcopy(COVERAGE)
            if len(self._index.names)!=34:
                coverage=dict(coverage,units=len(self._index.names),boundary_year=None,boundary_id='synthetic-test',source='synthetic test polygons')
            result=dict(snapshot['source'],items=matched,by_province=counts,total_matched=len(matched),
                        regional_total=len(regional),unmatched_regional_count=len(regional)-len(matched),
                        unmatched_reason='Outside mapped Indonesia or unknown/coastal geometry; regional neighbors are not assigned to Indonesian provinces',
                        invalid_records=snapshot['invalid_records'],coverage=coverage,days=days,
                        snapshot_id=snapshot['snapshot_id'],
                        snapshot_created_at=datetime.fromtimestamp(snapshot['created_at'],timezone.utc).isoformat().replace('+00:00','Z'),
                        assumptions=list(snapshot['source'].get('assumptions',[]))+[
                            'Satellite heat detections are not confirmed fires',
                            'Province counts use 2017 simplified polygons and 34 historical units, not current provincial geography',
                            'Raw counts retain all matched detections; event classes are separate heuristic candidates',
                            'Boundary points assigned once by alphabetical province name; small coastal/island errors remain possible'])
            events=classify_events(matched,context=[row for row,stamp in snapshot['detections']
                                   if row['province'] is not None and 0<=now-stamp<=PARAMS['context_days']*86400])
            signature=json.dumps({'rule_version':PARAMS['version'],'parameters':PARAMS,'volcanoes':VOLCANOES,'events':events,
                                  'data_as_of':result.get('data_as_of')},sort_keys=True,allow_nan=False)
            event_id=PARAMS['version']+'-'+hashlib.sha256(signature.encode()).hexdigest()[:16]
            summary=headline(events)
            class_counts={key:sum(e['classification']==key for e in events) for key in CLASSES}
            result.update(raw_count=len(matched),event_count=len(events),classification_counts=class_counts,
                          headline=summary,events_snapshot_id=event_id,rule_version=PARAMS['version'],
                          by_province_events={name:headline([e for e in events if e['province']==name]) for name in self._index.names},
                          evidence_coverage={'peat_mask_available':False,'vegetation_available':False,
                                             'osm_industrial_available':False,'volcano_list_complete':False,'volcano_entries':len(VOLCANOES)})
            metadata={k:copy.deepcopy(v) for k,v in result.items() if k not in ('items','by_province')}
            self._event_responses[days]=dict(metadata,items=events,snapshot_id=event_id,
                                            assumptions=result['assumptions']+LIMITATIONS)
            self._responses[days]=result
            return copy.deepcopy(result)

    def fire_events(self, days: int=7, *, classification: str | None=None,
                    province: str | None=None, since: str | None=None) -> dict:
        if classification is not None and classification not in CLASSES:
            raise ValueError('Unknown classification')
        after=stamp(since) if since is not None else None
        with self._lock:
            self.hotspots(days)
            result=copy.deepcopy(self._event_responses[days])
        items=[e for e in result['items'] if (classification is None or e['classification']==classification)
               and (province is None or e['province']==province)
               and (after is None or stamp(e['last_seen'])>=after)]
        result.pop('by_province_events',None)  # Raw endpoint owns its unfiltered per-province QA map.
        result.update(items=items,headline=headline(items),raw_count=sum(e['detection_count'] for e in items),
                      event_count=len(items),classification_counts={k:sum(e['classification']==k for e in items) for k in CLASSES},
                      filters={'class':classification,'province':province,'since':since},
                      since_semantics='last_seen >= since; full event members retained',
                      mode='live')
        return result

    def overview(self, days: int=1) -> dict:
        result=self.fire_events(days)
        events=result.pop('items')
        result['by_province']=[dict(province=name,**headline([e for e in events if e['province']==name]))
                               for name in self._index.names]
        result['land_event_definition']='Only positive gambut/vegetasi candidates; uncertain events remain separate, not safe.'
        h=result['headline']
        result['headline_sentence']=(f"{h['land_event_count']} kandidat kejadian lahan terklasifikasi; "
            f"{h['uncertain_event_count']} kejadian belum diketahui. Data gambut belum tersedia. "
            f"{h['excluded_industry_count']} kandidat industri dan {h['excluded_volcano_count']} kandidat gunung api dipisahkan "
            '(klasifikasi heuristik, belum terkonfirmasi; daftar gunung api tidak lengkap).')
        return result
