import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ArrowRight, Fingerprint, LockKeyhole, LogOut, Moon, RefreshCw, Sun, Trees, Waves, MapPin, Phone, Workflow } from 'lucide-react';
import { api, ApiError, mockConfig, type Config } from './api';
import MapCanvas from './map/MapCanvas';
import type { OsmCanals } from './map/canals';
import DecisionInbox, { SmsLoop } from './inbox/DecisionInbox';
import VillageDetail from './village/VillageDetail';
import AgentTrace from './trace/AgentTrace';
import FieldInbox from './field/FieldInbox';
import NationalView from './national/NationalView';
import ReplayJumps from './replay/ReplayJumps';
import { usePilot, dateLabel, replayDate, type Mode } from './map/pilot';
import CommandSummary from './layout/CommandSummary';
import './layout/command.css';
import PublicReportPage from './public/PublicReportPage';
import CommandPublicLink from './public/CommandPublicLink';

type Tab = 'Desa' | 'Percakapan' | 'Jejak';
const tabs = [{ name: 'Desa', icon: MapPin }, { name: 'Percakapan', icon: Phone }, { name: 'Jejak', icon: Workflow }] as const;

function Brand() { return <div className="brand"><span className="brand-icon"><Waves size={23}/></span><span>peatforge<span className="brand-sub">COMMAND</span></span></div>; }

export default function App() {
  if (window.location.pathname.replace(/\/$/, '') === '/lapor') return <PublicReportPage/>;
  const field = /^\/f\/([^/]+)\/?$/.exec(window.location.pathname);
  if (field) return <FieldInbox token={field[1]}/>;
  return <CommandApp/>;
}

