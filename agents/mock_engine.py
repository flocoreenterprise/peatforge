"""Synthetic fixtures for wiring only; never a scientific model or HTTP fallback."""
from datetime import date, timedelta
from typing import Any

VILLAGE_ID = '62.11.01.2002'
NOTICE = 'Contoh sintetis untuk uji alur, bukan hasil model terverifikasi'


class MockEngine:
    def _check(self, village_id: str) -> None:
        if village_id != VILLAGE_ID:
            raise KeyError('Unknown fixture village')

    def list_villages(self) -> list[dict[str, Any]]:
        return [{'id': VILLAGE_ID, 'name': 'Talio', 'code': 'TAL', 'lat': -3.0, 'lon': 114.1, 'source': 'fixture'}]

    def village_status(self, village_id: str, mode: str = 'live', day: int = 0) -> dict[str, Any]:
        self._check(village_id)
        today = date(2019, 6, 15) + timedelta(days=day) if mode == 'replay' else date.today()
        return dict(village_id=village_id, level='TINGGI', pfvi=190, wt_cm=45,
                    days_to_critical=None, critical_date=None, confidence=0.5,
                    data_as_of=f'{today.isoformat()}T00:00:00Z', wt_source='modelled',
                    source='fixture', stale=False, mode=mode, day=day,
                    assumptions=[NOTICE, 'Perkiraan model, belum diukur'], reason='fixture_no_crossing')

    def forecast(self, village_id: str, days: int = 14, mode: str = 'live', day: int = 0) -> list[dict[str, Any]]:
        status = self.village_status(village_id, mode, day)
        start = date.fromisoformat(status['data_as_of'][:10])
        return [dict(date=(start + timedelta(days=i)).isoformat(), pfvi=190, level='TINGGI', wt_cm=45, rain_mm=0) for i in range(days)]

    def simulate(self, village_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self._check(village_id)
        curve = [{k: v for k, v in point.items() if k in ('date', 'pfvi', 'wt_cm')}
                 for point in self.forecast(village_id, request['days'], request['mode'], request['day'])]
        return dict(baseline_critical_date=None, scenario_critical_date=None,
                    days_bought=None, at_least_days=None, reason='fixture_no_simulation',
                    wt_gain_cm=None, pfvi_reduction=None, curve_baseline=curve,
                    curve_scenario=curve, assumptions=[NOTICE])

    def get_canals(self, village_id: str) -> list[dict[str, Any]]:
        self._check(village_id)
        return [dict(id='K4', name='Kanal empat', block_id='K4', block_status='open', spacing_m=400, source='fixture')]

    def get_resources(self, village_id: str) -> dict[str, Any]:
        self._check(village_id)
        return dict(pumps=1, crews=1, canal_blocks=[dict(id='K4', status='open')], source='fixture')

    def get_hotspots(self, village_id: str, hours: int = 48, radius_km: float = 5, mode: str = 'live', day: int = 0) -> dict[str, Any]:
        self._check(village_id)
        return dict(items=[], available=False, stale=False, data_as_of=None, reason='mock_no_hotspot_data', source='fixture')
