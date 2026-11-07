import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import './field.css';
import VoicePanel from '../voice/VoicePanel';

type Message = { id: string; direction: string; body: string; created_at: string; status: string };
type ResponseBody = { items: Message[]; responder?: { name: string; village_id: string }; state?: string; browser_voice_supported?: boolean };
class FieldError extends Error { constructor(public status: number) { super('Layanan percakapan belum tersedia.'); } }
const displayTime = (value: string) => Number.isNaN(Date.parse(value)) ? '' : new Date(value).toLocaleTimeString('id-ID', { timeZone: 'Asia/Jakarta', hour: '2-digit', minute: '2-digit' }) + ' WIB';
const messageStatus = (status: string) => ({ mock: 'Simulasi pengiriman', sent: 'Terkirim', delivered: 'Diterima', received: 'Diterima', failed: 'Pengiriman gagal', queued: 'Menunggu pengiriman' })[status] ?? 'Tercatat';

export default function FieldInbox({ token }: { token: string }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [name, setName] = useState('');
  const [text, setText] = useState('');
  const [state, setState] = useState('');
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [error, setError] = useState('');
  const [access, setAccess] = useState<'loading' | 'ready' | 'invalid' | 'denied'>('loading');
  const [sending, setSending] = useState(false);
  const inFlight = useRef(false);
  const sendController = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const revision = useRef(0);
  const blocked = useRef(false);

  async function request(path: 'messages' | 'send', signal: AbortSignal, body?: string): Promise<ResponseBody> {
    const response = await fetch(`/api/field/${encodeURIComponent(token)}/${path}`, {
      method: body === undefined ? 'GET' : 'POST', signal: AbortSignal.any([signal, AbortSignal.timeout(12000)]),
      credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body,
    });
    if (!response.ok) throw new FieldError(response.status);
    return response.json() as Promise<ResponseBody>;
  }
  function failure(value: unknown) {
    if (value instanceof FieldError && value.status === 404) { blocked.current = true; setAccess('invalid'); setMessages([]); setName(''); }
    else if (value instanceof FieldError && (value.status === 401 || value.status === 403)) { blocked.current = true; setAccess('denied'); setMessages([]); setName(''); }
    else setError('Sambungan terputus. Pesan terakhir tetap ditampilkan; coba lagi.');
  }
  useEffect(() => {
    const controller = new AbortController(); let stopped = false; let busy = false; let terminal = false;
    generation.current += 1;
    blocked.current = false; revision.current = 0;
    setMessages([]); setName(''); setText(''); setState(''); setVoiceSupported(false); setError(''); setAccess('loading'); setSending(false); inFlight.current = false;
    async function load() {
      if (busy || terminal || blocked.current || inFlight.current) return; busy = true;
      const currentRevision = revision.current;
      try {
        const result = await request('messages', controller.signal);
        if (!stopped && !blocked.current && currentRevision === revision.current) { setMessages(result.items); setName(result.responder?.name ?? ''); setAccess('ready'); setVoiceSupported(result.browser_voice_supported === true); setError(''); if (result.state) setState(result.state); }
      } catch (value) {
        if (!stopped) {
          terminal = value instanceof FieldError && [401, 403, 404].includes(value.status);
          failure(value);
        }
      } finally { busy = false; }
    }
    void load(); const timer = window.setInterval(() => void load(), 3000);
    return () => { stopped = true; controller.abort(); sendController.current?.abort(); window.clearInterval(timer); };
    // The scoped token is the only request identity; never consult operator state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [messages.at(-1)?.id]);

  async function send(value: string) {
    const body = value.trim();
    if (inFlight.current || access !== 'ready' || !body || body.length > 500) return;
    const currentGeneration = generation.current;
    revision.current += 1;
    const controller = new AbortController(); sendController.current = controller; inFlight.current = true; setSending(true); setError('');
    try {
      const result = await request('send', controller.signal, JSON.stringify({ text: body }));
      if (currentGeneration === generation.current) { setMessages(result.items); setState(result.state ?? ''); setText(''); }
    } catch (value) {
      if (currentGeneration === generation.current) {
        failure(value);
        if (!(value instanceof FieldError && [401, 403, 404].includes(value.status))) setError('Pengiriman belum dapat dipastikan. Periksa percakapan sebelum mengirim ulang.');
      }
    } finally { if (currentGeneration === generation.current) { inFlight.current = false; setSending(false); } }
  }
  function submit(event: FormEvent) { event.preventDefault(); void send(text); }

  if (access === 'invalid' || access === 'denied') return <main className="pf-field pf-field-unavailable"><h1>{access === 'invalid' ? 'Link ini tidak berlaku' : 'Akses percakapan ditolak'}</h1><p>Hubungi operator untuk mendapatkan link yang berlaku.</p></main>;
  return <main className="pf-field">
    <header className="pf-field-header"><h1>Percakapan Peatforge</h1><p>{name || 'Memuat identitas petugas…'}</p><small>Jaga link ini agar hanya Anda yang menggunakannya.</small></header>
    {access === 'ready' && voiceSupported && <VoicePanel fieldToken={token}/>}
    <section className="pf-field-thread" aria-label="Percakapan dengan Warden" aria-busy={access === 'loading'}>
      {access === 'loading' && <p>Memuat percakapan…</p>}
      {access === 'ready' && !messages.length && <p>Belum ada pesan. Kirim laporan lapangan kepada Warden.</p>}
      {messages.map(message => <article className={`pf-field-bubble ${message.direction === 'inbound' ? 'pf-field-mine' : ''}`} key={message.id}>
        <small>{message.direction === 'inbound' ? 'Anda' : 'Warden'}</small><p>{message.body}</p><small>{displayTime(message.created_at)} · {messageStatus(message.status)}</small>
      </article>)}<div ref={bottom} />
    </section>
    <footer className="pf-field-compose">
      {error && <p role="alert">{error}</p>}
      {state === 'confirm' && <p role="status">Periksa pembacaan ulang Warden. Kirim YA jika benar, atau TIDAK untuk membatalkan.</p>}
      {state === 'saved' && <p role="status">Laporan telah dicatat. Tindakan lapangan tetap memerlukan keputusan operator.</p>}
      <div className="pf-field-quick"><button type="button" disabled={sending || access !== 'ready'} onClick={() => void send('YA')}>YA</button><button type="button" disabled={sending || access !== 'ready'} onClick={() => void send('TIDAK')}>TIDAK</button></div>
      <form onSubmit={submit}><label htmlFor="pf-field-message">Pesan kepada Warden</label><textarea id="pf-field-message" value={text} maxLength={500} rows={2} disabled={sending || access !== 'ready'} onChange={event => setText(event.target.value)} aria-describedby="pf-field-help" />
        <div className="pf-field-send"><small id="pf-field-help">Maksimal 500 karakter. Sebutkan air tanah, asap, dan lokasi.</small><button type="submit" disabled={sending || access !== 'ready' || !text.trim()}>{sending ? 'Mengirim…' : 'Kirim'}</button></div>
      </form>
    </footer>
  </main>;
}
