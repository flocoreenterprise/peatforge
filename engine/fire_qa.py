"""Deterministic heat-event candidates; never confirmation of a fire/factory.

Union-find edges require BOTH distance and time limits (inclusive). Components
are transitive: their total duration/diameter can exceed the edge thresholds.
Persistence uses an explicitly approximate equal-angle 375m grid (longitude
scaled at each latitude band), UTC distinct acquisition dates, full feed context.
The small GVP list is intentionally incomplete. Missing land cover stays unknown.
No network I/O, no fabricated peat mask, and no industrial provider retries.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

PARAMS = yaml.safe_load(Path(__file__).with_name('fire_qa_params.yaml').read_text())
CLASSES = ('gambut', 'lahan_vegetasi', 'industri', 'gunung_api', 'tidak_pasti')
# Coordinates verified against Smithsonian GVP public profiles 2026-11-07.
# All are Holocene volcanoes; this is NOT a complete Indonesian inventory.
VOLCANOES = (
    {'name':'Merapi', 'lat':-7.54, 'lon':110.446,
     'url':'https://volcano.si.edu/volcano.cfm?vn=263250'},
    {'name':'Semeru', 'lat':-8.108, 'lon':112.922,
     'url':'https://volcano.si.edu/volcano.cfm?vn=263300'},
    {'name':'Krakatau', 'lat':-6.1009, 'lon':105.4233,
     'url':'https://volcano.si.edu/volcano.cfm?vn=262000'},
    {'name':'Tengger Caldera', 'lat':-7.942, 'lon':112.95,
     'url':'https://volcano.si.edu/volcano.cfm?vn=263310'},
    {'name':'Lewotobi', 'lat':-8.542, 'lon':122.775,
     'url':'https://volcano.si.edu/volcano.cfm?vn=264180'},
    {'name':'Sinabung', 'lat':3.17, 'lon':98.392,
     'url':'https://volcano.si.edu/volcano.cfm?vn=261080'},
    {'name':'Agung', 'lat':-8.343, 'lon':115.508,
     'url':'https://volcano.si.edu/volcano.cfm?vn=264020'},
)
FIRMS_SOURCE = {'name':'NASA FIRMS heat detections', 'url':'https://firms.modaps.eosdis.nasa.gov/'}
LIMITATIONS = [
    'Kandidat berbasis aturan, bukan kebakaran atau pabrik terkonfirmasi; confidence adalah heuristik, bukan probabilitas terkalibrasi.',
    'Daftar gunung api Holosen Smithsonian GVP hanya tujuh entri terverifikasi; tidak lengkap untuk Indonesia. Di luar daftar tidak berarti bebas gunung api.',
    'Masker gambut dan bukti vegetasi nasional tidak tersedia; lokasi desa pilot bukan batas atau bukti tutupan gambut.',
    'Persistensi hanya memakai hari UTC berbeda dalam konteks FIRMS tersedia (maksimal tujuh hari); sel 375 m adalah pendekatan grid, bukan piksel sensor.',
    'Klaster transitif memakai tautan <=1 km dan <=48 jam; diameter atau durasi seluruh kejadian dapat lebih besar.',
    'OSM industri hanya dari bukti cache yang diberikan; tidak ada permintaan jaringan atau retry.',
    'Provinsi dan titik kejadian mengikuti deteksi pertama; kejadian dapat melintasi batas provinsi. Ketiadaan bukti bukan keadaan aman.',
]


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def distance_km(a, b):
    lat1,lat2 = math.radians(a['lat']),math.radians(b['lat'])
    dlat,dlon = lat2-lat1,math.radians(b['lon']-a['lon'])
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return PARAMS['earth_radius_km']*2*math.asin(min(1,math.sqrt(h)))


def cell(row):
    angle = math.degrees(PARAMS['persistence_cell_m']/1000/PARAMS['earth_radius_km'])
    band = math.floor((row['lat']+PARAMS['latitude_limit'])/angle)
    center_lat = (band+.5)*angle-PARAMS['latitude_limit']
    width = angle/max(PARAMS['grid_cosine_floor'],math.cos(math.radians(center_lat)))
    return band,math.floor((row['lon']+PARAMS['longitude_limit'])/width)


def headline(events):
    counts = {key:sum(e['classification']==key for e in events) for key in CLASSES}
    industry = sum(e['detection_count'] for e in events if e['classification']=='industri')
    volcano = sum(e['detection_count'] for e in events if e['classification']=='gunung_api')
    return dict(raw_count=sum(e['detection_count'] for e in events),event_count=len(events),
                land_event_count=counts['gambut']+counts['lahan_vegetasi'],peat_event_count=None,
                classified_peat_event_count=counts['gambut'],
                vegetation_event_count=counts['lahan_vegetasi'],excluded_industry_count=counts['industri'],
                excluded_volcano_count=counts['gunung_api'],uncertain_event_count=counts['tidak_pasti'],
                excluded_industry_detection_count=industry,excluded_volcano_detection_count=volcano,
                excluded_detection_count=industry+volcano)


def classify_events(rows, *, context=None, osm_cache=None, land_evidence=None):
    """Rows must be validated FIRMS records. Optional evidence is trusted adapter input.

    land_evidence(row) -> {peat:bool, vegetation:bool, source:{name,url}} or None.
    Evidence must positively cover EVERY member to assign a land-cover class.
    osm_cache -> {available:True,items:[{lat,lon,tags:{landuse:'industrial'},url}],data_as_of}.
    Unknown or malformed optional evidence is unavailable, never evidence of absence.
    """
    rows = sorted({r['id']:r for r in rows}.values(),key=lambda r:(stamp(r['detected_at']),r['id']))
    dates = defaultdict(set)
    for row in context if context is not None else rows:
        dates[cell(row)].add(stamp(row['detected_at']).date())
    parent = list(range(len(rows)))
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]]
            i=parent[i]
        return i
    times = [stamp(r['detected_at']).timestamp() for r in rows]
    # Spatial hash on 3D unit-sphere coordinates. A chord <=threshold has
    # coordinate differences <=threshold, so the 27 neighboring bins suffice.
    chord = 2*math.sin(PARAMS['cluster_distance_km']/2/PARAMS['earth_radius_km'])
    bins = defaultdict(list)
    for i,row in enumerate(rows):
        lat,lon=math.radians(row['lat']),math.radians(row['lon'])
        key=tuple(math.floor(v/chord) for v in (math.cos(lat)*math.cos(lon),math.cos(lat)*math.sin(lon),math.sin(lat)))
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for dz in (-1,0,1):
                    bucket=bins.get((key[0]+dx,key[1]+dy,key[2]+dz),[])
                    # Rows are time-sorted, so expired entries can be discarded.
                    while bucket and times[i]-times[bucket[0]]>PARAMS['cluster_hours']*3600:
                        bucket.pop(0)
                    for j in bucket:
                        if distance_km(row,rows[j])<=PARAMS['cluster_distance_km']+PARAMS['distance_tolerance_km']:
                            parent[find(i)] = find(j)
        bins[key].append(i)
    groups=defaultdict(list)
    for i,row in enumerate(rows):
        groups[find(i)].append(row)
    industrial=[]
    if isinstance(osm_cache,dict) and osm_cache.get('available'):
        for entry in osm_cache.get('items',[]):
            tags=entry.get('tags',{})
            if (tags.get('landuse')=='industrial' or tags.get('man_made') in ('works','flare')) and entry.get('url'):
                try:
                    if all(math.isfinite(float(entry[k])) for k in ('lat','lon')):
                        industrial.append(entry)
                except (ValueError,TypeError,KeyError):
                    pass
    events=[]
    for members in groups.values():
        sources=[dict(FIRMS_SOURCE)]
        reasons=['Deteksi panas satelit belum membuktikan kebakaran.']
        classification,score='tidak_pasti',PARAMS['confidence']['uncertain']
        volcano=next((v for v in VOLCANOES if any(distance_km(r,v)<=PARAMS['volcano_radius_km'] for r in members)),None)
        osm=next((o for o in industrial if any(distance_km(r,o)<=PARAMS['osm_industrial_radius_km'] for r in members)),None)
        persistent=max(len(dates[cell(r)]) for r in members)
        evidence=[land_evidence(r) if land_evidence else None for r in members]
        if volcano:
            classification,score='gunung_api',PARAMS['confidence']['volcano']
            reasons.append(f"Dalam {PARAMS['volcano_radius_km']:g} km gunung api Holosen {volcano['name']}; kandidat sumber panas vulkanik, bukan konfirmasi erupsi.")
            sources.append({'name':'Smithsonian Global Volcanism Program: '+volcano['name'],'url':volcano['url']})
        elif osm or persistent>=PARAMS['persistence_days']:
            classification='industri'
            score=PARAMS['confidence']['osm_industrial' if osm else 'persistence_only']
            if osm:
                reasons.append('Dekat titik referensi area industri yang dipetakan OSM; bukan pabrik terverifikasi.')
                sources.append({'name':'OpenStreetMap cached industrial evidence','url':osm['url']})
            if persistent>=PARAMS['persistence_days']:
                reasons.append(f"Panas persisten pada sel sekitar {PARAMS['persistence_cell_m']:g} m selama {persistent} hari UTC berbeda; kandidat industri dengan heuristik, bukan pabrik terverifikasi.")
        elif evidence and all(e and e.get('peat') is True and e.get('source') for e in evidence):
            classification,score='gambut',PARAMS['confidence']['peat']
            reasons.append('Seluruh deteksi tercakup bukti gambut positif; kandidat kejadian di gambut, perlu verifikasi.')
        elif evidence and all(e and e.get('vegetation') is True and e.get('source') for e in evidence):
            classification,score='lahan_vegetasi',PARAMS['confidence']['vegetation']
            reasons.append('Seluruh deteksi tercakup bukti vegetasi positif; kandidat kejadian vegetasi, perlu verifikasi.')
        else:
            reasons.append('Bukti tutupan gambut atau vegetasi positif belum tersedia; klasifikasi tidak pasti.')
        if classification in ('gambut','lahan_vegetasi'):
            sources.extend(e['source'] for e in evidence)
        if not isinstance(osm_cache,dict) or not osm_cache.get('available'):
            reasons.append('OSM industri tidak tersedia')
        ids=sorted(r['id'] for r in members)
        frps=[r['frp'] for r in members if r.get('frp') is not None]
        anchor=members[0]
        events.append(dict(id='event-'+hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16],
            lat=anchor['lat'],lon=anchor['lon'],location_method='first_detection',province=anchor.get('province'),
            first_seen=members[0]['detected_at'],last_seen=members[-1]['detected_at'],detection_count=len(members),
            detection_ids=ids,max_frp=max(frps) if frps else None,satellites=sorted({r.get('satellite','unknown') for r in members}),
            classification=classification,confidence=score,confidence_label='MED' if score>=PARAMS['medium_confidence_min'] else 'LOW',
            confirmed=False,reasons=reasons,sources=list({json.dumps(s,sort_keys=True):s for s in sources}.values()),
            persistence_days=persistent))
    return sorted(events,key=lambda e:(e['last_seen'],e['id']),reverse=True)
