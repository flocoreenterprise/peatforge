"""Validated shared tool inputs and engine results; no numeric generation by brains."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

Mode = Literal['live', 'replay']
Level = Literal['RENDAH', 'SEDANG', 'TINGGI', 'EKSTREM']
Channel = Literal['sms', 'voice', 'telegram', 'web']


class Model(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')


class Status(Model):
    village_id: str
    level: Level
    pfvi_level: Level | None = None
    critical_now: bool = False
    critical_reason: Literal['wt', 'pfvi', 'both'] | None = None
    pfvi: float | None = Field(ge=0, le=300)
    wt_cm: float | None
    days_to_critical: int | None = Field(ge=0)
    critical_date: str | None
    confidence: float = Field(ge=0, le=1)
    data_as_of: str
    wt_source: Literal['modelled', 'measured'] = 'modelled'
    source: Literal['fixture', 'modelled', 'measured']
    stale: bool
    mode: Mode
    day: int = Field(ge=0)
    assumptions: list[str]
    reason: str | None = None


class ForecastPoint(Model):
    date: str
    pfvi: float | None = Field(ge=0, le=300)
    level: Level
    wt_cm: float | None
    rain_mm: float | None = Field(ge=0)


class SimulationRequest(Model):
    type: Literal['canal_block', 'pump_standby', 'dispatch_verify']
    targets: list[str] = Field(min_length=1)
    delta_hc_cm: float | None = Field(default=None, ge=0, le=150)
    days: int = Field(default=14, ge=1, le=90)
    mode: Mode = 'live'
    day: int = Field(default=0, ge=0, le=61)


class Simulation(Model):
    baseline_critical_date: str | None
    scenario_critical_date: str | None
    days_bought: int | None = Field(ge=0)
    at_least_days: int | None = Field(ge=0)
    reason: str | None
    wt_gain_cm: float | None
    pfvi_reduction: float | None
    curve_baseline: list[dict[str, Any]]
    curve_scenario: list[dict[str, Any]]
    assumptions: list[str]


class ObservationInput(Model):
    village_id: str
    responder_id: str
    wt_cm: float | None = Field(default=None, ge=0, le=150)
    smoke: bool | None = None
    fire: bool | None = None
    landmark_id: str | None = None
    note: str = Field(default='', max_length=500)
    anonymous: bool = False
    confirmed: bool = False
    mode: Mode = 'live'
    day: int = Field(default=0, ge=0, le=61)


class Landmark(Model):
    landmark_id: str | None
    name: str | None
    lat: float | None
    lon: float | None
    match_score: float = Field(ge=0, le=1)
    source: Literal['fixture', 'measured'] = 'fixture'
