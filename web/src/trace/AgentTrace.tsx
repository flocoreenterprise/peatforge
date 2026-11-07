import { useEffect, useState } from 'react';
import { api } from '../api';
import './trace.css';

type Props = { villageId?: string; mode: 'live' | 'replay'; day: number; actionId?: string };
type Action = { id: string; title: string; status: string; trace_id?: string | null; responder_id?: string; anonymous?: boolean; mode?: string; day?: number };
type AgentEvent = { id: string; agent: string; event: string; input: unknown; output: unknown; tool_calls: unknown[]; trace_id: string; created_at: string };
type Message = { id: string; body: string; direction: string; channel: string; status: string; trace_id?: string; created_at: string };
const stages = ['Scout', 'Warden', 'Verifier', 'Planner', 'Operator'];
const descriptions: Record<string, string> = { Scout: 'Pemilihan petugas dan batas otonomi', Warden: 'Percakapan dan konfirmasi laporan', Verifier: 'Pemeriksaan bukti laporan', Planner: 'Penyusunan usulan berdasarkan alat', Operator: 'Keputusan manusia atas usulan' };
const labels: Record<string, string> = { proposed: 'Menunggu keputusan', approved: 'Disetujui; belum dilaksanakan', rejected: 'Ditolak', done: 'Pelaksanaan dikonfirmasi', expired: 'Kedaluwarsa', superseded: 'Digantikan' };
const time = (value: string) => Number.isNaN(Date.parse(value)) ? 'Waktu tidak tersedia' : new Date(value).toLocaleString('id-ID', { timeZone: 'Asia/Jakarta' }) + ' WIB';

// Trace payloads are server-sanitized. Avoid rendering arbitrary nested objects or contact fields.
function facts(value: unknown): string[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  const allowed = ['decision', 'status', 'mode', 'day', 'wt_cm', 'pfvi', 'days_bought', 'at_least_days', 'wt_gain_cm', 'pfvi_reduction', 'confirmed', 'available', 'passed', 'difference_cm', 'limit_cm'];
  return Object.entries(value).filter(([key, item]) => allowed.includes(key) && (item === null || ['number', 'boolean'].includes(typeof item) || typeof item === 'string' && /^[a-z_]+$/i.test(item)))
    .map(([key, item]) => `${key}: ${item === null ? 'belum tersedia' : String(item)}`);
}
function EventDetails({ event }: { event: AgentEvent }) {
  return <details className="pf-trace-event"><summary>{event.event} · {time(event.created_at)}</summary>
    <p>Catatan tersimpan dari {event.agent}.</p>
    {facts(event.input).length > 0 && <p>Masukan · {facts(event.input).join(' · ')}</p>}
    {facts(event.output).length > 0 && <p>Hasil · {facts(event.output).join(' · ')}</p>}
    {event.tool_calls?.map((tool, index) => {
      const item = tool && typeof tool === 'object' ? tool as Record<string, unknown> : {};
      const name = typeof item.name === 'string' && /^[a-z_]+$/i.test(item.name) ? item.name : 'Panggilan alat';
      return <p key={index}><code>{name}</code>{[...facts(item.arguments), ...facts(item.result ?? item.output)].map(fact => <span key={fact}> · {fact}</span>)}</p>;
    })}
  </details>;
}

