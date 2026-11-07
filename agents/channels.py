"""One channel boundary, durable inbound dedupe, and fail-closed SMS authentication."""
from dataclasses import dataclass, field
import hashlib
import hmac
import os
import time
from threading import RLock
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit
import httpx
from pydantic import Field
from .contacts import Contacts
from .db import Database, identifier, utc_now
from .models import Channel, Mode, Model

FIXTURE_SENDER = '+620000000001'  # Deliberately synthetic; never sent to a provider.


@dataclass(repr=False)
class ChannelSettings:
    public_demo: bool = True
    gateway_url: str = ''
    gateway_user: str = field(default='', repr=False)
    gateway_pass: str = field(default='', repr=False)
    webhook_secret: str = field(default='', repr=False)
    signing_key: str = field(default='', repr=False)
    encryption_key: str = field(default='', repr=False)
    phone_salt: str = field(default='', repr=False)

    @classmethod
    def from_environment(cls, *, public_demo: bool) -> 'ChannelSettings':
        return cls(public_demo=public_demo, **{name: os.environ.get(env, '') for name, env in {
            'gateway_url': 'SMS_GATEWAY_URL', 'gateway_user': 'SMS_GATEWAY_USER', 'gateway_pass': 'SMS_GATEWAY_PASS',
            'webhook_secret': 'SMS_WEBHOOK_SECRET', 'signing_key': 'SMS_SIGNING_KEY',
            'encryption_key': 'CONTACT_ENCRYPTION_KEY', 'phone_salt': 'PHONE_HASH_SALT'}.items()})


class ChannelUnavailable(Exception):
    pass


class Inbound(Model):
    responder_id: str
    channel: Channel
    text: str = Field(min_length=1, max_length=500)
    mode: Mode | None = None
    day: int | None = Field(default=None, ge=0, le=61)
    processing_id: str | None = None
    confirmation_report: dict[str, Any] | None = None
    context_captured: bool = False
    trace_id: str | None = None


def verify_webhook(settings: ChannelSettings, key: str | None, raw: bytes,
                   signature: str | None, timestamp: str | None, *, now: float | None = None) -> bool:
    if len(settings.webhook_secret) < 32 or not key or not hmac.compare_digest(settings.webhook_secret.encode(), key.encode()):
        return False
    if settings.signing_key:
        if len(settings.signing_key) < 32 or not signature or not timestamp:
            return False
        try:
            stamp = int(timestamp)
        except ValueError:
            return False
        if abs((time.time() if now is None else now) - stamp) > 300:
            return False
        expected = hmac.new(settings.signing_key.encode(), raw + timestamp.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected.encode(), signature.encode())
    return True


class Gateway:
    def __init__(self, settings: ChannelSettings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def _base(self) -> str:
        url = self.settings.gateway_url.rstrip('/')
        parts = urlsplit(url)
        if (self.settings.public_demo or parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment
                or not self.settings.gateway_user or not self.settings.gateway_pass):
            raise ChannelUnavailable('SMS provider unavailable')
        return url

    def request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        base = self._base()
        try:
            with httpx.Client(transport=self.transport, timeout=10, follow_redirects=False,
                              auth=(self.settings.gateway_user, self.settings.gateway_pass), trust_env=False) as client:
                response = client.request(method, base + endpoint, json=payload)
                response.raise_for_status()
                # Provider response can contain recipients or secrets; only inspect locally.
                return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError):
            raise ChannelUnavailable('SMS provider request failed') from None

    def send(self, phone: str, text: str) -> None:
        endpoint = '/messages' if urlsplit(self._base()).path.rstrip('/').endswith('/3rdparty/v1') else '/message'
        self.request('POST', endpoint, {'textMessage': {'text': text}, 'phoneNumbers': [phone]})

    def check(self) -> dict[str, str]:
        self.request('GET', '/health')
        return {'status': 'available'}

    def register_webhook(self, url: str) -> dict[str, str]:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or not self.settings.webhook_secret):
            raise ChannelUnavailable('Valid HTTPS callback and webhook secret required')
        self.request('POST', '/webhooks', {'event': 'sms:received', 'url': url + '?' + urlencode({'key': self.settings.webhook_secret})})
        return {'status': 'registered'}


