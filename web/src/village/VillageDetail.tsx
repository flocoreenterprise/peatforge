import { useEffect, useState } from 'react';
import { api } from '../api';
import './village.css';
import { criticalPresentation, type CriticalStatus } from './critical';
import { scenarioGainText, hydraulicAssumptionText, type ModelSetup } from './scenario';
import { createSimulationCache, simulationKey } from './simulationCache';

type Point = { date: string; wt_cm: number; pfvi: number };
type Status = CriticalStatus & { confidence: number; source: string; stale: boolean; data_as_of: string; assumptions: string[]; simulation_input_version?: string };
type Simulation = { days_bought: number | null; at_least_days: number | null; reason: string | null; wt_gain_cm: number; pfvi_reduction: number; baseline_critical_date: string | null; scenario_critical_date: string | null; curve_baseline: Point[]; curve_scenario: Point[]; assumptions: string[]; model_setup?: ModelSetup };
type History = { version: number; created_at: string; observation_id?: string | null; params: { provenance?: string; initial_wt_cm?: number }; old?: Status; new?: Status; observed_wt_cm?: number; applied_initial_wt_delta_cm?: number };
type Bundle = { status: Status; simulation: Simulation; canals: { id: string; name: string; source: string }[]; resources: { pumps: number; crews: number }; history: History[] };
const simulations = createSimulationCache<Simulation>();
const intervention = { type: 'canal_block', targets: ['K4'], delta_hc_cm: 60, days: 14 };
const number = (n: number | null, maximumFractionDigits = 1) => n === null ? 'belum tersedia' : new Intl.NumberFormat('id-ID', { maximumFractionDigits }).format(n);

export function benefitText(s: Simulation): string {
  if (s.reason === 'baseline_already_critical') return 'Kondisi awal sudah melewati ambang kritis; tidak ada tambahan hari sebelum kritis.';
  if (s.reason === 'baseline_not_critical_within_horizon') return 'Tanpa tindakan belum melewati ambang dalam 14 hari; tambahan hari belum dapat dihitung.';
  if (s.reason === 'scenario_beyond_horizon' && s.at_least_days !== null) return `Sekat diperkirakan memberi setidaknya ${number(s.at_least_days)} hari tambahan; batas akhirnya di luar simulasi.`;
  if (s.days_bought !== null && s.days_bought > 0) return `Sekat diperkirakan memberi ${number(s.days_bought)} hari tambahan sebelum ambang kritis.`;
  if (s.days_bought === 0) return 'Skenario sekat belum menunda ambang kritis dalam simulasi ini.';
  return 'Tambahan hari belum dapat dihitung.';
}

function CurveChart({ simulation, metric }: { simulation: Simulation; metric: 'wt_cm' | 'pfvi' }) {
  const baseline = simulation.curve_baseline;
  const scenario = simulation.curve_scenario;
  const threshold = metric === 'wt_cm' ? 60 : 250;
  const values = [...baseline, ...scenario].map(p => p[metric]);
  const max = Math.max(threshold * 1.15, ...values);
  const min = Math.min(0, ...values);
  const x = (i: number) => 44 + i / Math.max(1, baseline.length - 1) * 476;
  const y = (v: number) => 170 - (v - min) / (max - min) * 140;
  const line = (points: Point[]) => points.map((p, i) => `${x(i)},${y(p[metric])}`).join(' ');
  return <figure className="village-chart"><figcaption>{metric === 'wt_cm' ? 'Kedalaman air tanah modelled (cm, makin dalam makin kering)' : 'PFVI modelled (0–300)'}</figcaption>
    <svg viewBox="0 0 550 210" role="img" aria-label={`Kurva tanpa tindakan dan skenario sekat; ambang ${threshold}`}>
      <line x1="44" x2="520" y1={y(threshold)} y2={y(threshold)} className="village-threshold" />
      <text x="44" y={y(threshold) - 7}>Ambang {threshold}{metric === 'wt_cm' ? ' cm' : ''}</text>
      <polyline points={line(baseline)} className="village-baseline" /><polyline points={line(scenario)} className="village-scenario" />
      <text x="8" y="35">{number(max)}</text><text x="20" y="174">{number(min)}</text>
      <text x="44" y="198">{baseline[0]?.date}</text><text x="438" y="198">{baseline.at(-1)?.date}</text>
    </svg><div className="village-legend"><span>┄ Tanpa tindakan</span><span>━ Skenario sekat</span></div>
  </figure>;
}

