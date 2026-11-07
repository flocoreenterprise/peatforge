import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { PUBLIC_STORAGE_KEY, parseStoredSession, publicActionLabel, publicRequest, publicSessionDenied } from './client';
import type { PublicThread, PublicVillage, StoredPublicSession } from './client';
import './public.css';
import VoicePanel from '../voice/VoicePanel';

const restore = () => { try { return parseStoredSession(sessionStorage.getItem(PUBLIC_STORAGE_KEY)); } catch { return null; } };
export default function PublicReportPage() {
  const [session, setSession] = useState<StoredPublicSession | null>(restore);
  const [villages, setVillages] = useState<PublicVillage[]>([]);
  const [villageId, setVillageId] = useState('');
  const [consent, setConsent] = useState(false);
  const [thread, setThread] = useState<PublicThread>({ items: [] });
  const [text, setText] = useState('');
  const [notice, setNotice] = useState('');
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceFailed, setVoiceFailed] = useState(false);
  const [textFallback, setTextFallback] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const activeToken = useRef(session?.token ?? ''); activeToken.current = session?.token ?? '';
  const sending = useRef(false);
  const revision = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const pendingText = useRef<{ text: string; id: string } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  function forget() {
    requestController.current?.abort(); requestController.current = null;
    activeToken.current = ''; revision.current++; sending.current = false; pendingText.current = null;
    try { sessionStorage.removeItem(PUBLIC_STORAGE_KEY); } catch { /* In-memory session still closes. */ }
    setVoiceActive(false); setVoiceFailed(false); setTextFallback(false); setSession(null); setThread({ items: [] }); setText(''); setConsent(false); setBusy(false);
  }
  function failure(value: unknown, token?: string) {
    if (token && activeToken.current !== token) return;
    if (publicSessionDenied(value)) { forget(); setError('Sesi laporan tidak berlaku. Pilih desa dan mulai kembali.'); }
    else setError('Layanan laporan belum tersedia. Teks di kotak pesan tetap ada; coba lagi.');
  }
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    void publicRequest<{ items: PublicVillage[] }>('villages', null, controller.signal).then(result => { if (active) { setVillages(result.items); setVillageId(result.items[0]?.id ?? ''); } }).catch(() => { if (active) setError('Belum ada data desa — layanan lokasi belum tersedia.'); });
    return () => { active = false; controller.abort(); requestController.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!session) return;
    const token = session.token, controller = new AbortController(); let active = true, loading = false;
    setThread({ items: [] }); setError('');
    async function poll() {
      if (loading || sending.current) return; loading = true;
      const snapshot = revision.current;
      try { const result = await publicRequest<PublicThread>('messages', token, controller.signal); if (active && activeToken.current === token && revision.current === snapshot) { setThread(result); setError(''); } }
      catch (value) { if (active) failure(value, token); }
      finally { loading = false; }
    }
    void poll(); const timer = setInterval(() => void poll(), 3000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
    // Token alone defines the public identity; never consult operator cookies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [thread.items.length]);
  async function begin(event: FormEvent) {
    event.preventDefault();
    const village = villages.find(item => item.id === villageId);
    if (!village || !consent || sending.current) return;
    sending.current = true; setBusy(true); setError(''); setNotice('');
    const controller = new AbortController(); requestController.current = controller;
    try {
      const result = await publicRequest<PublicThread & { token: string }>('sessions', null, controller.signal, { village_id: village.id, consent: true });
      if (controller.signal.aborted) return;
      const next = parseStoredSession(JSON.stringify({ token: result.token, village_id: village.id, village_name: village.name }));
      if (!next) throw new Error('Sesi belum tersedia.');
      try { sessionStorage.setItem(PUBLIC_STORAGE_KEY, JSON.stringify(next)); } catch { /* Memory-only fallback. */ }
      activeToken.current = next.token; setVoiceFailed(false); setTextFallback(false); setVoiceActive(true); setSession(next); setThread({ ...result, items: result.items ?? [] });
    } catch (value) { if (!controller.signal.aborted) failure(value); }
    finally { if (!controller.signal.aborted) { sending.current = false; setBusy(false); } }
  }
  async function send(event: FormEvent) {
    event.preventDefault(); const value = text.trim(), token = session?.token;
    if (!value || !token || sending.current) return;
    sending.current = true; revision.current++; setBusy(true); setError('');
    const controller = new AbortController(); requestController.current = controller;
    const pending = pendingText.current?.text === value ? pendingText.current : { text: value, id: crypto.randomUUID() }; pendingText.current = pending;
    try {
      const result = await publicRequest<PublicThread>('messages', token, controller.signal, { text: value, event_id: pending.id });
      if (!controller.signal.aborted && activeToken.current === token) { setThread(result); setText(''); pendingText.current = null; }
    } catch (error) { if (!controller.signal.aborted) failure(error, token); }
    finally { if (activeToken.current === token) { sending.current = false; setBusy(false); } }
  }
  return <main className="public-report"><header className="public-heading"><span>PEATFORGE · LAPORAN WARGA</span><h1>Laporkan kondisi di sekitar Anda</h1><p>Tidak perlu nama atau nomor telepon. Jangan mendekati api untuk membuat laporan.</p></header>
    {error && <p className="public-error" role="alert">{error}</p>}
    {!session ? <form className="public-start" onSubmit={event => void begin(event)}><label htmlFor="public-village">Desa lokasi pengamatan</label><select id="public-village" value={villageId} disabled={busy || !villages.length} onChange={event => setVillageId(event.target.value)}>{!villages.length && <option value="">Belum ada data desa</option>}{villages.map(village => <option value={village.id} key={village.id}>{village.name}</option>)}</select><label className="public-consent"><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)}/><span>Saya setuju mikrofon dan suara saya diproses Warden AI melalui OpenAI. Transkrip dan desa pilihan saya disimpan serta dapat dibaca operator untuk pemeriksaan dan tindak lanjut. Saya tidak perlu memasukkan identitas pribadi.</span></label><button disabled={busy || !consent || !villageId}>{busy ? 'Menyiapkan…' : 'Bicara dengan Warden'}</button></form> : <>
      <div className="public-session"><strong>Desa: {session.village_name}</strong><button type="button" disabled={busy} onClick={() => { forget(); setNotice(''); setError(''); }}>Tutup sesi / ganti desa</button></div>
      <p className="public-disclaimer">Warden AI akan membaca ulang laporan. Ucapkan YA hanya jika bacaannya benar. Persetujuan operator belum berarti petugas sudah berangkat.</p>
      {voiceActive ? <VoicePanel publicToken={session.token} autoStart onFallback={() => setVoiceFailed(true)} /> : <button className="public-voice" type="button" disabled={busy} onClick={() => { setVoiceFailed(false); setVoiceActive(true); }}>Bicara dengan Warden</button>}
      {voiceFailed && <button className="public-text-fallback" type="button" onClick={() => setTextFallback(value => !value)}>{textFallback ? 'Sembunyikan pesan teks' : 'Suara tidak tersedia? Lanjut lewat teks'}</button>}
      {notice && <p role="status">{notice}</p>}
      <section className="public-thread" role="log" aria-label="Percakapan Warden">{thread.items.map(message => <article className={`public-message ${message.direction === 'inbound' ? 'public-message-user' : ''}`} key={message.id}><strong>{message.direction === 'inbound' ? 'Anda' : 'Warden AI'}</strong><p>{message.body}</p></article>)}{!thread.items.length && <p>Ceritakan air tanah, asap, api, atau lokasi yang Anda lihat. Angka pengukuran tidak wajib.</p>}<div ref={bottom}/></section>
      {!!thread.actions?.length && <section className="public-actions" aria-label="Tindak lanjut laporan"><h2>Tindak lanjut</h2>{thread.actions.map(action => <article key={action.id}>{action.priority === 'URGENT' && <strong>Mendesak · perlu perhatian operator</strong>}<p>{action.title}</p><small>{publicActionLabel(action.status)}</small></article>)}</section>}
      {textFallback && <form className="public-composer" onSubmit={event => void send(event)}><label htmlFor="public-text">Pesan untuk Warden</label><textarea id="public-text" value={text} disabled={busy} maxLength={500} rows={3} onChange={event => setText(event.target.value)} onFocus={() => setTimeout(() => bottom.current?.scrollIntoView({ block: 'nearest' }), 250)} placeholder="Contoh: asap tipis dekat jembatan desa"/><button disabled={busy || !text.trim()}>{busy ? 'Mengirim…' : 'Kirim pesan'}</button></form>}
    </>}
    <footer>Laporan ditinjau operator. Jangan menunggu balasan aplikasi jika Anda berada dalam bahaya langsung.</footer>
  </main>;
}
