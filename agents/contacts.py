"""Private contact storage. No phone values belong in errors or representations."""
import hashlib
import hmac
import re
from cryptography.fernet import Fernet, InvalidToken
from .db import Database, utc_now


def normalize_phone(phone: str) -> str:
    compact = re.sub(r'[\s()\-]', '', phone)
    if compact.startswith('08'):
        compact = '+62' + compact[1:]
    elif compact.startswith('628'):
        compact = '+' + compact
    if not re.fullmatch(r'\+[1-9]\d{7,14}', compact):
        raise ValueError('Invalid phone format')
    return compact


class Contacts:
    def __init__(self, database: Database, encryption_key: str, salt: str):
        if len(salt) < 32:
            raise ValueError('Contact configuration unavailable')
        try:
            self._cipher = Fernet(encryption_key.encode())
        except (ValueError, TypeError):
            raise ValueError('Contact configuration unavailable') from None
        self._salt = salt.encode()
        self.db = database

    def phone_hash(self, phone: str) -> str:
        return hmac.new(self._salt, normalize_phone(phone).encode(), hashlib.sha256).hexdigest()

    def bind(self, responder_id: str, phone: str, *, consent: bool = False) -> None:
        if not consent:
            raise ValueError('Explicit responder consent required')
        phone = normalize_phone(phone)
        encrypted = self._cipher.encrypt(phone.encode()).decode()
        with self.db.connect() as connection:
            row = connection.execute('UPDATE responders SET phone_encrypted=?,phone_hash=?,phone_last4=?,consent_at=? WHERE id=?',
                (encrypted, self.phone_hash(phone), phone[-4:], utc_now(), responder_id))
            if row.rowcount != 1:
                raise ValueError('Unknown responder')

    def find(self, phone: str) -> str | None:
        with self.db.connect() as connection:
            row = connection.execute('SELECT id FROM responders WHERE phone_hash=?', (self.phone_hash(phone),)).fetchone()
        return row['id'] if row else None

    def phone_for(self, responder_id: str) -> str:
        with self.db.connect() as connection:
            row = connection.execute('SELECT phone_encrypted,consent_at FROM responders WHERE id=?', (responder_id,)).fetchone()
        if not row or not row['phone_encrypted'] or not row['consent_at']:
            raise ValueError('Consented contact unavailable')
        try:
            return self._cipher.decrypt(row['phone_encrypted'].encode()).decode()
        except (InvalidToken, ValueError):
            raise ValueError('Contact unavailable') from None