export default function AgentTrace({ villageId, mode, day, actionId }: Props) {
  const [actions, setActions] = useState<Action[]>([]);
  const [selection, setSelection] = useState('');
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [error, setError] = useState('');
  const [traceError, setTraceError] = useState('');
  const [transcriptError, setTranscriptError] = useState('');
  const [loading, setLoading] = useState(true);
  const selected = actions.find(action => action.id === (actionId || selection)) ?? (!actionId ? actions[0] : undefined);

  useEffect(() => {
    const controller = new AbortController(); let stopped = false; let busy = false;
    setActions([]); setSelection(''); setError(''); setLoading(true);
    async function load() {
      if (busy) return; busy = true;
      try {
        const query = new URLSearchParams({ mode, day: String(mode === 'live' ? 0 : day) });
        if (villageId) query.set('village_id', villageId);
        const result = await api<{ items: Action[] }>(`/actions?${query}`, { signal: controller.signal });
        if (!stopped) { setActions(result.items.filter(action => (!action.mode || action.mode === mode) && (mode !== 'replay' || action.day === undefined || action.day === day))); setError(''); }
      } catch { if (!stopped) setError('Daftar usulan belum dapat diperbarui.'); }
      finally { busy = false; if (!stopped) setLoading(false); }
    }
    void load(); const timer = window.setInterval(() => void load(), 4000);
    return () => { stopped = true; controller.abort(); window.clearInterval(timer); };
  }, [villageId, mode, day]);

  useEffect(() => {
    const controller = new AbortController(); let stopped = false; let busy = false;
    setEvents([]); setMessages([]); setTraceError(''); setTranscriptError('');
    if (!selected?.trace_id) return () => controller.abort();
    const traceId = selected.trace_id;
    const responderId = selected.anonymous ? undefined : selected.responder_id;
    async function load() {
      if (busy) return; busy = true;
      await Promise.allSettled([
        api<{ items: AgentEvent[] }>(`/trace?${new URLSearchParams({ trace_id: traceId })}`, { signal: controller.signal }).then(result => {
          if (!stopped) { setEvents(result.items.filter(event => event.trace_id === traceId).sort((a, b) => a.created_at.localeCompare(b.created_at))); setTraceError(''); }
        }).catch(() => { if (!stopped) setTraceError('Jejak belum tersedia dari server. Catatan terakhir mungkin belum mutakhir.'); }),
        responderId ? api<{ items: Message[] }>(`/responders/${encodeURIComponent(responderId)}/messages`, { signal: controller.signal }).then(result => {
          if (!stopped) { setMessages(result.items.filter(message => message.trace_id === traceId).sort((a, b) => a.created_at.localeCompare(b.created_at))); setTranscriptError(''); }
        }).catch(() => { if (!stopped) setTranscriptError('Transkrip belum tersedia dari server.'); }) : Promise.resolve(),
      ]);
      busy = false;
    }
    void load(); const timer = window.setInterval(() => void load(), 4000);
    return () => { stopped = true; controller.abort(); window.clearInterval(timer); };
  }, [selected?.id, selected?.trace_id, selected?.responder_id, selected?.anonymous, mode, day]);

  return <section className="pf-trace" aria-label="Jejak agen">
    <h2>Jejak agen</h2><p>Ikuti bukti yang tersimpan, dari laporan hingga keputusan operator.</p>
    {error && <p role="status">{error}</p>}
    {actions.length > 0 && <label>Usulan <select value={selected?.id ?? ''} onChange={event => setSelection(event.target.value)} disabled={Boolean(actionId)}>
      {actionId && !selected && <option value="">Usulan tidak tersedia</option>}
      {actions.map(action => <option value={action.id} key={action.id}>{action.title} · {labels[action.status] ?? action.status}</option>)}
    </select></label>}
    {!selected ? <p>{loading ? 'Memuat usulan…' : actionId ? 'Usulan yang dipilih belum tersedia untuk desa dan waktu ini.' : 'Belum ada usulan untuk desa dan waktu ini.'}</p> : <>
      <p><strong>{selected.title}</strong> · {labels[selected.status] ?? selected.status}</p>
      {!selected.trace_id && <p>Usulan ini belum memiliki jejak tersimpan.</p>}
      {traceError && <p role="status">{traceError}</p>}
      <ol className="pf-trace-stages">{stages.map(stage => {
        const records = events.filter(event => event.agent.toLowerCase() === stage.toLowerCase());
        return <li key={stage}><strong>{stage}</strong><p>{descriptions[stage]}</p><small>{records.length ? 'Ada peristiwa tersimpan; lihat urutan waktu di bawah.' : 'Belum ada peristiwa tersimpan.'}</small></li>;
      })}</ol>
      <h3>Urutan peristiwa tersimpan</h3>
      {events.length ? events.map(event => <EventDetails key={event.id} event={event} />) : <p>Belum ada peristiwa tersimpan untuk jejak ini.</p>}
      <details><summary>Transkrip percakapan</summary><p>Pesan asli yang terhubung dengan jejak usulan ini. Pengiriman mock bukan pengiriman SMS nyata.</p>
        {transcriptError && <p role="status">{transcriptError}</p>}
        {messages.length ? <ol className="pf-trace-messages">{messages.map(message => <li key={message.id}><small>{message.direction === 'inbound' ? 'Petugas' : 'Warden'} · {message.channel} · {message.status} · {time(message.created_at)}</small><p>{message.body}</p></li>)}</ol> : <p>Belum ada transkrip yang tersedia melalui akses operator.</p>}
      </details>
    </>}
  </section>;
}
