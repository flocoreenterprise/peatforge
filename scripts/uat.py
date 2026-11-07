"""Load user-entered local configuration for an explicitly requested UAT command.

Never prints configuration. Secret entry remains exclusively set-secrets.ps1.
"""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ('voice', 'sms', 'sms-register'):
        print('Choose an explicit UAT command.')
        return 2
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=True)
    try:
        if args[0] == 'voice':
            from agents.cli_voice import main as run
        else:
            from agents.cli import main as run
        if args[0] == 'sms-register':
            # The gateway adapter validates the HTTPS origin and appends its
            # credential internally. Neither URL nor credential is printed.
            return run(['register-webhook', os.environ.get('PUBLIC_BASE_URL', '').rstrip('/') + '/api/channels/sms/inbound'])
        return run(args[1:])
    except Exception:
        print('UAT command unavailable; check local configuration. No values were printed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
