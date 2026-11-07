"""Deterministic readback state machine, shared by SMS and the demo input."""
import re
from threading import RLock
from typing import Callable, Any
from .channels import ChannelAdapter, Inbound
from .db import Database
from .models import ObservationInput
from .tools import ToolService

_ONES = dict(zip(('nol', 'satu', 'dua', 'tiga', 'empat', 'lima', 'enam', 'tujuh', 'delapan', 'sembilan'), range(10)))
_NUMBER_WORDS = set(_ONES) | {'sepuluh', 'sebelas', 'belas', 'puluh', 'seratus', 'ratus', 'koma'}


def number_words(words: list[str]) -> float:
    if not words:
        raise ValueError('Missing water depth')
    if 'koma' in words:
        index = words.index('koma')
        fraction = ''.join(str(_ONES[word]) for word in words[index+1:])
        if not fraction:
            raise ValueError('Missing decimal depth')
        return float(f'{int(number_words(words[:index]))}.{fraction}')
    if all(word in _ONES for word in words):
        return float(''.join(str(_ONES[word]) for word in words))
    total, current = 0, 0
    for word in words:
        if word in _ONES:
            current += _ONES[word]
        elif word == 'sepuluh':
            current += 10
        elif word == 'sebelas':
            current += 11
        elif word == 'belas':
            current += 10
        elif word == 'puluh':
            current *= 10
        elif word in ('seratus', 'ratus'):
            total += 100 if word == 'seratus' else current * 100
            current = 0
        else:
            raise ValueError('Unrecognized depth')
    return float(total + current)


def parse_report(text: str) -> tuple[dict[str, Any] | None, bool]:
    """Return parsed facts and clarification flag; embedded YA is smoke data only."""
    normalized = text.lower().strip()
    facts: dict[str, Any] = {}
    air = re.search(r'\bair\b\s*(?:(?:tanah|kurang|lebih|sekitar|sedalam|nya)\s*)*', normalized)
    if air:
        tail = normalized[air.end():]
        match = re.match(r'([+-]?\d+(?:[.,]\d+)?)\s*(cm|sentimeter|senti|meter|metre|m)?\b', tail)
        if match:
            depth = float(match.group(1).replace(',', '.'))
            unit = match.group(2)
        else:
            tokens = re.findall(r'\w+', tail)
            words = []
            for token in tokens:
                if token not in _NUMBER_WORDS:
                    break
                words.append(token)
            if not words:
                return None, True
            try:
                depth = number_words(words)
            except (ValueError, KeyError):
                return None, True
            unit = tokens[len(words)] if len(tokens) > len(words) else None
        if unit in ('m', 'meter', 'metre'):
            depth *= 100
        if not 0 <= depth <= 150:
            return None, True
        facts['wt_cm'] = depth
    smoke = re.search(r'\basap\s+(ya|iya|ada|tidak|nggak|tidak ada)\b', normalized)
    if smoke:
        facts['smoke'] = smoke.group(1) in ('ya', 'iya', 'ada')
    elif re.search(r'\basap\b', normalized):
        return None, True
    return (facts or None), False


def message_items(database: Database, responder_id: str, after: str | None = None) -> list[dict]:
    columns = 'id,responder_id,direction,channel,body,speech,status,trace_id,created_at'
    with database.connect() as connection:
        rows = connection.execute(f'SELECT {columns} FROM (SELECT {columns} FROM messages WHERE responder_id=? AND (? IS NULL OR created_at>?) ORDER BY created_at DESC,rowid DESC LIMIT 200) ORDER BY created_at',
                                  (responder_id, after, after)).fetchall()
    return [dict(row) for row in rows]


