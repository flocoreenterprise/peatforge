import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import { Layers, LocateFixed, Minus, Plus } from 'lucide-react';

export default function MapCanvas({ dark }: { dark: boolean }) {
  const node = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [satellite, setSatellite] = useState(true);
  const [tileError, setTileError] = useState(false);
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
    map.current = instance;
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(node.current);
    return () => { resize.disconnect(); instance.remove(); map.current = null; };
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
  return <>
    <div ref={node} className="map-canvas" aria-label="Peta dasar wilayah Pulang Pisau" />
    <div className="map-shade" />
    <div className="map-place"><span className="eyebrow">WILAYAH PILOT</span><strong>Pulang Pisau</strong><span>Kalimantan Tengah, Indonesia</span></div>
    {tileError && <div className="map-error" role="status">Peta dasar belum tersedia. Periksa sambungan internet.</div>}
    <div className="map-controls">
      <button aria-label="Perbesar peta" onClick={() => map.current?.zoomIn()}><Plus size={18}/></button>
      <button aria-label="Perkecil peta" onClick={() => map.current?.zoomOut()}><Minus size={18}/></button>
      <span />
      <button aria-label="Kembali ke wilayah pilot" onClick={() => map.current?.flyTo({ center: [114.08, -2.85], zoom: 9.1, bearing: 0, pitch: 0 })}><LocateFixed size={18}/></button>
    </div>
    <button className="basemap-button" onClick={() => setSatellite(!satellite)}><Layers size={16}/>{satellite ? 'Satelit' : 'OSM gelap'}</button>
  </>;
}
