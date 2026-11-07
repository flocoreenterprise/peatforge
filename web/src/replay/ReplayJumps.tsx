import { useEffect, useState } from 'react';
import { api } from '../api';
import { criticalMilestone, milestoneChoices, type Milestones } from './milestones';
import './replay.css';

const requests = new Map<string, Promise<Milestones>>();
function loadMilestones(villageId: string) {
  const existing = requests.get(villageId);
  if (existing) return existing;
  const request = api<Milestones>(`/replay/milestones?village_id=${encodeURIComponent(villageId)}`).catch(error => {
    requests.delete(villageId);
    throw error;
  });
  requests.set(villageId, request);
  if (requests.size > 32) requests.delete(requests.keys().next().value!);
  return request;
}

export default function ReplayJumps({ villageId, onJump, compact = false }: { villageId: string; onJump: (day: number) => void; compact?: boolean }) {
  const [result, setResult] = useState<Milestones | null>(null);
  const [error, setError] = useState('');
  const [selectedKind, setSelectedKind] = useState('wt_60');
  useEffect(() => {
    let stopped = false;
    setResult(null); setError(''); setSelectedKind('wt_60');
    void loadMilestones(villageId).then(data => {
      if (!stopped) {
        if (data.village_id !== villageId) setError('Desa pada data ambang tidak sesuai.');
        else setResult(data);
      }
    }).catch(error => {
      if (!stopped) setError(error instanceof Error ? error.message : 'Data ambang belum tersedia.');
    });
    return () => { stopped = true; };
  }, [villageId]);
  const current = result?.village_id === villageId ? result : null;
  const choices = milestoneChoices(current?.items ?? []);
  const selected = choices.find(choice => choice.kind === selectedKind)!;
  const critical = criticalMilestone(current?.items ?? []);
  const extra = <>
    <p>Lompat ke saat ambang pada model historis, bukan kondisi live.</p>
    {!current && <p role="status">{error || 'Memuat tanggal ambang historis…'}</p>}
    <div className="replay-jump-controls"><select aria-label="Ambang replay tujuan" disabled={!current}
      value={selectedKind} onChange={event => setSelectedKind(event.target.value)}>
      {choices.map(choice => <option key={choice.kind} value={choice.kind} disabled={choice.day === null}>
        {choice.label}{choice.day === null ? ` · ${choice.unavailable}` : ` · hari ${choice.day}`}
      </option>)}</select><button type="button" disabled={selected.day === null}
        onClick={() => { if (selected.day !== null) onJump(selected.day); }}>Lompat</button></div>
    {current && <p>{selected.day === null ? selected.unavailable : `${selected.date} · hari ${selected.day}`}</p>}
    {current && <details><summary>Sumber ambang</summary><p>Sumber: {current.source} · {current.stale ? 'cache kedaluwarsa' : 'tidak ditandai kedaluwarsa'} · data_as_of: {current.data_as_of ?? 'belum tersedia'}</p></details>}
  </>;
  if (compact) return <div className="replay-jumps replay-jumps-compact" aria-label="Lompat ke ambang replay">
    <button type="button" disabled={!current || critical.day === null} title={critical.day === null ? critical.unavailable : `Air tanah ≥60 cm atau indeks rawan api ≥250 · ${critical.date} · hari ${critical.day}`} onClick={() => { if (current && critical.day !== null) onJump(critical.day); }}>Lompat ke hari kritis</button>
    <details><summary>Ambang lain</summary><div className="replay-jump-extra">{extra}</div></details>
    {(!current && error || current && critical.day === null) && <span className="replay-primary-reason" role="status">{error || critical.unavailable}</span>}
  </div>;
  return <div className="replay-jumps" aria-label="Lompat ke ambang replay">{extra}</div>;
}
