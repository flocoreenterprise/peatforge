"""Historical S-NPP FIRMS detections, filtered to the five pilot radius circles."""
from __future__ import annotations
import copy
import math
import os
import threading
from datetime import date,timedelta
from pathlib import Path
from urllib.parse import quote

from .cache import DataUnavailable,DiskCache
from .ingest import FIRMS,parse_hotspots,_stamp
from .villages import list_villages

START=date(2019,6,15)
END=date(2019,8,15)


def _configured_key():
    # Latest configured file wins over inherited process state; never log values.
    from dotenv import dotenv_values
    configured=dotenv_values(Path(__file__).resolve().parent.parent/'.env')
    if 'FIRMS_MAP_KEY' in configured:
        return (configured['FIRMS_MAP_KEY'] or '').strip()
    return os.environ.get('FIRMS_MAP_KEY','').strip()


def _distance(lat1,lon1,lat2,lon2):
    a,b=math.radians(lat1),math.radians(lat2)
    x=math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371*2*math.asin(min(1,math.sqrt(x)))


class FirmsArchive:
    def __init__(self,cache:DiskCache,map_key:str|None=None,*,key_loader=_configured_key):
        self.cache=cache
        self._key=map_key
        self._key_loader=key_loader
        self._snapshots={}
        self._lock=threading.RLock()

    def hotspots(self,start:date=START,end:date=END)->dict:
        if not START<=start<=end<=END:
            raise ValueError('Archive range outside replay2019')
        with self._lock:
            key=self._key if self._key is not None else self._key_loader()
            if not key:
                return {'items':[],'available':False,'source':'NASA FIRMS archive','stale':False,
                        'data_as_of':None,'reason':'FIRMS_MAP_KEY unset','total_unique':None,'by_village':{},
                        'partial':False,'date_coverage':{'requested_start':start.isoformat(),'requested_end':end.isoformat(),
                                                       'complete':False,'successful_ranges':[],'unavailable_ranges':[]}}
            if (start,end) in self._snapshots:
                return copy.deepcopy(self._snapshots[(start,end)])
            villages=list_villages()
            # Enclosing box is only a download prefilter; exact 5km circles below.
            west=min(v['lon'] for v in villages)-.06
            east=max(v['lon'] for v in villages)+.06
            south=min(v['lat'] for v in villages)-.05
            north=max(v['lat'] for v in villages)+.05
            area=','.join(f'{v:.6f}' for v in (west,south,east,north))
            items,results,successful,failed={},[],[],[]
            current=start
            while current<=end:
                count=min(5,(end-current).days+1)
                period={'start':current.isoformat(),'end':(current+timedelta(days=count-1)).isoformat()}
                url=f'{FIRMS}/api/area/csv/{quote(key,safe="")}/VIIRS_SNPP_SP/{area}/{count}/{current.isoformat()}'
                try:
                    result=self.cache.get(url,parse_hotspots,ttl=31536000)
                except DataUnavailable:
                    failed.append(period)
                    current+=timedelta(days=count)
                    continue
                results.append(result)
                successful.append(period)
                for point in result['data']:
                    memberships=[v['id'] for v in villages if _distance(v['lat'],v['lon'],point['lat'],point['lon'])<=5]
                    detected=date.fromisoformat(point['detected_at'][:10])
                    if memberships and start<=detected<=end:
                        items[point['id']]=dict(point,source='NASA FIRMS archive',village_ids=memberships)
                current+=timedelta(days=count)
            if not results:
                raise DataUnavailable('NASA FIRMS archive unavailable')
            counts={v['id']:sum(v['id'] in p['village_ids'] for p in items.values()) for v in villages}
            snapshot={'items':sorted(items.values(),key=lambda h:h['detected_at']),'available':True,
                      'source':'NASA FIRMS archive','sensor':'VIIRS S-NPP standard processing',
                      'stale':any(r['stale'] for r in results),'data_as_of':_stamp(min(r['fetched_at'] for r in results)),
                      'partial':bool(failed),'total_unique':len(items) if not failed else None,
                      'retrieved_unique':len(items),'by_village':counts,'by_village_is_lower_bound':bool(failed),
                      'date_coverage':{'requested_start':start.isoformat(),'requested_end':end.isoformat(),
                                       'timezone':'UTC FIRMS acquisition dates','complete':not failed,'successful_ranges':successful,'unavailable_ranges':failed},
                      'assumptions':['Satellite heat detections, not confirmed fires',
                                     'Unique union within5km of five BMKG reference points; overlapping village counts must not be summed',
                                     'Incomplete coverage means retrieved counts are lower bounds, not full-period totals'],
                      'attribution':'NASA FIRMS VIIRS S-NPP archive',
                      'documentation':'https://firms.modaps.eosdis.nasa.gov/api/area/'}
            if not failed:
                self._snapshots[(start,end)]=snapshot
            return copy.deepcopy(snapshot)
