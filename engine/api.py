"""Engine routes, mounted by the parent app with shared operator dependency.

    app.include_router(create_router(), prefix='/api',
                       dependencies=[Depends(require_operator)])

The parent may intentionally omit the dependency for its public environmental
read/demo policy. No channel settings, contacts or secrets are exposed here.
Importing this module does not instantiate services or perform network I/O.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Path as PathParam
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from .cache import DataUnavailable
from .service import EngineService, REPLAY_DAYS, START, _service, replay_meta
from .villages import list_villages

Mode = Literal['live','replay']


class SimulationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    type: Literal['canal_block','pump_standby','dispatch_verify']
    targets: list[str] = Field(min_length=1,max_length=6)
    delta_hc_cm: float | None = Field(default=None,ge=0,le=150)
    days: int = Field(default=14,ge=1,le=14)
    mode: Mode = 'live'
    day: int = Field(default=0,ge=0,lt=REPLAY_DAYS)


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


def _error(code, message, status):
    return JSONResponse({'error':{'code':code,'message':message}},status_code=status)


class SafeEngineRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try:
                return await handler(request)
            except HTTPException:
                raise  # Preserve the parent operator auth dependency semantics.
            except KeyError:
                return _error('not_found','Desa tidak ditemukan',404)
            except (RequestValidationError, ValueError, TypeError):
                return _error('invalid','Permintaan tidak valid',422)
            except DataUnavailable:
                return _error('unavailable','Data belum tersedia',503)
            except Exception:
                return _error('unavailable','Data belum tersedia',503)
        return safe


def _response(value):
    # Reject NaN/Infinity before they can reach the HTTP encoder.
    encoded = jsonable_encoder(value)
    try:
        json.dumps(encoded,allow_nan=False)
    except (ValueError,TypeError):
        raise DataUnavailable('Non-finite or unserializable engine output') from None
    return JSONResponse(encoded)


def create_router(service: EngineService | None = None) -> APIRouter:
    router = APIRouter(route_class=SafeEngineRoute,tags=['engine'])
    warmup_lock = threading.Lock()
    def engine():
        return service if service is not None else _service()

    @router.get('/villages')
    def villages():
        return _response({'items':list_villages()})

    @router.get('/villages/{village_id}/status')
    def status(village_id: str, mode: Mode='live', day: int=Query(0,ge=0,lt=REPLAY_DAYS)):
        return _response(engine().village_status(village_id,mode,day))

    @router.get('/villages/{village_id}/forecast')
    def forecast(village_id: str, days: int=Query(14,ge=1,le=14), mode: Mode='live',
                 day: int=Query(0,ge=0,lt=REPLAY_DAYS)):
        return _response({'items':engine().forecast(village_id,days,mode,day)})

    @router.post('/villages/{village_id}/simulate')
    def simulate(village_id: str, request: SimulationInput):
        return _response(engine().simulate(village_id,request.model_dump()))

    @router.get('/villages/{village_id}/canals')
    def canals(village_id: str):
        return _response({'items':engine().get_canals(village_id)})

    @router.get('/villages/{village_id}/resources')
    def resources(village_id: str):
        return _response(engine().get_resources(village_id))

    @router.get('/villages/{village_id}/params/history')
    def history(village_id: str):
        return _response({'items':engine().params_history(village_id)})

    @router.get('/map/risk')
    def risk(mode: Mode='live', day: int=Query(0,ge=0,lt=REPLAY_DAYS),
             requested_date: date | None=Query(None,alias='date')):
        if requested_date is not None:
            day=(requested_date-START).days
            if not 0 <= day < REPLAY_DAYS:
                raise ValueError('Date outside replay window')
            mode='replay'
        return _response(engine().map_risk(mode,day))

    @router.get('/hotspots')
    def hotspots(hours: int=Query(72,ge=1,le=168), village_id: str | None=None,
                 radius_km: float=Query(5,gt=0,le=100,allow_inf_nan=False),
                 mode: Mode='live',day: int=Query(0,ge=0,lt=REPLAY_DAYS)):
        return _response(engine().get_hotspots(village_id,hours,radius_km,mode,day))

    @router.get('/replay/meta')
    def meta():
        return _response(replay_meta())

    @router.get('/replay/day/{day}')
    def replay_day(day: int=PathParam(...,ge=0,lt=REPLAY_DAYS)):
        return _response(engine().replay(day))

    @router.get('/replay/{scenario}/day/{day}')
    def replay_alias(scenario: str,day: int=PathParam(...,ge=0,lt=REPLAY_DAYS)):
        return _response(engine().replay(day,scenario))

    @router.get('/backtest')
    def backtest(village_id: str):
        return _response(engine().backtest(village_id))

    @router.post('/warmup')
    def warmup(request: EmptyInput,mode: Mode='live'):
        if not warmup_lock.acquire(blocking=False):
            return _error('busy','Persiapan data sedang berjalan',429)
        try:
            backend=engine()
            with ThreadPoolExecutor(max_workers=5) as workers:
                # One bounded batch: five villages, one mode, day zero only.
                results=list(workers.map(lambda v: backend.village_status(v['id'],mode,0),list_villages()))
            return _response({'ready':True,'villages':len(results)})
        finally:
            warmup_lock.release()

    return router


router = create_router()
