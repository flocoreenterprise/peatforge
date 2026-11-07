import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { VoiceEvents, VoiceResources } from './events';
import type { Transcript } from './events';
import { ValidatedAudioGate } from './audioGate';
import { callLifetimeMs, ephemeralCurrent } from './sessionClock';
import './voice.css';

export type VoicePanelProps = { fieldToken?: string; responderId?: string; mode?: 'live' | 'replay'; day?: number; onChanged?: () => void; publicToken?: string; autoStart?: boolean; onFallback?: () => void };
export const publicVoiceLifetimeMs = (expiresAt?: number | string, now = Date.now()) => {
  if (expiresAt === undefined) return 600000;
  const deadline = typeof expiresAt === 'number' ? expiresAt * 1000 : Date.parse(expiresAt);
  return Math.max(1, Math.min(600000, deadline - now || 1));
};
type Phase = 'idle' | 'connecting' | 'live' | 'simulation' | 'ended' | 'denied';
type Session = { session_id: string; client_secret?: string; server_connect?: boolean; expires_at: number | string; session_expires_at?: number | string; model: string; voice: string };
type Simulation = { items: { id?: string; role?: string; direction?: string; text?: string; body?: string }[]; state?: string; session_id?: string; ended?: boolean };
class VoiceError extends Error { constructor(public status: number) { super('Layanan suara belum tersedia.'); } }
const denied = (error: unknown) => error instanceof VoiceError && [401, 403, 404].includes(error.status);

