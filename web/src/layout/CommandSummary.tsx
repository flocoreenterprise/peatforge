import { useEffect, useState } from 'react';
import { api, type Village } from '../api';
import { number, dateLabel, type Risk, type Hotspots } from '../map/pilot';
import type { OsmCanals } from '../map/canals';
import { hotspotSourceLabel } from '../map/canals';
import { summaryFacts, type SummaryAction } from './summary';
import { sourceStamp } from './sourceStamp';

export default function CommandSummary({ enabled, mode, day, selectedDate, villages, risk, hotspots, osmCanals, synced, notice = '' }: { enabled: boolean; mode: 'live' | 'replay'; day: number; selectedDate?: string | null; villages: Village[]; risk: Risk | null; hotspots: Hotspots | null; osmCanals: OsmCanals | null; synced: string | null; notice?: string }) {
  const [actions, setActions] = useState<SummaryAction[] | null>(null);
  const [actionError, setActionError] = useState(false);
  useEffect(() => {
    setActions(null); setActionError(false);
    if (!enabled) return;
    const controller = new AbortController(); let active = true, busy = false;
    async function load() {
      if (busy) return; busy = true;
      try {
        const query = new URLSearchParams({ mode, day: String(mode === 'live' ? 0 : day) });
        const data = await api<{ items: SummaryAction[] }>(`/actions?${query}`, { signal: controller.signal });
        if (active) { setActions(data.items); setActionError(false); }
      } catch { if (active) setActionError(true); }
      finally { busy = false; }
    }
    void load(); const timer = setInterval(() => void load(), 4000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [enabled, mode, day]);
  const facts = summaryFacts(villages.map(village => village.id), risk, actions, mode, day);
  const hotspotCount = hotspots && hotspots.available !== false ? hotspots.items.length : null;
  const missing = 'Belum ada data';
  const bestVillage = villages.find(village => village.id === facts.best?.village_id)?.name;
  const sources = [...new Set(risk?.features.map(feature => feature.properties.source).filter(Boolean) ?? [])];
  const modelAssumptions = [...new Set(risk?.features.flatMap(feature => feature.properties.assumptions ?? []) ?? [])];
  const modelStale = risk?.features.some(feature => feature.properties.stale);
  const modelStatuses = risk?.features.map(feature => feature.properties) ?? [];
  const oldestStatus = modelStatuses.filter(status => status.data_as_of && Number.isFinite(Date.parse(status.data_as_of))).sort((a, b) => Date.parse(a.data_as_of!) - Date.parse(b.data_as_of!))[0];
  const modelStamp = sourceStamp(oldestStatus ?? modelStatuses[0] ?? { available: false, reason: 'snapshot model belum tersedia' }, { mode, historicalDate: selectedDate });
  const hotspotStamp = sourceStamp(hotspots ?? { available: false, reason: 'snapshot deteksi belum tersedia' }, { mode, historicalDate: selectedDate });
  const allLow = mode === 'live' && villages.length > 0 && facts.unknown === 0 && villages.every(village => { const status = risk?.features.find(feature => feature.properties.village_id === village.id)?.properties; return status?.level === 'RENDAH' && !status.critical_now && (status.wt_cm ?? Infinity) < 60 && (status.pfvi ?? Infinity) < 250; });
  const hasBmkgCoordinates = villages.some(village => 'provider' in village && village.provider === 'BMKG');
  const notes = {
    pending: actionError ? 'Pembaruan gagal; data terakhir mungkin kedaluwarsa.' : facts.pending === null ? 'Daftar usulan belum tersedia.' : 'Semua desa pada waktu yang dipilih.',
    critical: facts.critical === null ? 'Kondisi desa belum tersedia.' : `${facts.unknown ? `${number(facts.unknown)} desa belum lengkap. ` : ''}Air tanah ≥60 cm atau indeks rawan api ≥250.`,
    hotspots: hotspotCount === null ? hotspots?.reason || 'Sumber deteksi belum tersedia.' : 'Sekitar desa pilot · bukan api terkonfirmasi.',
    benefit: facts.best ? `${bestVillage ?? 'Desa pada usulan'} · tambahan waktu menuju ambang model, bukan hasil lapangan.` : 'Belum ada usulan dengan hasil hari yang terhitung.',
  };
  return <section className="command-summary" aria-label="Ringkasan wilayah">
    {notice && <div className="command-environment-banner" role="status">{notice}</div>}
    <p className="command-data-sentence"><span className="command-summary-time"><strong>{mode === 'replay' ? `Putar ulang 2019 · ${dateLabel(selectedDate)} · hari ${day}. ` : `Live · ${dateLabel(risk?.features[0]?.properties.data_as_of)}. `}</strong></span>{facts.critical === null ? 'Belum ada data kondisi desa untuk waktu ini.' : facts.critical > 0 ? `${number(facts.critical)} desa melewati ambang kritis model.` : facts.unknown ? 'Sebagian kondisi desa belum dapat dinilai.' : allLow ? 'Risiko rendah, pemantauan tetap berjalan.' : 'Belum ada desa melewati ambang kritis pada hasil model ini.'} {facts.unknown > 0 && `${number(facts.unknown)} desa belum memiliki data lengkap.`} {facts.pending !== null && `${number(facts.pending)} usulan menunggu keputusan.`} <span>Perkiraan model, belum diukur.</span></p>
    <p><small>{modelStamp}</small></p>
    <div className="command-metrics">
      <div className="command-metric"><span>Menunggu keputusan</span><strong>{facts.pending === null ? missing : number(facts.pending)}</strong><small className="command-metric-note">{notes.pending}</small></div>
      <div className="command-metric"><span>Desa melewati ambang model</span><strong>{facts.critical === null ? missing : number(facts.critical)}</strong><small className="command-metric-note">{notes.critical}</small></div>
      <div className="command-metric"><span>Deteksi panas satelit</span><strong>{hotspotCount === null ? missing : number(hotspotCount)}</strong><small className="command-metric-note">{notes.hotspots}</small></div>
      <div className="command-metric"><span>Manfaat usulan terbaik</span><strong>{facts.best ? `${number(facts.best.days_bought)} hari` : missing}</strong><small className="command-metric-note">{notes.benefit}</small></div>
    </div>
    <details className="command-source-disclosure"><summary>Sumber &amp; asumsi</summary>
      <div className="command-source-content">
        <ul><li>Menunggu keputusan: {notes.pending}</li><li>Desa melewati ambang model: {notes.critical}</li><li>Deteksi panas satelit: {notes.hotspots}</li><li>Manfaat usulan terbaik: {notes.benefit}</li></ul>
        <p><strong>{mode === 'replay' ? 'Putar ulang cuaca historis 2019' : 'Kondisi terkini'}</strong> · {synced ? `layar disinkronkan ${synced} WIB, bukan waktu sumber data` : 'Belum ada sinkronisasi layar'}. {mode === 'replay' ? 'Cuaca historis yang sudah diketahui dipakai sebagai prakiraan sempurna; bukan ramalan di luar sampel.' : 'Cuaca publik menjadi masukan model; bukan pengukuran air tanah desa.'}</p>
        <p>Indeks rawan api (PFVI) menggambarkan kerentanan kebakaran gambut, bukan konfirmasi api. Data yang belum tersedia tidak berarti aman. Sumber hasil model: {sources.length ? sources.join(' · ') : `${missing} — snapshot risiko belum tersedia`}{modelStale ? ' · cache kedaluwarsa' : ''}.</p>
        {!!modelAssumptions.length && <ul>{modelAssumptions.map(assumption => <li key={assumption}>{assumption}</li>)}</ul>}
        <p>{hotspotSourceLabel(hotspots, mode)}. {hotspotStamp}. Deteksi sekitar desa memakai jendela waktu yang dipilih layanan; warna menandai usia deteksi.</p>
        {hasBmkgCoordinates && <p>Koordinat administrasi desa: BMKG · titik referensi, bukan batas desa atau sumber pengukuran cuaca di sini.</p>}
        <p><strong>Kanal: OpenStreetMap, bukan survei.</strong> {osmCanals?.available ? 'Geometri kanal saat ini, bukan arsip 2019.' : `Belum ada data — ${osmCanals?.reason || 'geometri OpenStreetMap belum tersedia.'}`} <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors · ODbL</a>. Lapisan ini tidak mengganti geometri simulasi atau parameter model.</p>
        <p>Manfaat usulan terbaik membandingkan tambahan hari yang sudah dihitung engine pada usulan menunggu keputusan, bukan semua kemungkinan tindakan. {facts.hasLowerBounds && 'Sebagian usulan hanya memiliki batas bawah karena melewati horizon; lihat keputusan untuk rinciannya.'} Persetujuan belum berarti tindakan telah dilaksanakan.</p>
        <p>Cuaca historis memakai ERA5, data analisis ulang cuaca. Peta dasar: Esri World Imagery atau OpenStreetMap, sesuai pilihan lapisan. Asumsi model dan kanal tersedia di detail desa.</p>
      </div>
    </details>
  </section>;
}