function CommandApp() {
  const [dark, setDark] = useState(() => localStorage.getItem('peatforge-theme') === 'dark');
  const [phase, setPhase] = useState<'loading' | 'login' | 'command'>('loading');
  const [config, setConfig] = useState<Config | null>(null);
  const [preview, setPreview] = useState(false);
  const [error, setError] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>('Desa');
  const [scope, setScope] = useState<'pilot' | 'national'>('pilot');
  const [scoutError, setScoutError] = useState('');
  const [mode, setMode] = useState<Mode>('replay');
  const [day, setDay] = useState(9);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectVillage = useCallback((id: string) => { setSelectedId(id); setTab('Desa'); }, []);
  const [osmCanals] = useState<OsmCanals>({ type: 'FeatureCollection', features: [], available: false, reason: 'Geometri OpenStreetMap belum tersedia; pengambilan ulang dihentikan untuk demo.' });
  const pilot = usePilot(phase === 'command' && !preview && scope === 'pilot', mode, day);
  const { villages, risk, hotspots, meta } = pilot;
  const selected = villages.find(v => v.id === selectedId) ?? villages[0];
  const selectedDate = replayDate(meta, day);
  const [connection, setConnection] = useState('');
  const scoutBanner = config?.public_demo ? '' : scoutError;

  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; localStorage.setItem('peatforge-theme', dark ? 'dark' : 'light'); }, [dark]);
  const initialize = useCallback(async () => {
    setError(''); setPhase('loading');
    try {
      const next = await api<Config>('/config'); setConfig(next); if (next.public_demo === false || next.public_reporting_enabled) setMode('live'); setPreview(false); setPhase('command');
    } catch (failure) {
      if (failure instanceof ApiError && (failure.status === 401 || failure.status === 403)) { setPhase('login'); return; }
      // Only unavailable services activate the labelled, read-only shell preview.
      try {
        const next = await api<Config>('/config'); setConfig(next); if (next.public_demo === false || next.public_reporting_enabled) setMode('live'); setPreview(false); setPhase('command');
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


  useEffect(() => {
    if (phase !== 'command' || preview || scope !== 'pilot') return;
    let stopped = false; let pending = false;
    const controller = new AbortController();
    const tick = async () => {
      if (pending) return;
      pending = true;
      try {
        await api('/scout/tick', { method: 'POST', signal: controller.signal, body: JSON.stringify({ mode, day: mode === 'live' ? 0 : day }) });
        if (!stopped) setScoutError('');
      } catch { if (!stopped) setScoutError('Pemantauan otomatis Scout belum dapat diperbarui.'); }
      finally { pending = false; }
    };
    const initial = window.setTimeout(() => void tick(), 400);
    const timer = window.setInterval(() => void tick(), 4000);
    return () => { stopped = true; controller.abort(); clearTimeout(initial); clearInterval(timer); };
  }, [phase, preview, scope, mode, day]);

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

  return <main className="command-screen command-v2">
    <header className="command-topbar">
      <Brand/>
      <button className="command-scope text-button" onClick={() => setScope(scope === 'pilot' ? 'national' : 'pilot')}>{scope === 'pilot' ? 'Pulang Pisau · lihat Indonesia' : 'Indonesia · kembali ke pilot'}</button>
      {scope === 'pilot' && <div className="command-time-controls" aria-label="Waktu pantauan">
        <div className="command-mode-switch" role="group" aria-label="Mode waktu"><button aria-pressed={mode === 'live'} className={mode === 'live' ? 'active' : ''} onClick={() => setMode('live')}>Live</button><button aria-pressed={mode === 'replay'} className={mode === 'replay' ? 'active' : ''} onClick={() => setMode('replay')}>Replay 2019</button></div>
        {mode === 'replay' && <><div className="command-day-control"><label htmlFor="replay-day">{dateLabel(selectedDate)} · hari {day}</label><input id="replay-day" aria-label="Hari putar ulang" type="range" min={0} max={(meta?.days ?? 62) - 1} value={day} onChange={event => setDay(Number(event.target.value))}/></div>{selected && <div className="command-day-jump"><ReplayJumps villageId={selected.id} onJump={setDay} compact/></div>}</>}
      </div>}
      <div className="command-topbar-actions">{config?.public_reporting_enabled && <CommandPublicLink url={config.public_report_url}/>}<span className="command-connection"><i className={connection || preview || pilot.error ? 'offline' : ''}/>{preview ? 'Pratinjau lokal' : connection || pilot.error ? 'Pembaruan tertunda' : pilot.synced ? `Diperbarui ${pilot.synced}` : 'Menghubungkan…'}</span>{themeButton}{preview ? <button className="icon-button" aria-label="Coba hubungkan" onClick={() => void initialize()}><RefreshCw size={17}/></button> : !config?.public_demo && <button className="icon-button" aria-label="Keluar dari Command" disabled={busy} onClick={() => void logout()}><LogOut size={18}/></button>}</div>
    </header>
    {scope === 'national' ? <section className="command-national"><NationalView/></section> : <>
      <CommandSummary enabled={!preview} mode={mode} day={day} selectedDate={selectedDate} villages={villages} risk={risk} hotspots={hotspots} osmCanals={osmCanals} synced={pilot.synced} notice={connection || pilot.error || scoutBanner || (preview ? 'Pratinjau — API belum tersedia. Belum ada data operasional; pesan tidak dikirim.' : '')}/>
      <section className="command-workspace">
        <section className="command-map" aria-label="Peta wilayah pilot">
          <MapCanvas dark={dark} villages={villages} risk={risk} hotspots={hotspots} osmCanals={osmCanals} mode={mode} contained hideSourcePanel selectedId={selected?.id ?? null} onSelect={selectVillage}/>
          <details className="command-map-legend"><summary>Legenda</summary><div className="risk-legend">{['Rendah', 'Sedang', 'Tinggi', 'Ekstrem'].map((level, index) => <div key={level}><i className={`risk-${index}`}/><span>{level}</span></div>)}</div><p>Indeks rawan api (PFVI), bukan konfirmasi api. Air tanah yang dalam juga dapat melewati ambang kritis model.</p></details>
        </section>
        <div className="command-sheet">
          <aside className="command-decisions" aria-label="Keputusan semua desa">
            <header className="command-panel-heading"><span className="eyebrow">SEMUA DESA · {mode === 'replay' ? dateLabel(selectedDate) : 'KONDISI TERKINI'}</span><h1>Keputusan lapangan</h1><p>Periksa bukti dan manfaat model sebelum menyetujui tindakan. Disetujui belum berarti dilaksanakan.</p><span className="command-channel-status">{preview ? 'Pratinjau tanpa pengiriman' : config?.public_demo ? 'Demo terbuka · kanal simulasi' : config?.outbound === 'mock' || config?.mode === 'mock' ? 'Operator · kanal simulasi' : 'Operator · pengiriman diaktifkan'}</span></header>
            <DecisionInbox mode={mode} day={day} onShowLive={() => setMode('live')}/>
          </aside>
          <aside className="command-context" aria-label="Konteks desa terpilih">
            <header className="command-context-heading"><label htmlFor="command-village">Desa dipilih</label><select id="command-village" value={selected?.id ?? ''} disabled={!villages.length} onChange={event => selectVillage(event.target.value)}>{!villages.length && <option value="">Belum ada data desa</option>}{villages.map(village => <option key={village.id} value={village.id}>{village.name}</option>)}</select></header>
            <nav className="command-context-tabs" aria-label="Konteks desa">{tabs.map(({ name, icon: Icon }) => <button key={name} className={tab === name ? 'active' : ''} aria-pressed={tab === name} onClick={() => setTab(name)}><Icon size={16}/><span>{name}</span></button>)}</nav>
            <div className="command-context-content">
              {tab === 'Desa' && (selected ? <VillageDetail villageId={selected.id} mode={mode} day={day}/> : <Empty icon="village" title="Belum ada data desa" text="Lokasi dan kondisi desa belum tersedia dari layanan."/>)}
              {tab === 'Percakapan' && <SmsLoop villageId={selected?.id} mode={mode} day={day}/>}
              {tab === 'Jejak' && <AgentTrace villageId={selected?.id} mode={mode} day={day}/>}
            </div>
            <footer className="command-panel-footer"><span>{pilot.synced ? `Pembaruan ${pilot.synced} WIB` : 'Belum ada pembaruan'}</span><span>{scoutError && config?.public_demo ? 'Pemantauan otomatis belum tersinkron' : 'Diperbarui tiap 4 detik'}</span></footer>
          </aside>
        </div>
      </section>
    </>}
  </main>;
}

function Empty({ icon, title, text }: { icon: 'village' | 'phone' | 'trace'; title: string; text: string }) {
  const Icon = icon === 'village' ? Trees : icon === 'phone' ? Phone : Workflow;
  return <div className="empty-state"><div className="empty-icon"><Icon size={27}/></div><h3>{title}</h3><p>{text}</p></div>;
}
