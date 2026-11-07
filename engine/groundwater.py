"""One-dimensional conservative Boussinesq solver in metres and days.

Sy dh/dt = d/dx(K h dh/dx) + P - k ET0. h is hydraulic height above
an effective impermeable datum; WT depth is positive down from surface.
The effective datum is a benchmark-tuned team assumption, not peat thickness.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class GroundwaterParams:
    conductivity_m_day: float = 5.0
    specific_yield: float = 0.20
    spacing_m: float = 400.0
    effective_thickness_m: float = 24.0
    et_multiplier: float = 1.0
    nodes: int = 51
    stability_safety: float = 0.90

    def validate(self):
        values = asdict(self)
        if any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in values.values()):
            raise ValueError('Parameters must be finite numbers')
        if not isinstance(self.nodes, int) or isinstance(self.nodes, bool) or self.nodes != 51:
            raise ValueError('Pilot solver requires N=51')
        if not (0 < self.specific_yield <= 1 and self.conductivity_m_day > 0 and
                self.spacing_m > 0 and self.effective_thickness_m > 0 and
                self.et_multiplier >= 0 and 0 < self.stability_safety < 1):
            raise ValueError('Invalid hydraulic parameters')


def load_params(path: str | Path | None = None) -> GroundwaterParams:
    """Load values, retaining provenance in the YAML for display/review."""
    import yaml
    values = yaml.safe_load(Path(path or Path(__file__).with_name('params.yaml')).read_text(encoding='utf-8'))
    return GroundwaterParams(**{key: record['value'] for key, record in values['groundwater'].items()})


class NumericalFailure(RuntimeError):
    pass


def _finite_array(values: Sequence[float], label: str) -> list[float]:
    try:
        result = [float(v) for v in values]
    except (TypeError, ValueError):
        raise ValueError(f'{label} requires available numeric values') from None
    if any(not math.isfinite(v) for v in result):
        raise ValueError(f'{label} must be finite')
    return result


def solve_groundwater(rain_mm: Sequence[float], et0_mm: Sequence[float], *,
                      params: GroundwaterParams | None = None, initial_wt_cm: float = 40,
                      canal_wt_cm: float | Sequence[float] = 80,
                      initial_profile_m: Sequence[float] | None = None,
                      method: str = 'boussinesq', max_substeps_per_day: int = 100_000,
                      allow_fallback: bool = True) -> dict:
    """Return post-day mid-strip WT, hydraulic profiles and mass-balance ledgers.

    rain/ET0 inputs are mm/day. Canal depth is cm below peat surface and may
    vary daily. Boundary heights are held fixed during each day's integration.
    Initial profile, if supplied, is hydraulic height in metres. The mass ledger
    covers N-2 interior control volumes; boundary nodes are external reservoirs.
    Missing/nonfinite forcing is rejected rather than treated as zero rain.
    """
    p = params or GroundwaterParams()
    p.validate()
    rain, et0 = _finite_array(rain_mm, 'Rain'), _finite_array(et0_mm, 'ET0')
    if not rain or len(rain) != len(et0) or len(rain) > 366:
        raise ValueError('Forcing lengths must match and contain 1..366 days')
    if any(v < 0 for v in rain + et0):
        raise ValueError('Rain and ET0 must be nonnegative')
    if method not in ('boussinesq', 'bucket') or not isinstance(max_substeps_per_day, int) or max_substeps_per_day < 1:
        raise ValueError('Invalid solver controls')
    initial_wt_cm = float(initial_wt_cm)
    if not math.isfinite(initial_wt_cm) or not 0 <= initial_wt_cm <= p.effective_thickness_m * 100:
        raise ValueError('Initial WT outside hydraulic domain')
    canal = ([float(canal_wt_cm)] * len(rain) if isinstance(canal_wt_cm, (int, float))
             else _finite_array(canal_wt_cm, 'Canal depth'))
    if len(canal) != len(rain) or any(not math.isfinite(v) or not 0 <= v <= p.effective_thickness_m * 100 for v in canal):
        raise ValueError('Canal depth outside hydraulic domain or mismatched length')
    profile = ([p.effective_thickness_m - initial_wt_cm / 100] * p.nodes if initial_profile_m is None
               else _finite_array(initial_profile_m, 'Initial profile'))
    if len(profile) != p.nodes or any(not 0 <= h <= p.effective_thickness_m for h in profile):
        raise ValueError('Initial profile outside hydraulic domain')
    try:
        return _integrate(rain, et0, canal, profile, p, method, max_substeps_per_day)
    except NumericalFailure:
        if not allow_fallback or method == 'bucket':
            raise
        result = _integrate(rain, et0, canal, profile, p, 'bucket', max_substeps_per_day)
        result['fallback_reason'] = 'Explicit solver exceeded stability/substep limits; bucket approximation used'
        result['assumptions'].append(result['fallback_reason'])
        return result


def _integrate(rain, et0, canal, initial, p, method, limit):
    h = initial.copy()
    dx = p.spacing_m / (p.nodes - 1)
    width = dx * (p.nodes - 2)
    items = []
    totals = dict(storage_change_m2=0.0, recharge_m2=0.0, boundary_inflow_m2=0.0,
                  runoff_m2=0.0, unmet_demand_m2=0.0, residual_m2=0.0)
    for day, (rainfall, evaporation, canal_depth) in enumerate(zip(rain, et0, canal)):
        boundary = p.effective_thickness_m - canal_depth / 100
        h[0] = h[-1] = boundary
        start_storage = p.specific_yield * dx * sum(h[1:-1])
        recharge = (rainfall - p.et_multiplier * evaporation) / 1000
        elapsed, substeps, boundary_volume, runoff, unmet, max_ratio = 0.0, 0, 0.0, 0.0, 0.0, 0.0
        while elapsed < 1 - 1e-12:
            if substeps >= limit:
                raise NumericalFailure('Daily substep budget exceeded')
            if method == 'boussinesq':
                faces = [p.conductivity_m_day * (left + right) / 2 for left, right in zip(h[:-1], h[1:])]
                # Arithmetic-face flux is K/2 * (h_left**2 - h_right**2).
                # Its central Jacobian magnitude is 2*K*h_i, which can exceed
                # the sum of averaged face conductivities at an isolated peak.
                # Bound the nonlinear Jacobian globally to preserve monotonicity.
                max_sum = 2 * p.conductivity_m_day * max(h)
                dt = min(1 - elapsed, p.stability_safety * p.specific_yield * dx * dx / max_sum) if max_sum else 1 - elapsed
                ratio = dt * max_sum / (p.specific_yield * dx * dx)
                if ratio > p.stability_safety + 1e-12:
                    raise NumericalFailure('Explicit stability assertion failed')
                assert ratio <= p.stability_safety + 1e-12
                max_ratio = max(max_ratio, ratio)
                # Positive face flow points toward increasing x.
                flux = [face * (left - right) / dx for face, left, right in zip(faces, h[:-1], h[1:])]
                new = [h[i] + dt / p.specific_yield * ((flux[i-1] - flux[i]) / dx + recharge)
                       for i in range(1, p.nodes - 1)]
                boundary_volume += dt * (flux[0] - flux[-1])
            else:
                # Lumped diffusion timescale; explicitly an approximation.
                tau = p.specific_yield * p.spacing_m ** 2 / (math.pi ** 2 * p.conductivity_m_day * p.effective_thickness_m)
                dt = min(1 - elapsed, 0.5 * tau)
                average = sum(h[1:-1]) / (p.nodes - 2)
                exchange = (boundary - average) / tau
                new = [average + dt * (recharge / p.specific_yield + exchange)] * (p.nodes - 2)
                boundary_volume += dt * exchange * p.specific_yield * width
            for index, value in enumerate(new, 1):
                if not math.isfinite(value):
                    raise NumericalFailure('Non-finite solver state')
                runoff += max(0.0, value - p.effective_thickness_m) * p.specific_yield * dx
                unmet += max(0.0, -value) * p.specific_yield * dx
                h[index] = min(p.effective_thickness_m, max(0.0, value))
            elapsed += dt
            substeps += 1
        storage_change = p.specific_yield * dx * sum(h[1:-1]) - start_storage
        recharge_volume = recharge * width
        residual = storage_change - (recharge_volume + boundary_volume - runoff + unmet)
        scale = max(abs(storage_change), abs(recharge_volume) + abs(boundary_volume) + runoff + unmet, 1e-12)
        diagnostic = dict(storage_change_m2=storage_change, recharge_m2=recharge_volume,
                          boundary_inflow_m2=boundary_volume, runoff_m2=runoff,
                          unmet_demand_m2=unmet, residual_m2=residual,
                          relative_error=abs(residual) / scale, substeps=substeps,
                          max_stability_ratio=max_ratio if method == 'boussinesq' else None)
        for key in totals:
            totals[key] += diagnostic[key]
        items.append({'day': day, 'wt_cm': (p.effective_thickness_m - h[p.nodes // 2]) * 100,
                      'profile_m': h.copy(), 'mass_balance': diagnostic})
    scale = max(abs(totals['storage_change_m2']), abs(totals['recharge_m2']) +
                abs(totals['boundary_inflow_m2']) + totals['runoff_m2'] + totals['unmet_demand_m2'], 1e-12)
    totals['relative_error'] = abs(totals['residual_m2']) / scale
    return {'items': items, 'initial_profile_m': initial.copy(), 'method': method,
            'fallback_reason': None, 'mass_balance': totals, 'interior_control_length_m': width,
            'params': asdict(p), 'source': 'modelled', 'wt_source': 'modelled',
            'assumptions': ['Modelled groundwater; not measured',
                            'Homogeneous 1D strip, daily fixed symmetric canal boundaries',
                            'Effective hydraulic thickness is a benchmark-tuned team assumption',
                            'Mass ledger covers interior control volumes; boundary nodes are reservoirs'] +
                           (['Bucket diffusion approximation; no resolved hydraulic gradient'] if method == 'bucket' else [])}
