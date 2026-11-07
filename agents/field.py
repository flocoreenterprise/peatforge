"""Opaque field capability maps to exactly one responder, never operator auth."""
import hashlib
import re
import secrets
from .auth import RateLimit
from .channels import ChannelAdapter, Inbound
from .db import Database
from .warden import message_items


class FieldService:
    def __init__(self,database:Database,channels:ChannelAdapter):
        self.db,self.channels=database,channels
        self.limits=RateLimit(30)

    def link(self,responder_id:str)->str:
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row=connection.execute('SELECT field_token FROM responders WHERE id=?',(responder_id,)).fetchone()
            if not row:
                raise KeyError('Unknown responder')
            token=row['field_token']
            if not token:
                token=secrets.token_urlsafe(32)
                connection.execute('UPDATE responders SET field_token=? WHERE id=?',(token,responder_id))
            if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
                raise ValueError('Invalid field capability')
        return '/f/'+token

    def responder(self,token:str):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
            raise KeyError('Unknown field capability')
        with self.db.connect() as connection:
            row=connection.execute('SELECT id,name,village_id FROM responders WHERE field_token=?',(token,)).fetchone()
        if not row:
            raise KeyError('Unknown field capability')
        return dict(row)

    def messages(self,token:str,after:str|None=None)->dict:
        responder=self.responder(token)
        state=self.db.get('warden:'+responder['id'],{})
        return {'items':message_items(self.db,responder['id'],after),
                'responder':{'name':responder['name'],'village_id':responder['village_id']},
                'state':state.get('state','idle'),'mode':state.get('mode','live'),'day':state.get('day',0),'browser_voice_supported':True}

    def send(self,token:str,text:str)->dict:
        responder=self.responder(token)
        identity=hashlib.sha256(token.encode()).hexdigest()
        if not self.limits.allow(identity):
            from fastapi import HTTPException
            raise HTTPException(429,'Field message rate limit')
        result=self.channels.inbound(Inbound(responder_id=responder['id'],channel='web',text=text))
        return {**result,'responder':{'name':responder['name'],'village_id':responder['village_id']}}
