"""Local operator CLI. Run with configured environment; never loads .env."""
import argparse
from getpass import getpass
import sqlite3
from .app import _database_scope, create_app
from .channels import ChannelAdapter, ChannelSettings, ChannelUnavailable
from .db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Peatforge channels; no secrets are printed')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    send = commands.add_parser('send')
    send.add_argument('responder')
    send.add_argument('text')
    bind = commands.add_parser('bind')
    bind.add_argument('responder')
    bind.add_argument('--consent', action='store_true', help='Responder explicitly consented to contact')
    webhook = commands.add_parser('register-webhook')
    webhook.add_argument('url', help='HTTPS callback without query or credentials')
    args = parser.parse_args(argv)
    try:
        app = create_app()
        settings = ChannelSettings.from_environment(public_demo=app.state.public_demo)
        if app.state.public_demo and args.command != 'check':
            raise ChannelUnavailable('CLI contact/provider actions disabled in public demo')
        database = Database(app.state.database_path)
        database.initialize()
        _database_scope(database, app.state.public_demo)
        adapter = ChannelAdapter(database, settings)
        if args.command == 'check':
            if app.state.public_demo:
                print('mock: public demo; no provider check performed')
            else:
                adapter.gateway.check()
                print('provider reachable; delivery not tested')
        elif args.command == 'bind':
            if not args.consent:
                raise ValueError('Explicit responder consent required')
            adapter.contacts().bind(args.responder, getpass('Phone (hidden): '), consent=True)
            print('contact stored')
        elif args.command == 'send':
            adapter.send(args.responder, args.text)
            print('queued; delivery not confirmed')
        elif args.command == 'register-webhook':
            adapter.gateway.register_webhook(args.url)
            print('webhook registered')
        return 0
    except (ValueError, ChannelUnavailable, sqlite3.Error):
        print('Operation unavailable; check configuration and consent')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
