"""Explicit UAT commands. Environment only; check never contacts a provider."""
import argparse
import asyncio
import json
import os
import sqlite3
from fastapi import HTTPException
from .app import create_app
from .channels import ChannelUnavailable
from .contacts import normalize_phone
from .mock_engine import VILLAGE_ID


async def run(args):
    app=create_app(background_scout=False)
    async with app.router.lifespan_context(app):
        voice=app.state.voice
        if args.command=='check':
            print('configured' if voice.ready() else 'not configured')
            return 0 if voice.ready() else 1
        if args.command=='provision-uat':
            if not args.consent or app.state.public_demo:
                raise ChannelUnavailable('Protected explicit consent required')
            phone=normalize_phone(os.environ.get('UAT_PHONE_NUMBER',''))
            with app.state.database.connect() as connection:
                connection.execute('INSERT OR IGNORE INTO responders(id,village_id,name,role,tier) VALUES(?,?,?,?,?)',
                                   ('uat1',VILLAGE_ID,'Penerima uji coba Peatforge','UAT',1))
                for canal in app.state.engine.get_canals(VILLAGE_ID):
                    connection.execute('INSERT OR IGNORE INTO landmarks(id,village_id,name,aliases,source) VALUES(?,?,?,?,?)',
                                       (canal['id'],VILLAGE_ID,canal['name'],json.dumps(canal.get('aliases',[])),'fixture'))
                connection.execute('INSERT OR IGNORE INTO landmarks(id,village_id,name,aliases,source) VALUES(?,?,?,?,?)',
                                   ('W2',VILLAGE_ID,'Sumur dua',json.dumps(['sumur dua','sumur 2']),'fixture'))
            app.state.channels.contacts().bind('uat1',phone,consent=True)
            print('contact stored with consent')
            return 0
        result=voice.call(args.responder,args.diag,uat=args.uat)
        print(result['status']+(' ('+result['reason']+')' if result.get('reason') else ''))
        return 0 if result['status'] in ('queued','mock') else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description='Peatforge TwiML UAT; no secrets or contact values printed')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('check')
    provision=commands.add_parser('provision-uat')
    provision.add_argument('--consent',action='store_true')
    call=commands.add_parser('call')
    call.add_argument('responder')
    call.add_argument('--diag',action='store_true')
    call.add_argument('--uat',action='store_true',help='Consented normal Warden test; bypass risk threshold only')
    args=parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (ValueError,KeyError,ChannelUnavailable,RuntimeError,sqlite3.Error,HTTPException,OSError):
        print('unavailable; check configuration, chosen contact, consent and policy')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
