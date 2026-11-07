import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type Config } from '../api';
import './inbox.css';
import { validFieldPath } from './fieldLink';
import { deliveryLabel } from './delivery';
import { evidenceText } from './evidence';
import VoicePanel from '../voice/VoicePanel';
type ChannelConfig = Config & { outbound?: string; voice_mode?: string };

export type InboxProps = { villageId?: string; mode: 'live' | 'replay'; day: number; onShowLive?: () => void };
type Action = { id: string; village_id: string; title: string; reason: string; kind: string; status: string; priority?: string; source?: string; mode: string; day: number; responder_id: string; anonymous: boolean; days_bought: number | null; at_least_days: number | null; simulation_reason?: string; benefit: { wt_gain_cm: number | null; pfvi_reduction: number | null }; verifier: { decision: string; confidence_label?: string; checks: { name: string; passed: boolean | null; reason?: string; confidence_label?: string; qualitative?: boolean }[] }; assumptions?: string[]; followup_status?: string };
type Responder = { id: string; name: string; village_id: string };
type Message = { id: string; direction: string; body: string; status: string; channel: string; created_at: string };
type Progress = { state: string; mode: string; day: number; steps: { id: string; label: string; done: boolean }[] };
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Data belum tersedia';
export function benefitText(action: Action): string {
  if (action.days_bought !== null && action.days_bought > 0) return `+${action.days_bought} hari menuju ambang kritis model`;
  if (action.at_least_days !== null && action.at_least_days > 0) return `Setidaknya +${action.at_least_days} hari; melewati horizon model`;
  if (action.days_bought === 0) return action.simulation_reason === 'baseline_already_critical' ? 'Ambang kritis sudah tercapai; tambahan hari tidak diklaim' : 'Tidak ada penundaan ambang kritis model; tambahan hari tidak diklaim';
  const parts = [action.benefit.wt_gain_cm !== null ? `Air lebih dangkal ${action.benefit.wt_gain_cm} cm` : '', action.benefit.pfvi_reduction !== null ? `PFVI turun ${action.benefit.pfvi_reduction}` : ''].filter(Boolean);
  return parts.length ? parts.join(' · ') + ' · tambahan hari belum tersedia' : 'Tambahan hari belum tersedia';
}
export default function DecisionInbox({ villageId, mode, day, onShowLive }: InboxProps) {
  const context = useRef(''); context.current = villageId ?? '';
  const [items, setItems] = useState<Action[]>([]), [hidden, setHidden] = useState(0), [error, setError] = useState(''), [loaded, setLoaded] = useState(false);
  const [confirm, setConfirm] = useState<{ id: string; operation: 'approve' | 'reject' } | null>(null), [reason, setReason] = useState(''), [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    const requestedVillage = villageId ?? '';
    try { const result = await api<{ items: Action[]; hidden_count: number }>(`/actions${villageId ? `?village_id=${encodeURIComponent(villageId)}` : ''}`); if (context.current !== requestedVillage) return; setItems(result.items); setHidden(result.hidden_count); setError(''); setLoaded(true); } catch (e) { setError(errorText(e)); }
  }, [villageId]);
  useEffect(() => { setItems([]); setLoaded(false); void refresh(); const timer = setInterval(() => void refresh(), 4000); return () => clearInterval(timer); }, [refresh]);
  useEffect(() => { setConfirm(null); setReason(''); }, [villageId, mode, day]);
  async function transition() {
    if (!confirm) return; setBusy(true);
    try { await api(`/actions/${encodeURIComponent(confirm.id)}/${confirm.operation}`, { method: 'POST', body: JSON.stringify(confirm.operation === 'reject' ? { reason: reason.trim() } : {}) }); setConfirm(null); setReason(''); await refresh(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  const visible = items.filter(item => item.mode === mode && (mode === 'live' || item.day === day));
  const hasPublicLive = mode === 'replay' && items.some(item => item.source === 'public' && item.mode === 'live' && item.status === 'proposed');
  return <section className="pf-inbox" aria-label="Kotak keputusan"><header><span className="pf-inbox-kicker">KENDALI OPERATOR</span><h3>Keputusan lapangan</h3><p>Usulan memerlukan persetujuan Anda. Disetujui belum berarti dilaksanakan.</p></header>
    {error && <p className="pf-inbox-error" role="alert">{error} Data terakhir mungkin kedaluwarsa.</p>}
    {hasPublicLive && <div className="pf-inbox-public-notice" role="status"><p>Ada laporan publik Live yang menunggu keputusan. Laporan ini tidak dicampur dengan putar ulang.</p>{onShowLive && <button type="button" onClick={onShowLive}>Buka laporan publik Live</button>}</div>}
    {!loaded && !error && <p>Memuat keputusan…</p>}{loaded && !visible.length && <p className="pf-inbox-empty">Belum ada usulan untuk konteks ini. Laporan terkonfirmasi akan ditinjau Planner.</p>}
    {visible.map(action => <article className="pf-inbox-card" key={action.id}>{action.priority === 'URGENT' && <strong className="pf-inbox-urgent">URGENT · Perlu perhatian segera</strong>}{action.source === 'public' && <strong className="pf-inbox-public-source">Laporan publik</strong>}<div className="pf-inbox-cardtop"><span>{action.kind === 'verify' ? 'Perlu dicek' : 'Usulan tindakan'}</span><span>{({ proposed: 'Menunggu persetujuan', approved: 'Disetujui · belum dilaksanakan', rejected: 'Ditolak', done: 'Selesai' } as Record<string, string>)[action.status] ?? action.status}</span></div><h4>{action.title}</h4><p>{action.reason}</p>
      {evidenceText(action.verifier) && <p className="pf-inbox-evidence">{evidenceText(action.verifier)}</p>}
      {action.kind !== 'verify' && <p className="pf-inbox-impact">{benefitText(action)}</p>}<small>{action.anonymous ? 'Pelapor anonim' : `Pelapor: ${action.responder_id}`}</small>
      {(action.status === 'approved' || action.status === 'done') && <p className="pf-inbox-muted">Tindak lanjut: {deliveryLabel(action.followup_status)}. Persetujuan tidak memastikan pesan diterima.</p>}
      <details><summary>Kenapa usulan ini?</summary><p>Verifier: {action.verifier.decision === 'forward' ? 'Kuat' : action.verifier.decision === 'hold' ? 'Lemah · perlu pemeriksaan' : 'Cukup · ada catatan'}</p><ul>{action.verifier.checks.map((check, index) => <li key={`${check.name}-${index}`}>{({ confirmed: 'Laporan dikonfirmasi', water_table_discrepancy: 'Kesesuaian laporan dengan model', hotspots_2km_48h: 'Deteksi panas sekitar laporan', exact_tool_benefit: 'Manfaat sesuai hasil engine' } as Record<string, string>)[check.name] ?? check.name}: {check.passed === null ? 'data tidak tersedia' : check.passed ? 'lolos' : 'perlu perhatian'}{check.reason ? ` · ${check.reason}` : ''}</li>)}</ul>{action.simulation_reason && <p>Alasan model: {action.simulation_reason}</p>}{action.assumptions?.map(text => <p key={text}>{text}</p>)}<p>Perkiraan model, belum diukur. Deteksi panas bukan api terkonfirmasi.</p></details>
      {action.status === 'proposed' && <div className="pf-inbox-buttons"><button disabled={busy || !!error} onClick={() => { setConfirm({ id: action.id, operation: 'approve' }); setReason(''); }}>{action.kind === 'verify' ? 'Setujui pemeriksaan' : 'Setujui'}</button><button disabled={busy || !!error} onClick={() => { setConfirm({ id: action.id, operation: 'reject' }); setReason(''); }}>Tolak</button></div>}
      {confirm?.id === action.id && <div className="pf-inbox-confirm" role="group" aria-label="Konfirmasi keputusan"><p>{confirm.operation === 'approve' ? 'Setujui usulan dan proses tindak lanjut kepada pelapor? Status pengiriman akan ditampilkan terpisah. Pelaksanaan tetap belum dikonfirmasi.' : 'Tolak usulan ini? Catat alasannya.'}</p>{confirm.operation === 'reject' && <label>Alasan penolakan<textarea maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label>}<div className="pf-inbox-buttons"><button disabled={busy || (confirm.operation === 'reject' && !reason.trim())} onClick={() => void transition()}>{busy ? 'Menyimpan…' : 'Konfirmasi'}</button><button disabled={busy} onClick={() => setConfirm(null)}>Batal</button></div></div>}
    </article>)}{hidden > 0 && <p className="pf-inbox-muted">{hidden} usulan lama disembunyikan (kedaluwarsa/digantikan).</p>}
  </section>;
}

export function SmsLoop({ villageId, mode, day }: InboxProps) {
  const threadContext = useRef('');
  const [responders, setResponders] = useState<Responder[]>([]), [selected, setSelected] = useState(''), [messages, setMessages] = useState<Message[]>([]), [progress, setProgress] = useState<Progress | null>(null), [config, setConfig] = useState<ChannelConfig | null>(null);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [text, setText] = useState('AIR 61 ASAP YA dekat kanal 4'), [notice, setNotice] = useState('');
  useEffect(() => { let active = true; api<{ items: Responder[] }>(`/responders${villageId ? `?village_id=${encodeURIComponent(villageId)}` : ''}`).then(result => { if (!active) return; setResponders(result.items); setSelected(result.items.find(item => item.id === 'r1')?.id ?? result.items[0]?.id ?? ''); }).catch(e => { if (active) setError(errorText(e)); }); api<ChannelConfig>('/config').then(result => { if (active) setConfig(result); }).catch(e => { if (active) setError(errorText(e)); }); return () => { active = false; }; }, [villageId]);
  threadContext.current = selected;
  const refresh = useCallback(async () => { if (!selected) return; try { const [thread, state] = await Promise.all([api<{ items: Message[] }>(`/responders/${encodeURIComponent(selected)}/messages`), config?.public_demo === true ? api<Progress>(`/demo/progress?responder_id=${encodeURIComponent(selected)}`) : Promise.resolve(null)]); if (threadContext.current !== selected) return; setMessages(thread.items); setProgress(state); setError(''); } catch (e) { setError(errorText(e)); } }, [selected, config?.public_demo]);
  useEffect(() => { setMessages([]); setProgress(null); void refresh(); const timer = setInterval(() => void refresh(), 4000); return () => clearInterval(timer); }, [refresh]);
  async function send(alert = false, input = text) {
    const responder = responders.find(item => item.id === selected); if (!responder) return; setBusy(true); setNotice('');
    try { if (alert) { const result = await api<{ notified: number; suppressed: number }>('/events/risk-changed', { method: 'POST', body: JSON.stringify({ village_id: responder.village_id, mode, day: mode === 'live' ? 0 : day, ...((mode === 'replay' || config?.mode === 'mock') ? { hour_override: 10 } : {}) }) }); setNotice(`Pemicu diproses: ${result.notified} diberi notifikasi, ${result.suppressed} ditahan.`); } else { await api('/demo/inbound', { method: 'POST', body: JSON.stringify({ responder_id: selected, text: input, mode, day: mode === 'live' ? 0 : day }) }); setText(''); } await refresh(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  const demo = config?.public_demo === true;
  const enabled = demo && !!selected;
  return <section className="pf-inbox pf-sms" aria-label={demo ? "Simulator SMS operator" : "Percakapan responder"}><header><span className="pf-inbox-kicker">{demo ? "Simulated � KANAL SMS" : "KANAL RESPONDER"}</span><h3>{demo ? "Alur SMS lapangan" : "Percakapan responder"}</h3><p>{demo ? "Simulasi operator · tidak mengirim SMS nyata. AIR 61 adalah contoh laporan demo yang diketik operator." : "Pesan tersimpan dan status pengiriman dari kanal responder."}</p></header><label>Responden<select value={selected} onChange={e => setSelected(e.target.value)}>{responders.map(item => <option key={item.id} value={item.id}>{item.name} · {item.id}</option>)}</select></label>{!responders.length && <p>Responden belum tersedia.</p>}
    <FieldShare key={selected} responderId={selected} />
    {selected && config?.browser_voice_supported && <VoicePanel responderId={selected} mode={mode} day={mode === 'live' ? 0 : day}/>}
    {demo && <button className="pf-inbox-alert" disabled={!enabled || busy} onClick={() => void send(true)}>1. Picu peringatan model</button>}{notice && <p role="status">{notice}</p>}
    {error && <p className="pf-inbox-error" role="alert">{error}</p>}
    {demo && progress && <><ol className="pf-inbox-steps">{progress.steps.map(step => <li key={step.id} className={step.done ? 'pf-inbox-stepdone' : ''}><span>{step.done ? '✓' : '○'}</span>{step.label}</li>)}</ol><p className="pf-inbox-muted">Status tersimpan: {progress.state} · {progress.mode} / hari {progress.day}</p>{(progress.mode !== mode || (mode === 'replay' && progress.day !== day)) && <p>Progres berasal dari konteks sebelumnya. Picu peringatan untuk konteks saat ini.</p>}</>}
    <div className="pf-inbox-thread" role="log" aria-label="Pesan tersimpan">{messages.map(message => <div className={`pf-inbox-message ${message.direction === 'inbound' ? 'pf-inbox-inbound' : ''}`} key={message.id}><small>{message.direction === 'inbound' ? 'Responden' : 'Warden'} · {message.channel} · {deliveryLabel(message.status)}</small><p>{message.body}</p></div>)}{!messages.length && <p>Belum ada pesan tersimpan.</p>}</div>
    {demo && <><button disabled={!enabled || busy} onClick={() => void send(false, 'AIR 61 ASAP YA dekat kanal 4')}>2. Balasan Simulated � AIR 61 ASAP YA</button><form onSubmit={e => { e.preventDefault(); if (text.trim()) void send(); }}><label>Laporan contoh / balasan<textarea value={text} maxLength={500} onChange={e => setText(e.target.value)} placeholder="AIR 61 ASAP YA dekat kanal 4" /></label><div className="pf-inbox-buttons"><button disabled={!enabled || busy || !text.trim()}>Kirim laporan</button><button type="button" disabled={!enabled || busy || progress?.state !== 'confirm' || progress?.mode !== mode || progress?.day !== (mode === 'live' ? 0 : day)} onClick={() => void send(false, 'YA')}>YA · konfirmasi baca ulang</button></div></form><p className="pf-inbox-muted">Baca ulang pesan Warden sebelum YA tersendiri. Setelah usulan muncul, setujui melalui kotak keputusan; tindak lanjut akan muncul di utas ini.</p></>}
    {config && !demo && <ProtectedChannels key={selected} responderId={selected} responderName={responders.find(item => item.id === selected)?.name ?? 'responden terpilih'} config={config} onSent={refresh} />}
  </section>;
}




function FieldShare({ responderId }: { responderId: string }) {
  const [path, setPath] = useState(''), [busy, setBusy] = useState(false), [status, setStatus] = useState('');
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function load() {
    setBusy(true); setStatus(''); setPath('');
    try {
      const result = await api<{ field_path: unknown }>(`/responders/${encodeURIComponent(responderId)}/field-link`);
      if (!mounted.current) return;
      if (!validFieldPath(result.field_path)) { setStatus('Link responder belum tersedia.'); return; }
      setPath(result.field_path);
    } catch { if (mounted.current) setStatus('Link responder belum tersedia.'); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function copy() {
    if (!validFieldPath(path)) return;
    try { await navigator.clipboard.writeText(window.location.origin + path); if (mounted.current) setStatus('Link responder disalin.'); }
    catch { if (mounted.current) setStatus('Link belum dapat disalin. Gunakan tombol buka halaman responder.'); }
  }
  return <div><div className="pf-inbox-buttons"><button type="button" disabled={!responderId || busy} onClick={() => void load()}>{busy ? 'Memuat link…' : 'Bagikan link responder'}</button>{validFieldPath(path) && <><a href={path} target="_blank" rel="noreferrer" referrerPolicy="no-referrer">Buka halaman responder</a><button type="button" onClick={() => void copy()}>Salin link</button></>}</div>{status && <p role="status" className="pf-inbox-muted">{status}</p>}</div>;
}

function ProtectedChannels({ responderId, responderName, config, onSent }: { responderId: string; responderName: string; config: ChannelConfig; onSent: () => Promise<void> }) {
  const [text, setText] = useState(''), [busy, setBusy] = useState(false), [confirmCall, setConfirmCall] = useState(false), [notice, setNotice] = useState('');
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const callAvailable = config.voice_available && !!config.outbound && config.outbound !== 'mock';
  async function submit(kind: 'sms' | 'call') {
    if (!responderId || busy || (kind === 'call' && !callAvailable)) return;
    setBusy(true); setNotice('');
    try {
      const result = kind === 'sms'
        ? await api<{ status?: string }>(`/responders/${encodeURIComponent(responderId)}/send`, { method: 'POST', body: JSON.stringify({ text: text.trim(), channel: 'sms' }) })
        : await api<{ status?: string }>('/voice/twilio/call', { method: 'POST', body: JSON.stringify({ responder_id: responderId, diagnostic: false, uat: true }) });
      if (!alive.current) return;
      setNotice(`${kind === 'sms' ? 'Pesan operator' : 'Panggilan'}: ${deliveryLabel(result.status)}.`);
      if (kind === 'sms' && ['mock', 'queued', 'sent', 'delivered', 'pending', 'dispatching'].includes(result.status ?? '')) setText('');
      setConfirmCall(false);
      await onSent();
    } catch { if (alive.current) setNotice('Permintaan belum dapat dipastikan. Periksa status percakapan sebelum mencoba lagi.'); }
    finally { if (alive.current) setBusy(false); }
  }
  return <div><form onSubmit={event => { event.preventDefault(); if (text.trim()) void submit('sms'); }}><label>Pesan operator<textarea maxLength={300} value={text} onChange={event => setText(event.target.value)} placeholder="Tulis pesan untuk responder" /></label><div className="pf-inbox-buttons"><button disabled={!responderId || busy || !text.trim()}>Kirim SMS operator</button>{callAvailable && <button type="button" disabled={!responderId || busy} onClick={() => setConfirmCall(true)}>Uji telepon Warden</button>}</div></form>
    {confirmCall && <div className="pf-inbox-confirm" role="group" aria-label="Konfirmasi panggilan"><p>Uji coba telepon Warden kepada {responderName}, penerima uji yang dipilih dan telah memberikan persetujuan? Ini panggilan uji coba, bukan peringatan darurat. Batas keras satu panggilan per hari, persetujuan penerima, dan kebijakan jam tenang tetap diperiksa server.</p><div className="pf-inbox-buttons"><button disabled={busy} onClick={() => void submit('call')}>Konfirmasi uji telepon</button><button disabled={busy} onClick={() => setConfirmCall(false)}>Batal</button></div></div>}
    {notice && <p role="status">{notice}</p>}
  </div>;
}