class Warden:
    def __init__(self, database: Database, tools: ToolService, channels: ChannelAdapter):
        self.db, self.tools, self.channels = database, tools, channels
        self._lock = RLock()
        self.after_confirm: Callable[[dict, dict], Any] | None = None

    def _key(self, responder_id: str) -> str:
        return 'warden:' + responder_id

    def reset_for_alert(self, responder_id: str, mode: str, day: int, trace_id: str) -> None:
        with self._lock:
            self.db.set(self._key(responder_id), {'state': 'idle', 'mode': mode, 'day': day, 'trace_id': trace_id})

    def prepare(self, message: Inbound) -> Inbound:
        """Capture context at receipt, so a later retry cannot adopt a newer alert."""
        with self._lock:
            state = self.db.get(self._key(message.responder_id), {'mode': 'live', 'day': 0})
            return message.model_copy(update={
                'mode': message.mode if message.mode is not None else state.get('mode', 'live'),
                'day': message.day if message.day is not None else state.get('day', 0),
                'context_captured': True,
                'confirmation_report': state.get('pending') if state.get('state') == 'confirm' else None})

    def _confirm(self, message: Inbound, state: dict, key: str) -> dict:
        checkpoint_key = 'warden-confirm:' + (message.processing_id or key)
        checkpoint = self.db.get(checkpoint_key)
        if not checkpoint:
            pending = message.confirmation_report if message.context_captured else state.get('pending')
            if not pending:
                return self._reply(message, state, 'Belum ada laporan yang menunggu konfirmasi. Kirim AIR dalam cm dan ASAP YA atau TIDAK.')
            checkpoint = {'observation': pending}
            self.db.set(checkpoint_key, checkpoint)
        observation = ObservationInput(**checkpoint['observation'], confirmed=True)
        saved = checkpoint.get('saved')
        if saved is None:
            saved = self.tools.log_field_observation(observation, observation_id='o_' + (message.processing_id or key))
            checkpoint['saved'] = saved
            self.db.set(checkpoint_key, checkpoint)
        if state.get('pending') == checkpoint['observation']:
            state.pop('pending', None)
            state.update(state='saved', observation_id=saved['observation_id'])
            self.db.set(key, state)
        # A failed B5 hook resumes with the same observation; the hook must use
        # observation_id idempotently. Never create another observation on retry.
        if self.after_confirm and not checkpoint.get('hook_done'):
            self.after_confirm(observation.model_dump(), saved)
            checkpoint['hook_done'] = True
            self.db.set(checkpoint_key, checkpoint)
        return self._reply(message, state, 'Terima kasih. Laporan terkonfirmasi telah dicatat untuk ditinjau petugas.', observation_id=saved['observation_id'])

    def _reply(self, message: Inbound, state: dict, text: str, **extra) -> dict:
        self.channels.send(message.responder_id, text, message.channel if message.channel in ('sms', 'web') else 'web', trace_id=state.get('trace_id'))
        return {'items': message_items(self.db, message.responder_id), 'state': state['state'], **extra}

    def handle(self, message: Inbound) -> dict:
        with self._lock:
            return self._handle(message)

    def _handle(self, message: Inbound) -> dict:
        with self.db.connect() as connection:
            responder = connection.execute('SELECT village_id FROM responders WHERE id=?', (message.responder_id,)).fetchone()
        if not responder:
            raise ValueError('Unknown responder')
        key = self._key(message.responder_id)
        state = self.db.get(key, {'state': 'idle', 'mode': 'live', 'day': 0})
        text = message.text.strip().lower()
        standalone = re.sub(r'[.!?]+$', '', text).strip()
        if standalone in ('ya', 'iya'):
            return self._confirm(message, state, key)
        if standalone == 'tidak':
            state.pop('pending', None)
            state['state'] = 'idle'
            self.db.set(key, state)
            return self._reply(message, state, 'Laporan belum disimpan. Silakan kirim koreksi AIR dan ASAP.')
        if re.search(r'\banonim\b', text):
            state.pop('pending', None)
            state['state'] = 'idle'
            self.db.set(key, state)
            return self._reply(message, state, 'Fitur anonim belum tersedia pada demo ini. Jangan kirim informasi pribadi.')
        if standalone in ('ini siapa', 'siapa ini'):
            return self._reply(message, state, 'Ini Warden Peatforge, pencatat laporan untuk ditinjau petugas. Kanal demo memakai simulasi pengiriman.')
        if standalone in ('telp', 'telepon'):
            return self._reply(message, state, 'Panggilan belum tersedia. Silakan kirim AIR dalam cm dan ASAP YA atau TIDAK melalui pesan.')
        facts, clarify = parse_report(text)
        if clarify:
            state.pop('pending', None)
            state['state'] = 'idle'
            self.db.set(key, state)
            return self._reply(message, state, 'Mohon perjelas kedalaman air dalam cm dari permukaan gambut. Jika memakai meter, tulis nilainya jelas. Laporan belum disimpan.')
        if facts:
            mode = message.mode if message.mode is not None else state.get('mode', 'live')
            day = message.day if message.day is not None else state.get('day', 0)
            if mode == 'live' and day != 0:
                raise ValueError('Live mode requires day zero')
            landmark = self.tools.resolve_landmark(responder['village_id'], text)
            pending = dict(village_id=responder['village_id'], responder_id=message.responder_id,
                           mode=mode, day=day, landmark_id=landmark.landmark_id, note=message.text, **facts)
            observation = ObservationInput(**pending)
            state.update(state='confirm', pending=observation.model_dump(exclude={'confirmed'}), mode=mode, day=day)
            self.db.set(key, state)
            parts = []
            if observation.wt_cm is not None:
                parts.append(f'air {observation.wt_cm:g} cm dari permukaan gambut')
            if observation.smoke is not None:
                parts.append('asap ada' if observation.smoke else 'asap tidak ada')
            if landmark.name:
                parts.append(landmark.name)
            return self._reply(message, state, 'Baca ulang: ' + ', '.join(parts) + '. Balas YA untuk menyimpan atau TIDAK untuk koreksi.')
        return self._reply(message, state, 'Mohon kirim laporan AIR dalam cm dan ASAP YA atau TIDAK. Konfirmasi laporan hanya dengan pesan YA tersendiri.')
