import { useEffect, useState } from 'react';
import { api } from '../api';
import './national.css';
import { fireHeadline, classLabel, type FireOverview, type FireEvents } from '../map/fireEvents';

type Snapshot = {
  items: unknown[];
  by_province: Record<string, number>;
  total_matched: number;
  available?: boolean;
  regional_total: number;
  unmatched_regional_count: number;
  source: string;
  stale: boolean;
  data_as_of: string | null;
  partial: boolean;
  snapshot_id: string;
  coverage: { units: number; boundary_year: number };
};
const count = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? new Intl.NumberFormat('id-ID').format(value) : 'Belum tersedia';
const date = (value: string | null) => !value || Number.isNaN(Date.parse(value)) ? 'Waktu data belum tersedia' : new Date(value).toLocaleString('id-ID', { timeZone: 'Asia/Jakarta' }) + ' WIB';

export default function NationalView() {
  const [days, setDays] = useState(1);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [overview, setOverview] = useState<FireOverview | null>(null);
  const [events, setEvents] = useState<FireEvents | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let stopped = false; let busy = false;
    setSnapshot(null); setOverview(null); setEvents(null); setError(''); setLoading(true); setExpanded(false);
    async function load() {
      if (busy) return; busy = true;
      try {
        const results = await Promise.allSettled([api<Snapshot>(`/national/hotspots?days=${days}`, { signal: controller.signal }), api<FireOverview>(`/national/overview?days=${days}`, { signal: controller.signal }), api<FireEvents>(`/fire-events?days=${days}`, { signal: controller.signal })]);
        if (!stopped) {
          const [raw, classified, details] = results;
          if (raw.status === 'fulfilled') { setSnapshot(raw.value); setError(''); } else setError('Data deteksi panas nasional belum dapat diperbarui.');
          setOverview(classified.status === 'fulfilled' ? classified.value : null);
          setEvents(details.status === 'fulfilled' ? details.value : null);
        }
      } catch { if (!stopped) setError('Data deteksi panas nasional belum dapat diperbarui. Layanan atau sumber data belum tersedia.'); }
      finally { busy = false; if (!stopped) setLoading(false); }
    }
    void load(); const timer = window.setInterval(() => void load(), 4000);
    return () => { stopped = true; controller.abort(); window.clearInterval(timer); };
  }, [days]);
  const headline = fireHeadline(overview);
  const provinces = Object.entries(snapshot?.available === false ? {} : snapshot?.by_province ?? {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'id'));
  return <section className="pf-national" aria-label="Deteksi panas per provinsi">
    <header><div><span className="pf-national-eyebrow">Seluruh Indonesia · deteksi satelit</span><h1>Deteksi panas per provinsi</h1><p>Deteksi panas, bukan api terkonfirmasi.</p></div>
      <label>Rentang data<select value={days} onChange={event => setDays(Number(event.target.value))}>{[1, 2, 3, 4, 5, 6, 7].map(value => <option key={value} value={value}>{value} hari terakhir</option>)}</select></label>
    </header>
    {error && <p className="pf-national-notice" role="status">{error}{snapshot ? ' Data terakhir tetap ditampilkan dan mungkin belum mutakhir.' : ''}</p>}
    {!snapshot ? <p role="status">{loading ? 'Memuat deteksi panas…' : 'Belum ada data yang dapat ditampilkan.'}</p> : <>
      <div className="pf-national-stats"><div><small>Deteksi yang cocok dengan batas Indonesia</small><strong>{snapshot.available === false ? 'Belum tersedia' : count(snapshot.total_matched)}</strong></div><div><small>Rentang pengambilan data</small><strong>{days} hari terakhir</strong></div></div>
      {headline ? <><p className="pf-national-notice"><strong>Klasifikasi kandidat · belum terkonfirmasi</strong><br/>{headline}</p><p className="pf-national-notice"><strong>{overview?.headline.uncertain_event_count == null ? 'Jumlah kejadian belum pasti belum tersedia.' : `${count(overview.headline.uncertain_event_count)} kejadian belum pasti; tidak dimasukkan sebagai kebakaran lahan.`}</strong></p></> : <p>Klasifikasi kejadian belum tersedia; angka deteksi mentah tetap ditampilkan.</p>}
      <details><summary>Kenapa klasifikasi ini?</summary><p>Kandidat berdasarkan aturan/model, bukan konfirmasi api. Deteksi satelit dikelompokkan sebagai kejadian; panas industri/gunung api dipisahkan hanya jika bukti tersedia. Bukti vegetasi, gambut atau OSM yang tidak tersedia tetap tidak pasti.</p>
        {(overview?.partial || events?.partial || snapshot.partial) && <p>Cakupan sebagian; jumlah bukan total lengkap seluruh Indonesia.</p>}
        {events?.stale && <p>Klasifikasi memakai cache yang belum mutakhir.</p>}
        {overview?.assumptions?.map((reason, index) => <p key={`overview-${index}`}>{reason}</p>)}
        {events?.assumptions?.map((reason, index) => <p key={`events-${index}`}>{reason}</p>)}
        {events?.items?.slice(0, 12).map(event => <p key={event.id}><strong>{classLabel(event.classification)} · belum terkonfirmasi</strong>: {event.reasons.join(' · ')}. Sumber: {event.sources.map(source => source.name).join(' · ') || 'Belum tersedia'}.</p>)}
        {events && events.items.length > 12 && <p>Menampilkan alasan untuk 12 kejadian pertama.</p>}
        {!events && <p>Rincian alasan dan bukti belum tersedia.</p>}
        <p>Sumber deteksi: {snapshot.source}. Waktu klasifikasi: {date(overview?.data_as_of ?? null)}. Tidak ada PFVI nasional pada tampilan ini.</p>
      </details>
      <p>Angka menghitung deteksi satelit, bukan jumlah kejadian kebakaran unik. Lokasi yang sama dapat terdeteksi lebih dari sekali.</p>
      {snapshot.partial && <p className="pf-national-notice">Cakupan data sebagian; angka belum menggambarkan seluruh deteksi dalam rentang ini.</p>}
      {snapshot.stale && <p className="pf-national-notice">Data cache belum mutakhir. Gunakan waktu data di bawah saat membaca angka.</p>}
      <section className="pf-national-provinces" aria-label="Urutan jumlah deteksi per provinsi"><h2>{expanded ? 'Seluruh provinsi dalam data' : 'Provinsi dengan deteksi terbanyak'}</h2>
        {provinces.length ? <ol>{(expanded ? provinces : provinces.slice(0, 6)).map(([province, detections]) => <li key={province}><span>{province}</span><strong>{count(detections)} <small>deteksi</small></strong></li>)}</ol> : <p>Belum ada rincian provinsi dalam data ini. Ketiadaan deteksi bukan bukti wilayah aman.</p>}
        {provinces.length > 6 && <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? 'Tampilkan enam teratas' : 'Lihat semua provinsi'}</button>}
      </section>
      <details><summary>Cakupan dan sumber data</summary><p>Batas administratif yang digunakan: {count(snapshot.coverage?.units)} provinsi, tahun {snapshot.coverage?.boundary_year ?? 'belum tersedia'}. Batas lama ini tidak mewakili pembagian provinsi terkini.</p>
        <p>Deteksi dalam wilayah pengambilan regional: {count(snapshot.regional_total)}. Deteksi regional yang tidak cocok dengan batas Indonesia: {count(snapshot.unmatched_regional_count)}; angka tersebut bukan jumlah kebakaran Indonesia.</p>
        <p>Sumber deteksi: {snapshot.source}. Waktu data: {date(snapshot.data_as_of)}.</p>
        <p>Batas wilayah: <a href="https://www.geoboundaries.org/countryDownloads.html" target="_blank" rel="noopener noreferrer">geoBoundaries / OpenStreetMap · ODbL 1.0</a>.</p>
        <p>Tampilan ini merangkum deteksi panas; tidak menyediakan PFVI nasional atau konfirmasi kebakaran.</p>
      </details>
    </>}
  </section>;
}
