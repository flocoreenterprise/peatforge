"""Unverified synthetic pilot seeds. These are not administrative boundaries."""
from copy import deepcopy

_SEEDS = [
    ('62.11.01.2002', 'Talio', 'TAL', -3.00, 114.10),
    ('62.11.01.2003', 'Talio Hulu', 'THU', -2.98, 114.12),
    ('62.11.01.2004', 'Talio Muara', 'TMU', -3.02, 114.09),
    ('62.11.01.2005', 'Belanti Siam', 'BSI', -2.96, 114.14),
    ('62.11.01.2006', 'Gadabung', 'GAD', -3.04, 114.11),
]


def list_villages() -> list[dict]:
    return [{'id': i, 'name': n, 'code': c, 'lat': lat, 'lon': lon, 'source': 'fixture',
             'verified': False, 'assumptions': ['Unverified village name, adm4 mapping and approximate coordinate; no official boundary']}
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
