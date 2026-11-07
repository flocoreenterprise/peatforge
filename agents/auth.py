"""Fail-closed operator auth and bounded per-peer attempt limits; no env files."""
from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
from threading import RLock
import time
from fastapi import HTTPException, Request

COOKIE_NAME='peatforge_session'
SESSION_SECONDS=12*60*60


@dataclass(repr=False)
class AuthSettings:
    pin: str=field(default='',repr=False)
    session_secret: str=field(default='',repr=False)

    @classmethod
    def from_environment(cls):
        return cls(pin=os.environ.get('DEMO_PIN',''),session_secret=os.environ.get('SESSION_SECRET',''))

    def configured(self):
        return re.fullmatch(r'[0-9]{6}',self.pin) is not None and len(self.session_secret)>=32


class RateLimit:
    def __init__(self,limit:int,seconds:int=60, *,clock=time.time):
        self.limit,self.seconds,self.clock=limit,seconds,clock
        self._entries={}
        self._lock=RLock()

    def allow(self,key:str)->bool:
        with self._lock:
            now=self.clock()
            if key not in self._entries and len(self._entries)>=10000:
                self._entries={identity:values for identity,values in self._entries.items() if values and values[-1]>now-self.seconds}
                if len(self._entries)>=10000:
                    return False
            values=self._entries.setdefault(key,deque())
            while values and values[0]<=now-self.seconds:
                values.popleft()
            if len(values)>=self.limit:
                return False
            values.append(now)
            return True


def peer_ip(request:Request)->str:
    # CF/X-Forwarded-For are attacker-controlled unless an explicit proxy trust
    # boundary exists. This implementation intentionally trusts neither header.
    return request.client.host if request.client else 'unknown'


def secure_cookie(request:Request)->bool:
    host=(request.url.hostname or '').lower()
    if request.url.scheme=='https':
        return True
    if host=='localhost':
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True  # Public tunnel host remains Secure even on proxied HTTP.


class OperatorAuth:
    def __init__(self,settings:AuthSettings,public_demo:bool, *,clock=time.time):
        self.settings,self.public_demo,self.clock=settings,public_demo,clock
        self.attempts=RateLimit(5,clock=lambda:self.clock())

    def authenticated(self,request:Request)->bool:
        return self.public_demo or self.valid_cookie(request.cookies.get(COOKIE_NAME))

    def require(self,request:Request)->None:
        if not self.authenticated(request):
            raise HTTPException(401,'Operator authentication required')

    def check_pin(self,pin:str)->bool:
        return self.settings.configured() and isinstance(pin,str) and hmac.compare_digest(pin.encode(),self.settings.pin.encode())

    def issue(self)->str:
        if not self.settings.configured():
            raise HTTPException(503,'Authentication unavailable')
        issued=int(self.clock())
        payload=f'v1.{issued}.{issued+SESSION_SECONDS}.{secrets.token_hex(16)}'
        signature=hmac.new(self.settings.session_secret.encode(),payload.encode(),hashlib.sha256).hexdigest()
        return payload+'.'+signature

    def valid_cookie(self,cookie:str|None)->bool:
        if not self.settings.configured() or not cookie or len(cookie)>240:
            return False
        parts=cookie.split('.')
        if len(parts)!=5 or parts[0]!='v1' or not all(re.fullmatch(r'[0-9]{1,12}',value) for value in parts[1:3]):
            return False
        if not re.fullmatch(r'[0-9a-f]{32}',parts[3]) or not re.fullmatch(r'[0-9a-f]{64}',parts[4]):
            return False
        issued,expires=int(parts[1]),int(parts[2])
        now=self.clock()
        if expires-issued!=SESSION_SECONDS or issued>now+30 or expires<=now:
            return False
        expected=hmac.new(self.settings.session_secret.encode(),'.'.join(parts[:4]).encode(),hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected,parts[4])