class ChannelAdapter:
    def __init__(self, database: Database, settings: ChannelSettings, *, transport: httpx.BaseTransport | None = None):
        self.db, self.settings = database, settings
        self.gateway = Gateway(settings, transport=transport)
        self.on_inbound: Callable[[Inbound], dict[str, Any]] | None = None
        self.prepare_inbound: Callable[[Inbound], Inbound] | None = None
        self._inbound_lock = RLock()

    def contacts(self) -> Contacts:
        if self.settings.public_demo:
            raise ChannelUnavailable('Real contacts disabled in public demo')
        return Contacts(self.db, self.settings.encryption_key, self.settings.phone_salt)

    def send(self, responder_id: str, text: str, channel: Channel = 'sms', *, speech: str | None = None, trace_id: str | None = None) -> dict[str, str]:
        if channel not in ('sms', 'web') or not text.strip() or len(text) > 300:
            raise ValueError('Invalid outbound message')
        with self.db.connect() as connection:
            if not connection.execute('SELECT 1 FROM responders WHERE id=?', (responder_id,)).fetchone():
                raise ValueError('Unknown responder')
        message_id = identifier('m')
        status = 'mock' if self.settings.public_demo or channel == 'web' else 'pending'
        with self.db.connect() as connection:
            connection.execute('INSERT INTO messages(id,responder_id,direction,channel,body,speech,status,trace_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (message_id, responder_id, 'outbound', channel, text, speech, status, trace_id, utc_now()))
        if status == 'pending':
            try:
                self.gateway.send(self.contacts().phone_for(responder_id), text)
                status = 'queued'
            except (ChannelUnavailable, ValueError):
                with self.db.connect() as connection:
                    connection.execute("UPDATE messages SET status='failed' WHERE id=?", (message_id,))
                raise ChannelUnavailable('SMS delivery unavailable') from None
            with self.db.connect() as connection:
                connection.execute('UPDATE messages SET status=? WHERE id=?', (status, message_id))
        return {'message_id': message_id, 'status': status}

    def inbound(self, message: Inbound, *, external_id: str | None = None) -> dict[str, Any]:
        # Single-process app serializes duplicate delivery while callbacks run outside
        # SQLite transactions. Persisted pending rows resume after transient failures.
        with self._inbound_lock:
            return self._inbound(message, external_id=external_id)

    def _inbound(self, message: Inbound, *, external_id: str | None = None) -> dict[str, Any]:
        if not message.text.strip():
            raise ValueError('Empty inbound message')
        duplicate = False
        with self.db.connect() as connection:
            if not connection.execute('SELECT 1 FROM responders WHERE id=?', (message.responder_id,)).fetchone():
                raise ValueError('Unknown responder')
            existing = connection.execute('SELECT * FROM messages WHERE inbound_message_id=?', (external_id,)).fetchone() if external_id else None
            if existing:
                duplicate = True
                if existing['processing_complete']:
                    return {'accepted': True, 'duplicate': True}
                message = Inbound.model_validate_json(existing['inbound_data']) if existing['inbound_data'] else Inbound(
                    responder_id=existing['responder_id'], channel=existing['channel'], text=existing['body'], processing_id=existing['id'])
            else:
                message = self.prepare_inbound(message) if self.prepare_inbound else message
                message = message.model_copy(update={'processing_id': identifier('m')})
                connection.execute('INSERT INTO messages(id,responder_id,direction,channel,body,status,inbound_message_id,created_at,inbound_data) VALUES(?,?,?,?,?,?,?,?,?)',
                    (message.processing_id, message.responder_id, 'inbound', message.channel, message.text, 'received', external_id, utc_now(), message.model_dump_json()))
        result = {'accepted': True, 'duplicate': duplicate}
        if self.on_inbound is not None:
            result.update(self.on_inbound(message))
        with self.db.connect() as connection:
            connection.execute('UPDATE messages SET processing_complete=1 WHERE id=?', (message.processing_id,))
        return result

    def sms_inbound(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get('payload', {})
        if event.get('event') != 'sms:received' or not isinstance(payload, dict):
            raise ValueError('Invalid gateway event')
        sender, text, external_id = payload.get('sender'), payload.get('message'), payload.get('messageId')
        if not all(isinstance(value, str) and value for value in (sender, text, external_id)) or len(external_id) > 200:
            raise ValueError('Invalid gateway event')
        responder_id = ('r1' if sender == FIXTURE_SENDER else None) if self.settings.public_demo else self.contacts().find(sender)
        if not responder_id:
            # Unknown senders are acknowledged, never bound or contacted automatically.
            return {'accepted': True, 'duplicate': False}
        result = self.inbound(Inbound(responder_id=responder_id, channel='sms', text=text), external_id='sms:' + external_id)
        return {'accepted': result['accepted'], 'duplicate': result['duplicate']}
