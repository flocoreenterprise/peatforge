"""BMKG village metadata verified 2026-11-07; no administrative boundaries."""
from copy import deepcopy

_SEEDS = [
    ('62.11.01.2002', 'Talio', 'TAL', -3.1222040246, 114.0981588862),
    ('62.11.01.2003', 'Pangkoh Hilir', 'PHI', -3.0948407882, 114.1612351662),
    ('62.11.01.2004', 'Pangkoh Hulu', 'PHU', -3.0401913737, 114.1576813408),
    ('62.11.01.2005', 'Kantan Muara', 'KMU', -3.0086596884, 114.1169648671),
    ('62.11.01.2006', 'Talio Muara', 'TMU', -3.093826788, 114.1209430709),
]


def list_villages() -> list[dict]:
    return [{'id': i, 'name': n, 'code': c, 'lat': lat, 'lon': lon, 'source': 'provider',
             'verified': True, 'provider': 'BMKG',
             'source_url': 'https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4=' + i,
             'assumptions': ['BMKG village reference coordinate, not an official boundary; canal/well locations remain fixtures']}
            for i, n, c, lat, lon in _SEEDS]


def get_village(village_id: str) -> dict:
    for village in list_villages():
        if village['id'] == village_id:
            return deepcopy(village)
    raise KeyError('Unknown village')


def canals(village_id: str) -> list[dict]:
    get_village(village_id)
    words = ['satu', 'dua', 'tiga', 'empat', 'lima', 'enam']
    return [{'id': f'K{i}', 'name': f'Kanal {w}', 'block_id': f'K{i}', 'block_status': 'open',
             'spacing_m': 400, 'source': 'fixture', 'aliases': [f'kanal {i}', f'kanal {w}', f'parit {w}']}
            for i, w in enumerate(words, 1)]


def wells(village_id: str) -> list[dict]:
    get_village(village_id)
    return [{'id': f'W{i}', 'name': f'Sumur contoh {i}', 'source': 'fixture'} for i in range(1, 5)]


def hamlets(village_id: str) -> list[dict]:
    get_village(village_id)
    return [{'id': 'H1', 'name': 'Dusun contoh kanal empat', 'source': 'fixture',
             'aliases': ['kanal 4', 'kanal empat', 'parit empat']}]
