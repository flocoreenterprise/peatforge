"""Deterministic PFVI equations adapted from peatfr (MIT).

Credit: Mahdiyasa, Melly, Pasaribu, Taufik and Muljadi (2025), as credited in
planning blueprint; package author Melly. Primary equation reference:
https://github.com/mellygsln/peatfr/blob/main/R/Function%20Fire%20Predict.R
Written independently from the published equations. Differences are deliberate:
positive-down WT in cm; previous-day rain (upstream R vector aligns same-day);
mineral-soil bounds 15/45; constrained two-start fit plus explicit team priors.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

BOUNDS = ((0.0, 50.0), (0.01, 0.5), (1.05, 8.0), (5.0, 300.0))
ASSUMPTIONS = [
    'PFVI is modelled, not measured fire probability',
    'Mineral-soil 15/45 percent bounds are a proxy assumption, not peat moisture',
    '175 at 40cm and 250 at 60cm are team equilibrium calibration priors at 28C and R0=3000mm/year; not independent validation',
    'Previous-day rainfall rule corrects upstream same-day vector alignment; first day uses missing previous rain unless supplied',
]


@dataclass(frozen=True)
class PFVIParams:
    aH: float = 6.192465462820873
    bH: float = 0.045505543655178664
    n: float = 2.0
    alpha_cm: float = 50.0

    def validate(self):
        for value, (low, high) in zip(asdict(self).values(), BOUNDS):
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError('PFVI parameter outside constrained bounds')


def load_pfvi_config(path: str | Path | None = None) -> dict:
    """Read versioned defaults and mineral proxy bounds from source-labelled YAML."""
    import yaml
    records = yaml.safe_load(Path(path or Path(__file__).with_name('params.yaml')).read_text(encoding='utf-8'))['pfvi']
    values = {key: record['value'] for key, record in records.items()}
    params = PFVIParams(**{key: values[key] for key in asdict(PFVIParams())})
    params.validate()
    return {'params': params, 'fc': values['mineral_fc_pct'], 'sat': values['mineral_sat_pct'],
            'annual_rain_mm': values['annual_rain_mm'], 'ridge_weight': values['prior_weight']}


def _number(value, label: str) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{label} must be an available finite number') from None
    if not math.isfinite(result):
        raise ValueError(f'{label} must be finite')
    return result


def clamp(value: float) -> float:
    return max(0.0, min(300.0, _number(value, 'Index')))


def diobs(soil_moisture_pct: float, fc: float = 15, sat: float = 45) -> float:
    sm, fc, sat = [_number(v, 'Soil moisture bound') for v in (soil_moisture_pct, fc, sat)]
    if sat <= fc:
        raise ValueError('Saturation must exceed field-capacity proxy')
    return clamp(300 * (1 - (sm - fc) / (sat - fc)))


def rainfall_factor(rain_mm: float, previous_rain_mm: float | None) -> float:
    rain = _number(rain_mm, 'Rain')
    previous = None if previous_rain_mm is None else _number(previous_rain_mm, 'Previous rain')
    if rain < 0 or (previous is not None and previous < 0):
        raise ValueError('Rain must be nonnegative')
    if previous is None or previous <= 5.1:
        return max(0.0, rain - 5.1)
    return rain


def drying_coefficient(temperature_c: float, annual_rain_mm: float = 3000, dt_days: float = 1) -> float:
    temperature, annual, dt = [_number(v, 'Drying input') for v in (temperature_c, annual_rain_mm, dt_days)]
    if not -80 <= temperature <= 65 or annual < 0 or not 0 < dt <= 1:
        raise ValueError('Drying inputs outside supported domain')
    return (0.4982 * math.exp(0.0905 * temperature + 1.6096) - 4.268) * dt * 1e-3 / (1 + 10.88 * math.exp(-0.00173582677165354 * annual))


def water_table_factor(wt_cm: float, params: PFVIParams | None = None) -> float:
    p = params or PFVIParams()
    p.validate()
    depth = max(0.0, _number(wt_cm, 'Water depth'))
    # Log-domain retention calculation remains finite even for very deep input.
    log_ratio = math.log(depth / p.alpha_cm) if depth else -math.inf
    power_log = p.n * log_ratio
    log_one_plus = max(0.0, power_log) + math.log1p(math.exp(-abs(power_log)))
    theta = math.exp(-(1 - 1 / p.n) * log_one_plus)
    return p.aH - p.bH * (1 - theta) * 300


def equilibrium_pfvi(wt_cm: float, params: PFVIParams | None = None,
                     temperature_c: float = 28, annual_rain_mm: float = 3000) -> float:
    """Dry constant-forcing fixed point, intentionally unclamped for prior fitting."""
    coefficient = drying_coefficient(temperature_c, annual_rain_mm)
    if coefficient <= 0:
        raise ValueError('Positive drying coefficient required for equilibrium prior')
    return 300 - water_table_factor(wt_cm, params) / coefficient


def run_pfvi(wt_cm: Sequence[float], rain_mm: Sequence[float], temperature_c: Sequence[float], *,
             params: PFVIParams | None = None, initial_pfvi: float = 150,
             previous_rain_mm: float | None = None, annual_rain_mm: float = 3000) -> dict:
    p = params or PFVIParams()
    p.validate()
    if not 1 <= len(wt_cm) <= 366 or not len(wt_cm) == len(rain_mm) == len(temperature_c):
        raise ValueError('PFVI input arrays must match and contain 1..366 days')
    x = clamp(initial_pfvi)
    items = []
    for day, (depth, rain, temperature) in enumerate(zip(wt_cm, rain_mm, temperature_c)):
        df = (300 - clamp(x)) * drying_coefficient(temperature, annual_rain_mm)
        rf = rainfall_factor(rain, previous_rain_mm)
        wtf = water_table_factor(depth, p)
        raw = clamp(x) + df - rf - wtf
        x = clamp(raw)
        items.append({'day': day, 'pfvi': x, 'level': level(x), 'df': df, 'rf': rf,
                      'wtf': wtf, 'unclamped_pfvi': raw, 'previous_rain_mm': previous_rain_mm})
        previous_rain_mm = _number(rain, 'Rain')
    return {'items': items, 'params': asdict(p), 'source': 'modelled',
            'assumptions': ASSUMPTIONS.copy(), 'last_rain_mm': previous_rain_mm}


def level(pfvi: float) -> str:
    value = _number(pfvi, 'PFVI')
    if not 0 <= value <= 300:
        raise ValueError('PFVI outside 0..300')
    return 'RENDAH' if value < 100 else 'SEDANG' if value < 175 else 'TINGGI' if value < 250 else 'EKSTREM'


def calibrate(wt_cm: Sequence[float], rain_mm: Sequence[float], temperature_c: Sequence[float],
              soil_moisture_pct: Sequence[float], *, ridge_weight: float = 1.0,
              maxiter: int = 500, fc: float = 15, sat: float = 45) -> dict:
    """Constrained deterministic two-start Nelder-Mead against mineral-soil DIobs.

    Priors are added to trajectory MSE, never reported as validation samples.
    Parameter optimization occurs in a unit box so parameter scales are comparable.
    """
    import numpy as np
    from scipy.optimize import minimize
    if not len(wt_cm) == len(rain_mm) == len(temperature_c) == len(soil_moisture_pct) or not 2 <= len(wt_cm) <= 366:
        raise ValueError('Calibration needs 2..366 matching daily observations')
    if not math.isfinite(ridge_weight) or ridge_weight <= 0 or not isinstance(maxiter, int) or not 1 <= maxiter <= 5000:
        raise ValueError('Invalid calibration controls')
    observed = [diobs(v, fc=fc, sat=sat) for v in soil_moisture_pct]
    # Validate all forcing before optimizer so missing input cannot become a fit.
    run_pfvi(wt_cm, rain_mm, temperature_c, initial_pfvi=observed[0])
    def decode(unit):
        return PFVIParams(*(low + min(1.0, max(0.0, float(v))) * (high - low)
                            for v, (low, high) in zip(unit, BOUNDS)))
    def encode(p):
        return [(v-low)/(high-low) for v, (low, high) in zip(asdict(p).values(), BOUNDS)]
    def objective(unit):
        p = decode(unit)
        predicted = run_pfvi(wt_cm, rain_mm, temperature_c, params=p, initial_pfvi=observed[0])['items']
        mse = sum((point['pfvi'] - target)**2 for point, target in zip(predicted, observed)) / len(observed)
        prior = ((equilibrium_pfvi(40, p)-175)**2 + (equilibrium_pfvi(60, p)-250)**2) / 2
        return mse + ridge_weight * prior
    starts = (PFVIParams(), PFVIParams(12, 0.12, 3, 100))
    results = [minimize(objective, np.array(encode(start)), method='Nelder-Mead', bounds=[(0,1)]*4,
                        options={'maxiter': maxiter, 'xatol': 1e-7, 'fatol': 1e-5}) for start in starts]
    best = min(results, key=lambda result: float(result.fun))
    fitted = decode(best.x)
    fitted.validate()
    return {'params': asdict(fitted), 'objective': float(best.fun), 'converged': bool(best.success),
            'starts': 2, 'source': 'modelled', 'assumptions': ASSUMPTIONS.copy(),
            'prior_weight': ridge_weight, 'samples': len(observed), 'optimizer': 'bounded Nelder-Mead',
            'prior_equilibria': {'wt40_cm': equilibrium_pfvi(40, fitted), 'wt60_cm': equilibrium_pfvi(60, fitted)}}