export default function VillageDetail({ villageId, mode, day }: { villageId: string; mode: 'live' | 'replay'; day: number }) {
  const [data, setData] = useState<Bundle | null>(null);
  const [error, setError] = useState('');
  const [metric, setMetric] = useState<'wt_cm' | 'pfvi'>('wt_cm');
  useEffect(() => {
    let stopped = false;
    let busy = false;
    const controller = new AbortController();
    setData(null); setError('');
    const load = async () => {
      if (busy) return;
      busy = true;
      const base = `/villages/${encodeURIComponent(villageId)}`;
      const query = `mode=${mode}&day=${mode === 'live' ? 0 : day}`;
      try {
        const [status, canals, resources, history] = await Promise.all([
          api<Status>(`${base}/status?${query}`, { signal: controller.signal }),
          api<{ items: Bundle['canals'] }>(`${base}/canals`, { signal: controller.signal }),
          api<Bundle['resources']>(`${base}/resources`, { signal: controller.signal }),
          api<{ items: Bundle['history'] }>(`${base}/params/history`, { signal: controller.signal }),
        ]);
        if (stopped) return;
        const key = simulationKey({ villageId, mode, day, intervention,
          inputVersion: status.simulation_input_version,
          historyVersion: Math.max(0, ...history.items.map(item => item.version)) });
        const simulation = await simulations.get(key, () => api<Simulation>(`${base}/simulate`, {
          method: 'POST', body: JSON.stringify({ ...intervention, mode, day: mode === 'live' ? 0 : day }),
        }));
        if (!stopped) { setData({ status, simulation, canals: canals.items, resources, history: history.items }); setError(''); }
      } catch (e) { if (!stopped) setError(e instanceof Error ? e.message : 'Data belum tersedia'); }
      finally { busy = false; }
    };
    void load(); const timer = window.setInterval(() => void load(), 4000);
    return () => { stopped = true; controller.abort(); window.clearInterval(timer); };
  }, [villageId, mode, day]);
  if (!data) return <section className="village-detail" aria-live="polite"><h2>Detail desa</h2><p>{error || 'Memuat perkiraan desa…'}</p></section>;
  const { status: s, simulation: sim } = data;
  const critical = criticalPresentation(s);
  return <section className="village-detail">
    <header><span className="village-eyebrow">{mode === 'replay' ? `Putar ulang · hari ${day}` : 'Kondisi desa'} · Indeks rawan api {critical.pfviLevel}</span>{critical.wtCritical && <p className="village-warning" role="status"><strong>Air tanah melewati ambang model sekarang · kedalaman ≥60 cm</strong></p>}<h2>{critical.urgency}</h2><p className="village-benefit">{benefitText(sim)}</p></header>
    <p>Ambang kritis model: air tanah ≥60 cm atau indeks rawan api (PFVI) ≥250. Warna kerawanan memakai indeks saja.</p>
    <p className="village-model">Perkiraan model, belum diukur (modelled)</p>
    <p>{scenarioGainText(sim)}</p>
    <div className="village-stats"><div><small>Indeks rawan api · model</small><strong>{s.pfvi == null ? 'Belum ada data' : `${number(s.pfvi)} / 300`}</strong>{s.pfvi == null && <small>Indeks belum tersedia dari model.</small>}</div><div><small>Kedalaman air tanah · model</small><strong>{s.wt_cm == null ? 'Belum ada data' : `${number(s.wt_cm)} cm`}</strong>{s.wt_cm == null && <small>Kedalaman belum tersedia dari model.</small>}</div><div><small>Skor keyakinan model</small><strong>{number(s.confidence)}</strong></div></div>
    {error && <p role="status" className="village-warning">Pembaruan gagal: {error}. Angka terakhir tetap ditampilkan.</p>}
    <ul className="village-summary"><li>PFVI menunjukkan kerentanan kebakaran gambut, bukan konfirmasi api.</li><li>Skenario K4: kenaikan batas air kanal +60 cm diterapkan pada kedua ujung satu strip simetris. K4 + K5 tidak memberi manfaat tambahan secara aditif; ini bukan simulasi dua kanal independen atau tindakan lapangan. Model saat ini mendukung maksimal 14 hari.</li><li>Kanal dan sumber daya adalah fixture contoh, bukan hasil survei.</li><li>{mode === 'replay' ? 'Putar ulang memakai cuaca historis yang sudah diketahui; bukan ramalan di luar sampel.' : 'Cuaca publik menjadi masukan model; bukan pengukuran air tanah desa.'}</li></ul>
    <details className="pf-village-details"><summary>Lihat detail angka</summary>
      
      <p>Ambang kritis: air tanah ≥60 cm atau PFVI ≥250. Keyakinan bukan probabilitas terkalibrasi.</p>
      <p>{hydraulicAssumptionText(sim.model_setup)}</p>
      {sim.model_setup && <p>Susunan model dari API: {number(sim.model_setup.nodes)} node; konduktivitas {number(sim.model_setup.conductivity_m_day, 6)} m/hari; specific yield {number(sim.model_setup.specific_yield, 6)}; horizon {number(sim.model_setup.horizon_days)} hari. Batas pada kedua ujung; target mewakili satu strip simetris.</p>}
      <div className="village-chart-switch" role="group" aria-label="Angka pada grafik"><button type="button" aria-pressed={metric === 'wt_cm'} onClick={() => setMetric('wt_cm')}>Air tanah</button><button type="button" aria-pressed={metric === 'pfvi'} onClick={() => setMetric('pfvi')}>PFVI</button></div>
      <CurveChart simulation={sim} metric={metric} />
      <p>Pada akhir horizon: air tanah lebih dangkal {number(sim.wt_gain_cm)} cm; penurunan PFVI {number(sim.pfvi_reduction)}. Ini selisih model, bukan hasil terukur.</p>
      <p>Tanggal ambang tanpa tindakan: {sim.baseline_critical_date ?? 'tidak tercapai dalam horizon'}. Dengan sekat: {sim.scenario_critical_date ?? 'tidak tercapai dalam horizon'}.</p>
      <h3>Kanal dan sumber daya · fixture</h3><p>{data.canals.find(c => c.id === 'K4')?.name ?? 'K4'} · {data.resources.pumps} pompa · {data.resources.crews} regu. Lokasi dan kesiapan belum diverifikasi.</p>
      <h3>Sumber dan asumsi</h3><p>Sumber hasil: {s.source} · {s.stale ? 'cache kedaluwarsa' : 'tidak ditandai kedaluwarsa'} · data_as_of: {s.data_as_of}</p>
      <ul>{Array.from(new Set([...s.assumptions, ...sim.assumptions])).map(note => <li key={note}>{note}</li>)}</ul>
      <h3>Riwayat parameter</h3>
      {mode === 'replay' && <p>Riwayat pembaruan live; replay tidak diubah.</p>}
      {data.history.map(h => <div key={`${h.version}-${h.created_at}`}>
        <p>Versi {h.version} · {h.created_at}<br />{h.params.provenance}</p>
        {h.old && h.new && <>
          <p>Koreksi terbatas kondisi awal dari laporan terkonfirmasi; bukan kalibrasi K/Sy dan belum tervalidasi di lapangan.</p>
          <p>Air tanah dilaporkan: {h.observed_wt_cm === undefined ? 'belum tersedia' : `${number(h.observed_wt_cm)} cm`}. Koreksi kondisi awal diterapkan: {h.applied_initial_wt_delta_cm === undefined ? 'belum tersedia' : `${number(h.applied_initial_wt_delta_cm)} cm`}{h.params.initial_wt_cm === undefined ? '.' : `; kondisi awal menjadi ${number(h.params.initial_wt_cm)} cm.`}</p>
          <p>Model sebelum → sesudah: air tanah {number(h.old.wt_cm)} → {number(h.new.wt_cm)} cm; PFVI {number(h.old.pfvi)} → {number(h.new.pfvi)}; keyakinan heuristik {number(h.old.confidence)} → {number(h.new.confidence)}.</p>
        </>}
      </div>)}
    </details>
    <details className="village-glossary"><summary>Arti istilah</summary><dl><dt>PFVI</dt><dd>Indeks kerentanan kebakaran gambut dari model, antara 0 dan 300.</dd><dt>Kalibrasi</dt><dd>Penyesuaian parameter dengan data pembanding. Model ini belum dikalibrasi independen di lapangan.</dd><dt>Sekat kanal</dt><dd>Penghalang aliran kanal untuk menahan air; manfaat di sini hanya simulasi.</dd><dt>Otonomi</dt><dd>Tingkat kewenangan sistem. Tindakan lapangan membutuhkan persetujuan manusia.</dd></dl></details>
  </section>;
}

