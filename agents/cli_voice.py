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
        if args.command=='status':
            result=voice.status_report(args.responder)
            print('mode='+result['mode'])
            for call in result['calls']:
                print('provider_call_sid='+call['call_sid']+' status='+call['status']+' error_code='+str(call['error_code']))
            for attempt in result['attempts']:
                print('local_status='+attempt['status']+' error_code='+str(attempt['error_code']))
            for item in result['transcript']:
                print(item['direction']+': '+item['text'])
            if result['error_code'] is not None:
                print('provider_error_code='+str(result['error_code']))
            return 0 if result['error_code'] is None else 1
        result=voice.call(args.responder,args.diag,uat=args.uat,cli_uat_pair=args.uat_pair,cli_realtime_uat=args.realtime_uat)
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
    call.add_argument('--uat-pair',action='store_true',help='Explicit one diagnostic then normal UAT pair; CLI only')
    call.add_argument('--realtime-uat',action='store_true',help='One authorized Realtime attempt after the completed pair; CLI only')
    status=commands.add_parser('status')
    status.add_argument('responder')
    args=parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (ValueError,KeyError,ChannelUnavailable,RuntimeError,sqlite3.Error,HTTPException,OSError):
        print('unavailable; check configuration, chosen contact, consent and policy')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
