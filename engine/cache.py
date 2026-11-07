"""Small disk HTTP cache: no URLs or credentials persisted."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class DataUnavailable(RuntimeError):
    """Safe, deliberately provider-detail-free public failure."""


class AuthenticationError(DataUnavailable):
    pass


def cache_key(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, 'KEY' if any(s in k.lower() for s in ('key', 'token', 'secret', 'password')) else v)
             for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    path = re.sub(r'(/api/area/csv/)[^/]+', r'\1KEY', parts.path)
    safe = urlunsplit((parts.scheme, parts.hostname or '', path, urlencode(sorted(query)), ''))
    return hashlib.sha256(safe.encode()).hexdigest()


def download(url: str, timeout: float) -> str:
    with urlopen(Request(url, headers={'User-Agent': 'Peatforge/0.1'}), timeout=timeout) as response:
        payload = response.read(20_000_001)
        if len(payload) > 20_000_000:
            raise DataUnavailable('Provider response too large')
        return payload.decode('utf-8-sig')


class DiskCache:
    def __init__(self, directory: str | Path, *, fetcher=download, timeout: float = 10,
                 retries: int = 1, sleeper=time.sleep, clock=time.time):
        if not 0 < timeout <= 60 or not 0 <= retries <= 3:
            raise ValueError('Invalid network limits')
        self.directory = Path(directory)
        self.fetcher, self.timeout, self.retries = fetcher, timeout, retries
        self.sleeper, self.clock = sleeper, clock

    def get(self, url: str, parser, *, ttl: float = 3600) -> dict:
        path = self.directory / (cache_key(url) + '.json')
        cached = None
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            cached = (parser(record['body']), float(record['fetched_at']))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if cached and 0 <= self.clock() - cached[1] < ttl:
            return {'data': cached[0], 'stale': False, 'cached': True, 'fetched_at': cached[1]}
        for attempt in range(self.retries + 1):
            try:
                body = self.fetcher(url, self.timeout)
                data = parser(body)  # Never replace good cache with an invalid payload.
                fetched_at = self.clock()
                self.directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.directory,
                                                 delete=False) as handle:
                    temp = handle.name
                    json.dump({'body': body, 'fetched_at': fetched_at}, handle, allow_nan=False)
                os.replace(temp, path)
                return {'data': data, 'stale': False, 'cached': False, 'fetched_at': fetched_at}
            except HTTPError as error:
                if error.code in (401, 403):
                    raise AuthenticationError('Provider authentication rejected') from None
            except (OSError, ValueError, TypeError, KeyError, DataUnavailable):
                pass
            if attempt < self.retries:
                self.sleeper(0.25 * (2 ** attempt))
        if cached:
            return {'data': cached[0], 'stale': True, 'cached': True, 'fetched_at': cached[1],
                    'reason': 'Provider unavailable; last successful response'}
        raise DataUnavailable('Provider unavailable and no usable cache') from None