export default function VoicePanel({ fieldToken, responderId, mode = 'live', day = 0, onChanged, publicToken, autoStart = false, onFallback }: VoicePanelProps) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [notice, setNotice] = useState('');
  const [entries, setEntries] = useState<Transcript[]>([]);
  const [muted, setMuted] = useState(false);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [pipelineState, setPipelineState] = useState('');
  const audio = useRef<HTMLAudioElement>(null);
  const runtime = useRef<VoiceResources | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const generation = useRef(0);
  const busy = useRef(false);
  const closeServer = useRef<(() => void) | null>(null);
  const pendingText = useRef<{ text: string; id: string } | null>(null);
  const simulationSession = useRef<string | null>(null);
  const mutedRef = useRef(false);
  const audioBusy = useRef(false);
  const changed = useRef(onChanged);
  changed.current = onChanged;

  function bodyScope() { if (publicToken) return { mode, day }; return { ...(responderId ? { responder_id: responderId } : {}), mode, day }; }
  async function request<T>(path: string, body: unknown, signal: AbortSignal): Promise<T> {
    const response = await fetch(`${publicToken ? "/api/public/voice" : "/api/voice"}/${path}`, {
      method: 'POST', credentials: fieldToken || publicToken ? 'omit' : 'same-origin', cache: 'no-store', referrerPolicy: 'no-referrer', redirect: 'error',
      headers: { 'Content-Type': 'application/json', ...(fieldToken ? { 'X-Field-Token': fieldToken } : {}), ...(publicToken ? { Authorization: `Bearer ${publicToken}` } : {}) },
      body: JSON.stringify(body), signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    });
    if (!response.ok) throw new VoiceError(response.status);
    return response.json() as Promise<T>;
  }
  function dispose() {
    generation.current += 1;
    runtime.current?.close(); runtime.current = null; stream.current = null;
    closeServer.current?.(); closeServer.current = null;
    simulationSession.current = null;
    audioBusy.current = false;
    busy.current = false;
    if (audio.current) { audio.current.pause(); audio.current.srcObject = null; }
  }
  function fail(error: unknown, version: number) {
    if (version !== generation.current) return;
    dispose(); setMuted(false); setSending(false);
    if (publicToken) onFallback?.();
    if (denied(error)) {
      setPhase('denied'); setEntries([]); setText(''); pendingText.current = null;
      setNotice('Akses percakapan ditolak atau tautan sudah tidak berlaku.');
    } else {
      setPhase('simulation');
      setNotice(error instanceof VoiceError && error.status === 429 ? 'Batas demo suara tercapai (200 sesi per jam, maksimal 600 detik per sesi). Coba lagi nanti atau gunakan Simulasi teks.' : 'Suara belum tersedia. Gunakan Simulasi teks; laporan diproses oleh Warden yang sama.');
    }
  }
  useEffect(() => {
    setPhase('idle'); setNotice(''); setEntries([]); setText(''); setMuted(false); mutedRef.current = false; setSending(false); setPipelineState(''); pendingText.current = null;
    return dispose;
    // A different scope must release the previous microphone and all pending requests.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldToken, publicToken, responderId, mode, day]);

  useEffect(() => {
    if (!autoStart) return;
    const timer = setTimeout(() => { void start(); }, 0);
    return () => clearTimeout(timer);
    // Mounted only after an explicit consented user action.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, publicToken]);

  async function start() {
    if (busy.current || phase === 'denied') return;
    dispose(); busy.current = true;
    const version = generation.current;
    const resources = new VoiceResources(); runtime.current = resources;
    const controller = new AbortController(); resources.add(() => controller.abort());
    const active = () => version === generation.current;
    setPhase('connecting'); setNotice('Menyiapkan sambungan suara…'); setEntries([]); setMuted(false); mutedRef.current = false;
    try {
      // Only this explicit button handler may request microphone access.
      const session = await request<Session>('session', bodyScope(), controller.signal);
      if (!active()) return;
      if (!session.session_id || (!session.server_connect && (typeof session.client_secret !== 'string' || !session.client_secret))) throw new Error('Sesi tidak tersedia.');
      const sessionId = session.session_id;
      const endBody = { session_id: sessionId };
      closeServer.current = () => { void request('end', endBody, new AbortController().signal).catch(() => {}); };
      if (!session.server_connect && !ephemeralCurrent(session.expires_at)) throw new Error('Sesi kedaluwarsa.');
      const sessionTimer = setTimeout(() => { if (active()) { dispose(); setPhase('ended'); setNotice('Batas sesi berakhir. Mulai lagi bila diperlukan.'); } }, session.server_connect ? publicVoiceLifetimeMs(session.session_expires_at) : callLifetimeMs(session.session_expires_at));
      resources.add(() => clearTimeout(sessionTimer));
      if (!navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection === 'undefined' || typeof MediaRecorder === 'undefined') throw new Error('Suara tidak didukung.');
      const media = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      resources.add(() => media.getTracks().forEach(track => track.stop()));
      if (!active()) return;
      media.getAudioTracks().forEach(track => { track.enabled = false; });
      stream.current = media;
      const pc = new RTCPeerConnection(); resources.add(() => { pc.ontrack = null; pc.onconnectionstatechange = null; pc.close(); });
      const channel = pc.createDataChannel('oai-events');
      resources.add(() => { channel.onmessage = null; channel.onopen = null; channel.onerror = null; channel.onclose = null; channel.close(); });
      const sendRaw = (event: unknown) => { if (channel.readyState !== 'open') throw new Error('Sambungan terputus.'); channel.send(JSON.stringify(event)); };
      let remote: MediaStream | null = null;
      let events: VoiceEvents;
      let rejectPlayback: ((error: Error) => void) | null = null;
      const gate = new ValidatedAudioGate({
        recorder: () => {
          if (!remote) throw new Error('Suara belum tersedia.');
          return new MediaRecorder(remote);
        },
        send: () => sendRaw({ type: 'response.create' }),
        url: blob => URL.createObjectURL(blob), revoke: url => URL.revokeObjectURL(url),
        play: url => new Promise<void>((resolve, reject) => {
          if (!active() || !audio.current) { reject(new Error('Sesi berakhir.')); return; }
          rejectPlayback = reject;
          audio.current.onended = () => { rejectPlayback = null; resolve(); }; audio.current.onerror = () => { rejectPlayback = null; reject(new Error('Pemutaran gagal.')); };
          audio.current.srcObject = null; audio.current.src = url; audio.current.muted = false;
          void audio.current.play().catch(error => { rejectPlayback = null; reject(error); });
        }),
        stopPlayback: () => { const cancel = rejectPlayback; rejectPlayback = null; cancel?.(new Error('Pemutaran dihentikan.')); if (audio.current) { audio.current.pause(); audio.current.onended = null; audio.current.onerror = null; audio.current.removeAttribute('src'); audio.current.load(); } },
        busy: value => {
          if (!active()) return;
          audioBusy.current = value;
          media.getAudioTracks().forEach(track => { track.enabled = !value && !mutedRef.current; });
          setNotice(value ? 'Warden menyiapkan jawaban; angka diperiksa sebelum suara diputar.' : 'Warden tersambung. Silakan bicara.');
        },
        fail: () => fail(new Error('Suara belum dapat diverifikasi.'), version),
        settled: () => events?.playbackSettled(),
      });
      resources.add(() => gate.close());
      const send = (event: unknown) => {
        if (event && typeof event === 'object' && 'type' in event && event.type === 'response.create') gate.request();
        else sendRaw(event);
      };
      events = new VoiceEvents({
        persist: entry => request('transcript', { session_id: sessionId, event_id: entry.id, role: entry.role, text: entry.text }, controller.signal),
        tool: (callId, name, args) => request('tool', { session_id: sessionId, call_id: callId, name, arguments: args }, controller.signal),
        send,
        transcript: entry => { if (active()) { setEntries(previous => [...previous, entry]); changed.current?.(); } },
        ended: () => { if (active()) { dispose(); setPhase('ended'); setNotice('Percakapan selesai.'); } },
        validated: (responseId, itemId) => gate.approved(responseId, itemId),
        failed: () => fail(new Error('Transkripsi belum tersedia.'), version),
      });
      resources.add(() => events.close());
      channel.onmessage = event => {
        if (!active()) return;
        try {
          const parsed = JSON.parse(event.data);
          if (parsed.type === 'input_audio_buffer.speech_stopped') { audioBusy.current = true; media.getAudioTracks().forEach(track => { track.enabled = false; }); setNotice('Laporan diterima; Warden menyiapkan jawaban.'); }
          gate.handle(parsed); if (active()) void events.handle(parsed).catch(error => fail(error, version));
        }
        catch { fail(new Error('Sambungan terputus.'), version); }
      };
      channel.onerror = () => fail(new Error('Sambungan terputus.'), version);
      channel.onclose = () => { if (active()) fail(new Error('Sambungan terputus.'), version); };
      channel.onopen = () => {
        if (!active()) return;
        busy.current = false; setPhase('live'); setNotice('Warden tersambung. Mikrofon aktif.');
        try { send({ type: 'response.create' }); } catch (error) { fail(error, version); }
      };
      pc.ontrack = event => {
        if (!active()) { event.track.stop(); return; }
        remote = event.streams[0] ?? new MediaStream([event.track]);
        resources.add(() => event.track.stop());
        gate.streamReady();
      };
      pc.onconnectionstatechange = () => { if (active() && ['failed', 'disconnected', 'closed'].includes(pc.connectionState)) fail(new Error('Sambungan terputus.'), version); };
      media.getAudioTracks().forEach(track => pc.addTrack(track, media));
      const offer = await pc.createOffer();
      if (!active()) return;
      await pc.setLocalDescription(offer);
      if (!session.server_connect && !ephemeralCurrent(session.expires_at)) throw new Error('Sesi kedaluwarsa.');
      let sdp: string;
      if (session.server_connect) {
        const answer = await request<{ sdp: string }>('connect', { session_id: sessionId, sdp: offer.sdp }, controller.signal);
        if (typeof answer.sdp !== 'string' || !answer.sdp) throw new Error('Sambungan suara tidak valid.');
        sdp = answer.sdp;
      } else {
        let ephemeral = session.client_secret!; session.client_secret = '';
        const response = await fetch('https://api.openai.com/v1/realtime/calls', {
          method: 'POST', headers: { Authorization: `Bearer ${ephemeral}`, 'Content-Type': 'application/sdp' },
          body: offer.sdp, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]), credentials: 'omit', referrerPolicy: 'no-referrer',
        });
        ephemeral = '';
        if (!response.ok) throw new Error('Layanan suara belum tersedia.');
        sdp = await response.text();
      }
      if (!active()) return;
      await pc.setRemoteDescription({ type: 'answer', sdp });
      const connectionTimer = setTimeout(() => { if (active() && channel.readyState !== 'open') fail(new Error('Sambungan terputus.'), version); }, 20000);
      resources.add(() => clearTimeout(connectionTimer));
    } catch (error) { fail(error, version); }
  }

  async function simulate(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || busy.current || phase !== 'simulation') return;
    busy.current = true; setSending(true); setNotice('');
    const version = generation.current;
    const resources = new VoiceResources(); runtime.current = resources;
    const controller = new AbortController(); resources.add(() => controller.abort());
    const pending = pendingText.current?.text === value ? pendingText.current : { text: value, id: crypto.randomUUID() };
    pendingText.current = pending;
    try {
      const result = await request<Simulation>('simulate', { ...bodyScope(), ...(simulationSession.current ? { session_id: simulationSession.current } : {}), text: value, event_id: pending.id }, controller.signal);
      if (version !== generation.current) return;
      if (result.session_id) {
        simulationSession.current = result.session_id;
        const sessionId = result.session_id;
        closeServer.current = () => { void request('end', { session_id: sessionId }, new AbortController().signal).catch(() => {}); };
      }
      setEntries(result.items.map((item, index) => ({ id: item.id ?? `${pending.id}:${index}`, role: item.role === 'user' || item.direction === 'inbound' ? 'user' : 'assistant', text: item.text ?? item.body ?? '' })).filter(item => item.text) as Transcript[]);
      changed.current?.();
      setPipelineState(result.state ?? ''); setText(''); pendingText.current = null;
      setNotice('Simulasi teks — mikrofon tidak digunakan.');
      if (result.ended) { dispose(); setSending(false); setPhase('ended'); setNotice('Simulasi selesai.'); }
    } catch (error) {
      if (denied(error)) fail(error, version);
      else if (version === generation.current) setNotice('Simulasi belum tersedia. Teks tetap tersimpan di kotak pesan; coba lagi.');
    } finally {
      if (version === generation.current) { resources.close(); runtime.current = null; busy.current = false; setSending(false); }
    }
  }

  return <section className="voice-panel" aria-label="Talk to Warden">
    <div className="voice-heading"><div><h3>{publicToken ? 'Bicara dengan Warden' : 'Talk to Warden'}</h3><p>Laporan suara dan konfirmasi lapangan. Demo publik: maksimal 600 detik per sesi, 200 sesi per jam.</p></div><span className={`voice-badge voice-${phase}`}>{phase === 'simulation' ? 'Simulasi' : phase === 'live' ? 'Suara aktif' : phase === 'connecting' ? 'Menghubungkan' : phase === 'denied' ? 'Akses ditolak' : 'Siap'}</span></div>
    <p className="voice-privacy">Mikrofon aktif setelah Anda menekan Talk to Warden. Suara langsung diproses OpenAI dan transkrip disimpan. Laporan dicatat setelah konfirmasi YA; tindakan lapangan perlu persetujuan operator.</p>
    <div className="voice-actions">
      {phase !== 'live' && phase !== 'connecting' && <button type="button" disabled={phase === 'denied' || sending} onClick={() => void start()}>{publicToken ? 'Bicara dengan Warden' : 'Talk to Warden'}</button>}
      {(phase === 'live' || phase === 'connecting') && <>
        <button type="button" disabled={phase !== 'live'} aria-pressed={muted} onClick={() => { const next = !muted; mutedRef.current = next; stream.current?.getAudioTracks().forEach(track => { track.enabled = !next && !audioBusy.current; }); setMuted(next); }}>{muted ? 'Aktifkan mikrofon' : 'Bisukan mikrofon'}</button>
        <button type="button" className="voice-end" onClick={() => { dispose(); setMuted(false); setPhase('ended'); setNotice('Panggilan diakhiri.'); }}>{publicToken ? 'Tutup' : 'Akhiri'}</button>
      </>}
      {!publicToken && ['idle', 'ended'].includes(phase) && <button type="button" className="voice-secondary" onClick={() => { dispose(); setPhase('simulation'); setNotice('Simulasi teks — mikrofon tidak digunakan.'); }}>Buka Simulasi teks</button>}
      {!publicToken && phase === 'simulation' && <button type="button" className="voice-secondary" disabled={sending} onClick={() => { dispose(); setPhase('ended'); setNotice('Simulasi ditutup.'); }}>Tutup Simulasi</button>}
    </div>
    {notice && <p className="voice-notice" role="status">{notice}</p>}
    <audio ref={audio} hidden aria-hidden="true" />
    {!!entries.length && <ol className="voice-transcripts" aria-label="Transkrip percakapan">{entries.map(entry => <li key={`${entry.role}:${entry.id}`}><strong>{entry.role === 'user' ? 'Anda' : 'Warden'}</strong><p>{entry.text}</p></li>)}</ol>}
    {!publicToken && phase === 'simulation' && <form className="voice-form" onSubmit={event => void simulate(event)}>
      <label htmlFor={`voice-text-${fieldToken ? 'field' : responderId ?? 'operator'}`}>Pesan Simulasi untuk Warden</label>
      <textarea id={`voice-text-${fieldToken ? 'field' : responderId ?? 'operator'}`} value={text} maxLength={500} disabled={sending} onChange={event => setText(event.target.value)} placeholder="Tulis laporan lapangan, lalu konfirmasikan saat Warden meminta." rows={3} />
      <button disabled={sending || !text.trim()}>{sending ? 'Mengirim…' : 'Kirim Simulasi'}</button>
      {pipelineState === 'confirm' && <p className="voice-notice">Periksa bacaan ulang Warden sebelum mengirim konfirmasi.</p>}
    </form>}
  </section>;
}
