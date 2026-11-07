"""Validated shared tool inputs and engine results; no numeric generation by brains."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    simulation_input_version: str | None = None
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
    days: int = Field(default=14, ge=1, le=14)
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
    model_setup: dict[str, Any] | None = None


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
    water_category: Literal['kering','sedikit','masih_ada','banjir'] | None = None
    smoke_category: Literal['tidak','bau','terlihat','api'] | None = None
    place_text: str | None = Field(default=None,max_length=150)
    reported_words: dict[str,str] = Field(default_factory=dict)
    evidence_type: Literal['numeric','qualitative'] = 'numeric'
    confidence_label: Literal['REPORTED','LOW'] = 'REPORTED'

    @model_validator(mode='after')
    def classify_evidence(self):
        if any(key not in ('water','smoke','place') or len(value)>200 for key,value in self.reported_words.items()):
            raise ValueError('Invalid reported words')
        self.evidence_type='qualitative' if self.wt_cm is None else 'numeric'
        self.confidence_label='LOW' if self.wt_cm is None else 'REPORTED'
        return self


class Landmark(Model):
    landmark_id: str | None
    name: str | None
    lat: float | None
    lon: float | None
    match_score: float = Field(ge=0, le=1)
    source: Literal['fixture', 'measured'] = 'fixture'
