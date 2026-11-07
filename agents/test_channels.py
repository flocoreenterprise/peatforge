import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from .app import create_app
from .channels import ChannelAdapter, ChannelSettings, ChannelUnavailable, FIXTURE_SENDER, Gateway, Inbound, verify_webhook
from .contacts import Contacts, normalize_phone
from .db import Database
from .tools import seed_mock


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'channels.sqlite3')
        self.db.initialize()
        seed_mock(self.db)
        self.settings = ChannelSettings(public_demo=False, gateway_url='https://gateway.test/3rdparty/v1',
            gateway_user='user', gateway_pass='password', webhook_secret='s' * 32,
            encryption_key=Fernet.generate_key().decode(), phone_salt='h' * 32)

    def test_normalization_encryption_hash_consent(self):
        contacts = Contacts(self.db, self.settings.encryption_key, self.settings.phone_salt)
        self.assertEqual(normalize_phone('0812-3456-7890'), '+6281234567890')
        self.assertEqual(normalize_phone('6281234567890'), '+6281234567890')
        with self.assertRaises(ValueError):
            contacts.bind('r1', '081234567890')
        contacts.bind('r1', '081234567890', consent=True)
        self.assertEqual(contacts.find('+6281234567890'), 'r1')
        self.assertEqual(contacts.phone_for('r1'), '+6281234567890')
        with self.db.connect() as connection:
            row = connection.execute('SELECT * FROM responders WHERE id=?', ('r1',)).fetchone()
        self.assertNotIn('6281234567890', row['phone_encrypted'])
        self.assertEqual(row['phone_last4'], '7890')
        with self.assertRaises(ValueError):
            Contacts(self.db, '', '')

    def test_gateway_shapes_basic_auth_and_paths(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(202, json={'id': 'provider-private-response'})
        transport = httpx.MockTransport(handler)
        contacts = Contacts(self.db, self.settings.encryption_key, self.settings.phone_salt)
        contacts.bind('r1', '081234567890', consent=True)
        adapter = ChannelAdapter(self.db, self.settings, transport=transport)
        self.assertEqual(adapter.send('r1', 'Cek kanal 4')['status'], 'queued')
        self.assertEqual(requests[0].url.path, '/3rdparty/v1/messages')
        self.assertTrue(requests[0].headers['Authorization'].startswith('Basic '))
        self.assertEqual(json.loads(requests[0].content), {'textMessage': {'text': 'Cek kanal 4'}, 'phoneNumbers': ['+6281234567890']})
        self.settings.gateway_url = 'http://127.0.0.1:8080'
        adapter.gateway.send('+6281234567890', 'test')
        self.assertEqual(requests[-1].url.path, '/message')

    def test_provider_401_never_falls_back_to_mock(self):
        contacts = Contacts(self.db, self.settings.encryption_key, self.settings.phone_salt)
        contacts.bind('r1', '081234567890', consent=True)
        adapter = ChannelAdapter(self.db, self.settings, transport=httpx.MockTransport(lambda request: httpx.Response(401)))
        with self.assertRaises(ChannelUnavailable):
            adapter.send('r1', 'test')
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('SELECT status FROM messages').fetchone()['status'], 'failed')

    def test_public_demo_never_uses_provider_or_real_contacts(self):
        self.settings.public_demo = True
        adapter = ChannelAdapter(self.db, self.settings, transport=httpx.MockTransport(lambda request: self.fail('Network attempted')))
        self.assertEqual(adapter.send('r1', 'test')['status'], 'mock')
        with self.assertRaises(ChannelUnavailable):
            adapter.contacts()
        with self.assertRaises(ChannelUnavailable):
            adapter.gateway.register_webhook('https://callback.test/api/channels/sms/inbound')
        self.assertTrue(adapter.sms_inbound({'event': 'sms:received', 'payload': {'sender': '+6281234567890', 'message': 'test', 'messageId': 'unknown'}})['accepted'])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) AS n FROM messages WHERE direction='inbound'").fetchone()['n'], 0)

    def test_sms_sender_normalization_and_dedupe(self):
        Contacts(self.db, self.settings.encryption_key, self.settings.phone_salt).bind('r1', '081234567890', consent=True)
        adapter = ChannelAdapter(self.db, self.settings)
        event = {'event': 'sms:received', 'payload': {'sender': '6281234567890', 'message': 'AIR 61', 'messageId': 'event-1'}}
        self.assertFalse(adapter.sms_inbound(event)['duplicate'])
        self.assertTrue(adapter.sms_inbound(event)['duplicate'])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) AS n FROM messages').fetchone()['n'], 1)
        with self.assertRaises(ValueError):
            adapter.sms_inbound({'event': 'sms:received', 'payload': {'phoneNumber': '081234567890', 'message': 'AIR 61', 'messageId': 'event-2'}})
        self.assertFalse(adapter.inbound(Inbound(responder_id='r1', channel='web', text='test'))['duplicate'])

    def test_hmac_raw_body_timestamp_and_unset_fail_closed(self):
        raw, timestamp = b'{"event":"test"}', '1000'
        self.assertFalse(verify_webhook(ChannelSettings(), '', raw, None, None, now=1000))
        self.assertFalse(verify_webhook(ChannelSettings(webhook_secret='short'), 'short', raw, None, None, now=1000))
        self.assertFalse(verify_webhook(self.settings, '🔒', raw, None, None, now=1000))
        self.assertTrue(verify_webhook(self.settings, 's' * 32, raw, None, None, now=1000))
        self.settings.signing_key = 'k' * 32
        signature = hmac.new(b'k' * 32, raw + timestamp.encode(), hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook(self.settings, 's' * 32, raw, signature, timestamp, now=1000))
        for body, sig, stamp, now in ((raw+b' ', signature, timestamp, 1000), (raw, None, timestamp, 1000), (raw, signature, timestamp, 1301), (raw, signature, 'bad', 1000)):
            self.assertFalse(verify_webhook(self.settings, 's'*32, body, sig, stamp, now=now))
        self.assertFalse(verify_webhook(self.settings, 's'*32, raw, '🔒', timestamp, now=1000))

    def test_webhook_registration_and_safe_response(self):
        requests = []
        gateway = Gateway(self.settings, transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(201, json={'url': 'secret'})))
        self.assertEqual(gateway.register_webhook('https://callback.test/api/channels/sms/inbound'), {'status': 'registered'})
        payload = json.loads(requests[0].content)
        self.assertEqual(requests[0].url.path, '/3rdparty/v1/webhooks')
        self.assertEqual(payload['event'], 'sms:received')
        self.assertTrue(payload['url'].endswith('?key=' + 's'*32))

    def test_webhook_http_boundary_requires_secret_and_dedupes(self):
        path = Path(self.temp.name) / 'app.sqlite3'
        with patch.dict('os.environ', {'SMS_WEBHOOK_SECRET': 's'*32, 'SMS_SIGNING_KEY': ''}):
            app = create_app(db_path=path, public_demo=True)
            with TestClient(app) as client:
                event = {'event': 'sms:received', 'payload': {'sender': FIXTURE_SENDER, 'message': 'AIR 61', 'messageId': 'fixture-1'}}
                self.assertEqual(client.post('/api/channels/sms/inbound', json=event).status_code, 403)
                first = client.post('/api/channels/sms/inbound?key=' + 's'*32, json=event)
                second = client.post('/api/channels/sms/inbound?key=' + 's'*32, json=event)
                self.assertEqual(first.json(), {'accepted': True, 'duplicate': False})
                self.assertEqual(second.json(), {'accepted': True, 'duplicate': True})
                bad = client.post('/api/channels/sms/inbound?key=' + 's'*32, content='not json')
                self.assertEqual(bad.status_code, 422)
                nested = client.post('/api/channels/sms/inbound?key=' + 's'*32,
                    content='[' * 20000 + '0' + ']' * 20000,
                    headers={'Content-Type': 'application/json'})
                self.assertEqual(nested.status_code, 422)
                self.assertEqual(nested.json()['error']['code'], 'invalid')
                self.assertNotIn(FIXTURE_SENDER, first.text)


if __name__ == '__main__':
    unittest.main()
