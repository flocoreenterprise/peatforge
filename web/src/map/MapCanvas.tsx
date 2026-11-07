/// <reference types="vite/client" />
import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { Layers, LocateFixed, Minus, Plus } from 'lucide-react';
import type { Village } from '../api';
import type { Hotspots, Risk } from './pilot';

// MapLibre 6 ships a standalone module worker. Explicitly emit its URL so the
// production bundle does not resolve a missing sibling beside the app chunk.
maplibregl.setWorkerUrl(workerUrl);

function fitVillages(instance: maplibregl.Map, villages: Village[]) {
  if (!villages.length) return;
  const bounds = new maplibregl.LngLatBounds();
  villages.forEach(village => bounds.extend([village.lon, village.lat]));
  const { clientWidth: width, clientHeight: height } = instance.getContainer();
  instance.fitBounds(bounds, {
    padding: width < 768 ? { top: 155, bottom: height * .51 + 65, left: 50, right: 90 }
      : width < 1200 ? { top: 180, bottom: 90, left: 390, right: 140 }
      : { top: 180, bottom: 100, left: 470, right: 340 },
    maxZoom: 11.3, duration: 0, bearing: 0, pitch: 0,
  });
}

export default function MapCanvas({ dark, villages, risk, hotspots, selectedId, onSelect }: { dark: boolean; villages: Village[]; risk: Risk | null; hotspots: Hotspots | null; selectedId: string | null; onSelect: (id: string) => void }) {
  const node = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [satellite, setSatellite] = useState(true);
  const [tileError, setTileError] = useState(false);
  const [ready, setReady] = useState(false);
  const fitted = useRef(false);
  const villagesRef = useRef(villages);
  villagesRef.current = villages;
  useEffect(() => {
    if (!node.current) return;
    const instance = new maplibregl.Map({
      container: node.current, center: [114.08, -2.85], zoom: 9.1,
      attributionControl: { compact: true },
      style: { version: 8, sources: {
        imagery: { type: 'raster', tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'], tileSize: 256, attribution: 'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community' },
        osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>' },
      }, layers: [
        { id: 'imagery', type: 'raster', source: 'imagery', paint: { 'raster-saturation': -0.85, 'raster-brightness-min': 0.1, 'raster-brightness-max': 0.87 } },
        { id: 'osm', type: 'raster', source: 'osm', layout: { visibility: 'none' }, paint: { 'raster-saturation': -1, 'raster-brightness-max': 0.42 } },
      ] },
    });
    instance.on('error', () => setTileError(true));
    instance.on('idle', () => { if (instance.areTilesLoaded()) setTileError(false); });
    instance.on('load', () => setReady(true));
    const suppressContextMenu = (event: MouseEvent) => event.preventDefault();
    instance.getCanvas().addEventListener('contextmenu', suppressContextMenu);
    map.current = instance;
    let previousWidth = node.current.clientWidth;
    let previousHeight = node.current.clientHeight;
    const resize = new ResizeObserver(() => {
      const { clientWidth, clientHeight } = instance.getContainer();
      if (clientWidth === previousWidth && clientHeight === previousHeight) return;
      previousWidth = clientWidth; previousHeight = clientHeight;
      instance.resize();
      if (instance.isStyleLoaded()) {
        fitVillages(instance, villagesRef.current);
        fitted.current = villagesRef.current.length > 0;
      }
    });
    resize.observe(node.current);
    return () => { resize.disconnect(); instance.getCanvas().removeEventListener('contextmenu', suppressContextMenu); instance.remove(); map.current = null; };
  }, []);
  useEffect(() => {
    const instance = map.current;
    const update = () => {
      instance?.setLayoutProperty('imagery', 'visibility', satellite ? 'visible' : 'none');
      instance?.setLayoutProperty('osm', 'visibility', satellite ? 'none' : 'visible');
      instance?.setPaintProperty('imagery', 'raster-brightness-max', dark ? 0.48 : 0.87);
    };
    if (instance?.isStyleLoaded()) update(); else instance?.once('load', update);
    return () => { instance?.off('load', update); };
  }, [satellite, dark]);
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready) return;
    const markers = villages.map(village => {
      const status = risk?.features.find(feature => feature.properties.village_id === village.id)?.properties;
      const button = document.createElement('button');
      button.className = `village-marker ${village.lon < 114.13 ? 'label-left' : ''} ${status?.level?.toLowerCase() ?? 'unknown'} ${village.id === selectedId ? 'selected' : ''}`;
      button.setAttribute('aria-label', `${village.name}: PFVI ${status?.level ?? 'belum tersedia'}${status?.wt_cm != null && status.wt_cm >= 60 ? ' · WT kritis' : ''} · perkiraan model, belum diukur. Pilih desa`);
      button.setAttribute('aria-pressed', String(village.id === selectedId));
      const dot = document.createElement('span'); dot.className = 'marker-dot';
      const label = document.createElement('span'); label.className = 'marker-label'; label.textContent = village.name + (status?.wt_cm != null && status.wt_cm >= 60 ? ' · WT KRITIS' : '');
      button.append(dot, label); button.onclick = () => onSelect(village.id);
      return new maplibregl.Marker({ element: button, anchor: 'center' }).setLngLat([village.lon, village.lat]).addTo(instance);
    });
    if (villages.length && !fitted.current) {
      fitVillages(instance, villages);
      fitted.current = true;
    }
    return () => markers.forEach(marker => marker.remove());
  }, [villages, risk, selectedId, onSelect, ready]);
  useEffect(() => {
    const instance = map.current; if (!instance || !ready) return;
    const referenceTime = Date.parse(hotspots?.data_as_of ?? '') || Date.now();
    const data: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: (hotspots?.available === false ? [] : hotspots?.items ?? []).map(point => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [point.lon, point.lat] }, properties: { recent: referenceTime - Date.parse(point.detected_at) >= 0 && referenceTime - Date.parse(point.detected_at) < 86400000 } })) };
    if (instance.getSource('hotspots')) (instance.getSource('hotspots') as maplibregl.GeoJSONSource).setData(data);
    else { instance.addSource('hotspots', { type: 'geojson', data }); instance.addLayer({ id: 'hotspot-points', type: 'circle', source: 'hotspots', paint: { 'circle-radius': 5, 'circle-color': ['case', ['get', 'recent'], '#e58c44', '#c6ad8d'], 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff8e9' } }); }
  }, [hotspots, ready]);
  return <>
    <div ref={node} className="map-canvas" aria-label="Peta dasar wilayah Pulang Pisau" />
    <div className="map-shade" />
    {!villages.length && <div className="map-place"><span className="eyebrow">WILAYAH PILOT</span><strong>Pulang Pisau</strong><span>Kalimantan Tengah, Indonesia</span></div>}
    {tileError && <div className="map-error" role="status">Peta dasar belum tersedia. Periksa sambungan internet.</div>}
    <div className="map-controls">
      <button aria-label="Perbesar peta" onClick={() => map.current?.zoomIn()}><Plus size={18}/></button>
      <button aria-label="Perkecil peta" onClick={() => map.current?.zoomOut()}><Minus size={18}/></button>
      <span />
      <button aria-label="Kembali ke wilayah pilot" onClick={() => { if (map.current) fitVillages(map.current, villages); }}><LocateFixed size={18}/></button>
    </div>
    <button className="basemap-button" onClick={() => setSatellite(!satellite)}><Layers size={16}/>{satellite ? 'Satelit' : 'OSM gelap'}</button>
  </>;
}


