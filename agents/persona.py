"""Everyday, non-coercive shared voice wording and literal observation categories."""
import re

BYE=re.compile(r'\b(?:matiin|matikan|tutup|udahan|selesai|cukup|stop|goodbye|bye)\b|\bnanti\s+(?:saja|aja)\b|\blagi\s+sibuk\b',re.I)
WATER_QUESTION='Air di parit dekat kebun masih ada atau sudah kering?'
SMOKE_QUESTION='Ada lihat asap atau cium bau terbakar?'
PLACE_QUESTION='Kira-kira dekat mana?'
THANKS='Laporan Bapak/Ibu saya teruskan ke posko. Kalau ada keputusan, saya kabari lewat jalur ini. Terima kasih. Hati-hati.'


def safe_name(name):
    text=str(name or '').strip()
    if re.search(r'\d|https?://|[/<>@]',text,re.I) or re.match(r'(?:responder|penerima uji|demo|test|petugas)',text,re.I):
        return ''
    return re.sub(r'[^\w\s\-\']','',text,flags=re.UNICODE).strip()[:40]


def greeting(name=None,village=None):
    name=safe_name(name)
    person=('Pak/Bu '+name) if name else 'Pak/Bu'
    place=' di '+safe_name(village) if safe_name(village) else ' di kampung'
    return f'Selamat siang, {person}. Saya Warden, asisten AI uji coba Peatforge. Saya ingin mencatat pengamatan Bapak/Ibu{place}. Ada waktu sebentar?'


def water_category(text):
    value=text.lower().strip()
    if re.search(r'\b(?:banjir|meluap|tergenang)\b',value) and not re.search(r'\b(?:tidak|nggak|gak|tak|belum)\s+(?:banjir|meluap|tergenang)\b',value):
        return 'banjir'
    if positive(value,r'\b(?:sedikit|menipis|dangkal|nyaris kering|hampir kering)\b'):
        return 'sedikit'
    if re.search(r'\b(?:tidak|nggak|gak|tak)\s+(?:ada\s+)?air\b|\bair(?:nya)?\s+(?:habis|tidak ada)\b',value):
        return 'kering'
    if re.search(r'\bkering\b',value) and not re.search(r'\b(?:tidak|nggak|gak|belum)\s+kering\b',value):
        return 'kering'
    if re.search(r'\bmasih ada\b|\bada air\b|\bair(?:nya)? ada\b',value):
        return 'masih_ada'
    return None


def positive(text,pattern):
    """Negation attaches to its own observation, not every clause in the report."""
    for match in re.finditer(pattern,text):
        prefix=text[:match.start()]
        if not re.search(r'\b(?:tidak|nggak|gak|tak|belum|bukan)(?:\s+(?:ada|terlihat|kelihatan|tampak|nampak|melihat|lihat|sedang))*\s*$',prefix):
            return True
    return False


def smoke_category(text):
    value=text.lower().strip()
    clauses=re.split(r'[,;.!?]|\b(?:tapi|tetapi|namun|cuma|hanya)\b',value)
    # A burning smell is not observed fire. Require an affirmative fire statement.
    for clause in clauses:
        if clause.strip()=='api' or positive(clause,r'\b(?:ada|terlihat|kelihatan|nampak|tampak|melihat|lihat)\s+api\b') or positive(clause,r'\bapi(?:nya)?\s+(?:menyala|berkobar)\b'):
            return 'api'
    if value.strip('.!?')=='terlihat' or positive(value,r'\b(?:terlihat|kelihatan|nampak|tampak|melihat|lihat)\s+(?:ada\s+)?asap\b'):
        return 'terlihat'
    if positive(value,r'\b(?:bau|tercium|aroma)\b'):
        return 'bau'
    if re.search(r'\b(?:tidak|nggak|gak|tak)\s+(?:(?:ada|terlihat|kelihatan|nampak|tampak)\s+)?(?:asap|bau)\b',value) or value.strip('.!?') in ('tidak','nggak','gak','tidak ada','nggak ada'):
        return 'tidak'
    return None


def category_facts(text, *,water=False,smoke=False):
    result={}
    wc=water_category(text) if water or re.search(r'\b(?:air|handil|parit|sumur)\b',text,re.I) else None
    sc=smoke_category(text) if smoke or re.search(r'\b(?:asap|bau|api)\b',text,re.I) else None
    if wc:
        result['water_category']=wc
    if sc:
        result['smoke_category']=sc
        result['smoke']=False if sc=='tidak' else True if sc=='terlihat' else None
        if sc=='api':
            result['fire']=True
    return result


def readback(words):
    parts=[]
    for key,label in (('water','airnya'),('smoke','keadaan asap atau api'),('place','tempatnya')):
        if words.get(key):
            parts.append(label+' '+words[key])
    return 'Saya baca ulang kata Bapak/Ibu: '+ '; '.join(parts)+'. Apakah sudah sesuai? Jawab YA untuk menyimpan, atau TIDAK untuk memperbaiki.'
