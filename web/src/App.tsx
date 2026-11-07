import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ArrowRight, Check, ChevronRight, CircleHelp, Fingerprint, Leaf, LockKeyhole, LogOut, Menu, Moon, Radio, RefreshCw, ShieldCheck, Sun, Trees, Waves, X, Inbox, MapPin, Phone, Workflow } from 'lucide-react';
import { api, ApiError, mockConfig, type Config } from './api';
import MapCanvas from './map/MapCanvas';
import { usePilot, dateLabel, replayDate, number, type Mode } from './map/pilot';

type Tab = 'Keputusan' | 'Desa' | 'Telepon-SMS';
const tabs = [{ name: 'Keputusan', icon: Inbox }, { name: 'Desa', icon: MapPin }, { name: 'Telepon-SMS', icon: Phone }] as const;

function Brand() { return <div className="brand"><span className="brand-icon"><Waves size={23}/></span><span>peatforge<span className="brand-sub">COMMAND</span></span></div>; }

export default function App() {
  const [dark, setDark] = useState(() => localStorage.getItem('peatforge-theme') === 'dark');
  const [phase, setPhase] = useState<'loading' | 'login' | 'command'>('loading');
  const [config, setConfig] = useState<Config | null>(null);
  const [preview, setPreview] = useState(false);
  const [error, setError] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>('Desa');
  const [mode, setMode] = useState<Mode>('live');
  const [day, setDay] = useState(9);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectVillage = useCallback((id: string) => { setSelectedId(id); setTab('Desa'); }, []);
  const [drawer, setDrawer] = useState(false);
  const pilot = usePilot(phase === 'command' && !preview, mode, day);
  const { villages, risk, hotspots, meta } = pilot;
  const selected = villages.find(v => v.id === selectedId) ?? villages[0];
  const selectedStatus = risk?.features.find(f => f.properties.village_id === selected?.id)?.properties;
  const selectedDate = replayDate(meta, day);
  const [connection, setConnection] = useState('');


  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; localStorage.setItem('peatforge-theme', dark ? 'dark' : 'light'); }, [dark]);
  const initialize = useCallback(async () => {
    setError(''); setPhase('loading');
    try {
      const next = await api<Config>('/config'); setConfig(next); setPreview(false); setPhase('command');
    } catch (failure) {
      if (failure instanceof ApiError && (failure.status === 401 || failure.status === 403)) { setPhase('login'); return; }
      // Only unavailable services activate the labelled, read-only shell preview.
      try {
        const next = await api<Config>('/config'); setConfig(next); setPreview(false); setPhase('command');
      } catch (configFailure) {
        if (configFailure instanceof ApiError && (configFailure.status === 401 || configFailure.status === 403)) { setPhase('login'); setError('Masuk dengan PIN operator untuk melanjutkan.'); return; }
        setConfig(mockConfig); setPreview(true); setPhase('command');
      }
    }
  }, []);
  useEffect(() => { void initialize(); }, [initialize]);
  useEffect(() => {
    const unauthorized = () => { setPhase('login'); setConfig(null); setPreview(false); setPin(''); setError('Sesi berakhir. Masuk kembali dengan PIN operator.'); };
    window.addEventListener('peatforge:unauthorized', unauthorized);
    return () => window.removeEventListener('peatforge:unauthorized', unauthorized);
  }, []);


  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await api('/auth/login', { method: 'POST', body: JSON.stringify({ pin }) }); setPin(''); await initialize(); }
    catch (failure) { setError(failure instanceof ApiError ? failure.message : 'Layanan belum tersedia. Coba lagi sebentar.'); setPin(''); }
    finally { setBusy(false); }
  }
  async function logout() {
    setBusy(true);
    try { await api('/auth/logout', { method: 'POST' }); setPhase('login'); setConfig(null); setPreview(false); }
    catch { setConnection('Keluar belum berhasil. Coba lagi.'); }
    finally { setBusy(false); }
  }
  const themeButton = <button className="icon-button" onClick={() => setDark(!dark)} aria-label={dark ? 'Aktifkan mode terang' : 'Aktifkan mode malam'}>{dark ? <Sun size={19}/> : <Moon size={19}/>}</button>;
  if (phase === 'loading') return <div className="loading-screen"><Brand/><span className="loading-line"/><p>Menghubungkan pusat komando…</p></div>;
  if (phase === 'login') return <main className="login-screen">
    <section className="login-story"><Brand/><div className="story-content"><span className="eyebrow">MENJAGA GAMBUT. MENJAGA KEHIDUPAN.</span><h1>Waktu untuk<br/>bertindak.<br/><em>Sebelum api.</em></h1><p>Dari perkiraan air tanah hingga kabar warga.<br/>Satu tempat untuk keputusan yang berarti.</p><div className="story-line"><Trees size={20}/><span>Pilot Pulang Pisau · Kalimantan Tengah</span></div></div><span className="story-footer">PEATLAND INTELLIGENCE / HUMAN DECISIONS</span></section>
    <section className="login-form-side"><div className="login-top">{themeButton}<span>AKSES OPERATOR</span></div><form onSubmit={login} className="login-form"><div className="login-lock"><Fingerprint size={30}/></div><span className="eyebrow">SELAMAT DATANG KEMBALI</span><h2>Masuk ke Command</h2><p>Gunakan PIN operator untuk membuka wilayah pantauan dan kotak keputusan.</p><label htmlFor="pin">PIN operator</label><input id="pin" type="password" inputMode="numeric" pattern="[0-9]{6}" autoComplete="off" maxLength={6} minLength={6} required value={pin} onChange={e => setPin(e.target.value.replace(/\D/g, ''))} placeholder="••••••" aria-describedby={error ? 'login-error' : 'pin-help'}/><span id="pin-help" className="input-help">Enam digit dari pengelola sistem.</span>{error && <div id="login-error" className="form-error" role="alert">{error}</div>}<button className="primary-button" disabled={busy || pin.length !== 6}>{busy ? 'Memeriksa…' : 'Buka pusat komando'}<ArrowRight size={18}/></button><div className="secure-note"><LockKeyhole size={14}/><span>Sesi terlindungi. Tindakan lapangan tetap memerlukan persetujuan manusia.</span></div></form><footer>Peatforge Command<span>Operator console · v0.1</span></footer></section>
  </main>;

  return <main className="command-screen">
    <header className="topbar"><Brand/><div className="header-divider"/><span className="workspace-label">Pusat komando</span><div className="topbar-right"><span className="connection-indicator"><i className={connection || preview ? 'offline' : ''}/>{preview ? 'Pratinjau lokal' : connection ? 'Terputus' : 'Terhubung'}</span>{themeButton}{preview ? <button className="text-button" aria-label="Coba hubungkan" onClick={() => void initialize()}><RefreshCw size={16}/><span>Coba hubungkan</span></button> : <button className="icon-button" aria-label="Keluar dari Command" disabled={busy} onClick={() => void logout()}><LogOut size={18}/></button>}</div></header>
    <section className="command-body">
      <MapCanvas dark={dark} villages={villages} risk={risk} hotspots={hotspots} selectedId={selected?.id ?? null} onSelect={selectVillage}/>
      <section className="replay-control glass-panel" aria-label="Waktu pantauan"><div className="mode-switch"><button className={mode === 'live' ? 'active' : ''} onClick={() => setMode('live')}>Live</button><button className={mode === 'replay' ? 'active' : ''} onClick={() => setMode('replay')}>Replay 2019</button></div>{mode === 'replay' && <div className="replay-slider"><label htmlFor="replay-day">{dateLabel(selectedDate)} · hari {day}</label><input id="replay-day" aria-label="Hari replay" type="range" min={0} max={(meta?.days ?? 62) - 1} value={day} onChange={event => setDay(Number(event.target.value))}/><span>ERA5 · prakiraan sempurna historis</span></div>}</section>
      <div className="workspace-toolbar"><div className="location-selector"><MapPin size={16}/><span>Wilayah pilot</span></div><span className="live-badge"><Radio size={13}/>{preview ? 'Pratinjau mock' : config?.public_demo ? 'Demo terbuka · kanal simulasi' : config?.mode === 'mock' ? 'Kanal simulasi' : 'LIVE'}</span><button className="drawer-toggle" onClick={() => setDrawer(!drawer)} aria-expanded={drawer} aria-label="Buka panel informasi">{drawer ? <X size={18}/> : <Menu size={18}/>}</button></div>
      {(preview || pilot.error || config?.mode === 'mock' || connection) && <div className="environment-banner" role="status">{connection || pilot.error || (preview ? 'Pratinjau mock — API belum tersedia. Tidak ada data operasional atau pengiriman pesan.' : 'Kanal komunikasi dalam mode simulasi. Sumber data lingkungan ditandai pada tiap hasil.')}</div>}
      <aside className="operations-panel glass-panel">
        <div className="panel-topline"><span className="eyebrow">MEJA OPERASI</span><span className="panel-counter">01 / PILOT</span></div>
        <h1>Gambut terpantau.<br/><span>Keputusan di tangan Anda.</span></h1>
        <p className="panel-intro">Pantau kondisi desa, dengarkan laporan warga, lalu tentukan langkah berikutnya.</p>
        <div className="three-stats"><div><strong>{villages.length || '—'}</strong><span>Desa terhubung</span></div><div><strong>{risk ? risk.features.filter(f => ['TINGGI', 'EKSTREM'].includes(f.properties.level)).length : '—'}</strong><span>Risiko tinggi+</span></div><div><strong>{hotspots && hotspots.available !== false ? hotspots.items.length : '—'}</strong><span>Titik panas 72j</span></div></div>
        <nav className="panel-tabs" aria-label="Panel operasi">{tabs.map(({ name, icon: Icon }) => <button key={name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}><Icon size={16}/><span>{name}</span></button>)}</nav>
        <div className="panel-content">
          {tab === 'Keputusan' && <><div className="section-heading"><h2>Kotak keputusan</h2><span>Persetujuan manusia</span></div><div className="empty-state"><div className="empty-icon"><ShieldCheck size={27}/></div><h3>Siap menerima usulan</h3><p>Usulan tindakan dan laporan yang perlu diperiksa akan muncul di sini setelah terhubung.</p><span className="waiting-label"><span/>MENUNGGU DATA OPERASIONAL</span></div><div className="approval-note"><LockKeyhole size={15}/><p>Setiap tindakan lapangan memerlukan persetujuan operator.</p></div></>}
          {tab === 'Desa' && <><div className="section-heading"><h2>Wilayah pantauan</h2><span>{pilot.loading ? 'Memuat risiko…' : `${villages.length} desa`}</span></div>{villages.length ? <div className="village-list">{villages.map(village => { const status = risk?.features.find(f => f.properties.village_id === village.id)?.properties; return <button className={`village-row ${village.id === selected?.id ? 'selected' : ''}`} key={village.id} onClick={() => selectVillage(village.id)}><span className="village-icon"><Trees size={18}/></span><div><strong>{village.name}</strong><span>{status ? <><i className={`status-dot ${status.level.toLowerCase()}`}/>{status.level} · {status.stale ? 'cache lama' : status.source === 'fixture' ? 'contoh' : 'model'}</> : 'Risiko belum tersedia'}</span></div><ChevronRight size={16}/></button>; })}</div> : <Empty icon="village" title="Menunggu data desa" text="Lokasi dan kondisi desa tersedia setelah layanan terhubung."/>}{selected && <section className="selected-village"><span className="eyebrow">DESA TERPILIH</span><h3>{selected.name}</h3><p>{selectedStatus ? `${selected.name} berada pada tingkat ${selectedStatus.level.toLowerCase()}. ${selectedStatus.days_to_critical == null ? 'Tidak ada perlintasan batas kritis dalam horizon model.' : selectedStatus.days_to_critical === 0 ? 'Batas kritis model sudah tercapai.' : `Batas kritis model diperkirakan dalam ${number(selectedStatus.days_to_critical)} hari.`}` : 'Risiko belum tersedia. Kondisi belum dapat disimpulkan.'}</p><div className="village-numbers"><span>Indeks rawan api<strong>{number(selectedStatus?.pfvi)}<small> / 300</small></strong></span><span>Air tanah model<strong>{number(selectedStatus?.wt_cm)}<small> cm</small></strong></span></div><p className="source-line">Air tanah: perkiraan model, belum diukur.</p><p className="source-line">Lokasi: {selected.source === 'provider' ? 'BMKG' : selected.source} · titik referensi, bukan batas desa.</p><p className="source-line">Risiko: {selectedStatus?.source ?? 'belum tersedia'}{selectedStatus?.stale ? ' · cache lama' : ''} · data per {dateLabel(selectedStatus?.data_as_of)}</p></section>}</>}
          {tab === 'Telepon-SMS' && <><div className="section-heading"><h2>Kabar dari lapangan</h2><span>SMS & telepon</span></div><Empty icon="phone" title="Dekat dengan warga" text="Percakapan dan laporan responder akan tersedia di panel ini."/><div className="approval-note"><Radio size={15}/><p>Responder menggunakan SMS, telepon, atau link web. Tanpa memasang aplikasi.</p></div></>}

        </div>
        <div className="panel-footer"><span><i/>{pilot.synced ? `Sinkron ${pilot.synced} WIB` : 'Belum ada pembaruan'}</span><span>Polling 4 dtk</span></div>
      </aside>
      <aside className={`context-panel glass-panel ${drawer ? 'drawer-open' : ''}`}><div className="context-heading"><span className="eyebrow">CARA MEMBACA PETA</span><Leaf size={18}/></div><h2>Kenali risikonya.<br/>Periksa buktinya.</h2><p>Warna risiko menunjukkan indeks rawan api. Data yang belum tersedia tidak berarti kondisi rendah risiko.</p><div className="risk-legend">{['Rendah', 'Sedang', 'Tinggi', 'Ekstrem'].map((level, i) => <div key={level}><i className={`risk-${i}`}/><span>{level}</span></div>)}</div><div className="model-note"><CircleHelp size={16}/><p>Air tanah: <strong>perkiraan model, belum diukur.</strong> Titik panas adalah deteksi satelit, bukan api terkonfirmasi.</p></div><div className="hotspot-note"><strong>Titik panas · radius 5 km dari desa pilot</strong><p>{hotspots?.available === false ? mode === 'replay' ? 'Arsip titik panas tidak tersedia. Data live tidak diputar ulang.' : 'Data titik panas belum tersedia.' : hotspots ? `${number(hotspots.items.length)} deteksi dalam 72 jam · ${hotspots.source ?? 'NOAA-20/21'}${hotspots.stale ? ' · cache lama' : ''}` : 'Memuat deteksi satelit…'}</p><span>Data per {dateLabel(hotspots?.data_as_of)}</span><p>Oranye: &lt;24 jam · pudar: 24–72 jam.</p></div><div className="context-bottom"><Check size={14}/><span>Manusia memegang keputusan akhir</span></div></aside>
      <div className="map-bottom-note"><span>WILAYAH PILOT</span><span>Titik desa BMKG · tanpa batas atau geometri kanal</span></div>
    </section>
  </main>;
}

function Empty({ icon, title, text }: { icon: 'village' | 'phone' | 'trace'; title: string; text: string }) {
  const Icon = icon === 'village' ? Trees : icon === 'phone' ? Phone : Workflow;
  return <div className="empty-state"><div className="empty-icon"><Icon size={27}/></div><h3>{title}</h3><p>{text}</p></div>;
}



