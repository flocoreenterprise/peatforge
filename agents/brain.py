"""Offline wording first. No provider SDK, credentials, arithmetic, or network."""
import re

_NUMBERS = re.compile(r'[+-]?(?:\d+(?:[.,]\d+)*|[.,]\d+)')
_URLS = re.compile(r'(?:https?://|www\.|/f/)[^\s<>]+', re.IGNORECASE)


def safe_rewrite(original: str, candidate: str, max_length: int = 300) -> str:
    """Accept wording only when every numeric token and URL stays in order."""
    if not candidate.strip() or len(candidate) > max_length:
        return original
    if _NUMBERS.findall(candidate) != _NUMBERS.findall(original):
        return original
    if _URLS.findall(candidate) != _URLS.findall(original):
        return original
    return candidate


class OfflineBrain:
    mode = 'offline'

    def render(self, intent: str, *, facts: str = '') -> str:
        templates = {
            'reminder': 'Mohon kirim laporan air atau asap. Laporan akan dibaca ulang sebelum disimpan.',
            'thanks': 'Terima kasih. Laporan telah dicatat untuk ditinjau petugas.',
            'unavailable': 'Data belum tersedia. Mohon tunggu pemeriksaan petugas.',
            'readback': 'Mohon konfirmasi: {facts}. Balas YA atau TIDAK.',
            'alert': '{facts}. Perkiraan model, belum diukur. Mohon cek kondisi setempat.',
        }
        if intent not in templates:
            raise ValueError('Unknown template intent')
        text = templates[intent].format(facts=facts)
        if len(text) > 300:
            raise ValueError('Message exceeds SMS limit')
        return text
