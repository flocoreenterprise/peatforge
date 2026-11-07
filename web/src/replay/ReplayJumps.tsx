import { useEffect, useState } from 'react';
import { api } from '../api';
import { milestoneChoices, type Milestones } from './milestones';
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

export default function ReplayJumps({ villageId, onJump }: { villageId: string; onJump: (day: number) => void }) {
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
  return <div className="replay-jumps" aria-label="Lompat ke ambang replay">
    <p>Ambang model historis · ERA5 prakiraan sempurna; bukan live.</p>
    {!current && <p role="status">{error || 'Memuat tanggal ambang historis…'}</p>}
    <div className="replay-jump-controls"><select aria-label="Ambang replay tujuan" disabled={!current}
      value={selectedKind} onChange={event => setSelectedKind(event.target.value)}>
      {choices.map(choice => <option key={choice.kind} value={choice.kind} disabled={choice.day === null}>
        {choice.label}{choice.day === null ? ` · ${choice.unavailable}` : ` · hari ${choice.day}`}
      </option>)}</select><button type="button" disabled={selected.day === null}
        onClick={() => { if (selected.day !== null) onJump(selected.day); }}>Lompat</button></div>
    {current && <p>{selected.day === null ? selected.unavailable : `${selected.date} · hari ${selected.day}`}</p>}
    {current && <details><summary>Sumber ambang</summary><p>Sumber: {current.source} · {current.stale ? 'cache kedaluwarsa' : 'tidak ditandai kedaluwarsa'} · data_as_of: {current.data_as_of ?? 'belum tersedia'}</p></details>}
  </div>;
}
