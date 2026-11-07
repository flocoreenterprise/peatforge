import { useEffect, useState } from 'react';
import { api, type Village } from '../api';

export type Mode = 'live' | 'replay';
export type Level = 'RENDAH' | 'SEDANG' | 'TINGGI' | 'EKSTREM';
export type Status = { village_id: string; name?: string; level: Level; pfvi: number | null; wt_cm: number | null; days_to_critical: number | null; critical_date: string | null; source: string; wt_source: string; stale: boolean; data_as_of: string | null; assumptions: string[]; confidence: number; mode: Mode; day: number };
export type Risk = { type: 'FeatureCollection'; features: { type: 'Feature'; geometry: { type: 'Point'; coordinates: [number, number] }; properties: Status }[] };
export type Hotspots = { items: { id: string; lat: number; lon: number; detected_at: string; source: string }[]; available?: boolean; stale: boolean; data_as_of: string | null; source?: string; reason?: string };
export type ReplayMeta = { start: string; end: string; days: number; perfect_forecast: boolean };
export const number = (value: number | null | undefined) => value == null ? '—' : new Intl.NumberFormat('id-ID', { maximumFractionDigits: 1 }).format(value);
export function dateLabel(value: string | null | undefined) { if (!value) return 'Belum tersedia'; const date = new Date(value); return Number.isNaN(date.getTime()) ? 'Belum tersedia' : new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Jakarta' }).format(date); }
export function replayDate(meta: ReplayMeta | null, day: number) { if (!meta) return null; const date = new Date(`${meta.start}T00:00:00Z`); date.setUTCDate(date.getUTCDate() + day); return date.toISOString().slice(0, 10); }
export function usePilot(enabled: boolean, mode: Mode, day: number) {
  const [villages, setVillages] = useState<Village[]>([]);
  const [risk, setRisk] = useState<Risk | null>(null);
  const [hotspots, setHotspots] = useState<Hotspots | null>(null);
  const [meta, setMeta] = useState<ReplayMeta | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [synced, setSynced] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) { setVillages([]); setRisk(null); setHotspots(null); return; }
    let active = true; let inFlight = false;
    const controller = new AbortController();
    setRisk(null); setHotspots(null); setLoading(true);
    const poll = async () => {
      if (inFlight) return; inFlight = true;
      const query = `mode=${mode}&day=${mode === 'live' ? 0 : day}`;
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(12000)]);
      const results = await Promise.allSettled([
        api<{ items: Village[] }>('/villages', { signal }),
        api<Risk>(`/map/risk?${query}`, { signal }),
        api<Hotspots>(`/hotspots?hours=72&radius_km=5&${query}`, { signal }),
        api<ReplayMeta>('/replay/meta', { signal }),
      ]);
      inFlight = false; if (!active) return;
      const [v, r, h, m] = results;
      if (v.status === 'fulfilled') setVillages(v.value.items);
      if (r.status === 'fulfilled') setRisk(r.value); else setRisk(null);
      if (h.status === 'fulfilled') setHotspots(h.value); else setHotspots({ items: [], available: false, stale: false, data_as_of: null, reason: 'Layanan titik panas belum tersedia.' });
      if (m.status === 'fulfilled') setMeta(m.value);
      setError(v.status === 'rejected' || r.status === 'rejected' ? 'Data risiko belum tersedia · mencoba kembali' : '');
      setLoading(false);
      if (v.status === 'fulfilled' && r.status === 'fulfilled') setSynced(new Intl.DateTimeFormat('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Asia/Jakarta' }).format(new Date()));
    };
    void poll(); const timer = window.setInterval(() => void poll(), 4000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [enabled, mode, day]);
  return { villages, risk, hotspots, meta, error, loading, synced };
}

