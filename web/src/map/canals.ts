export type OsmCanals = {
  type: 'FeatureCollection';
  features: GeoJSON.Feature<GeoJSON.LineString, Record<string, unknown>>[];
  available?: boolean;
  source?: string;
  attribution?: string;
  reason?: string;
  [key: string]: unknown;
};

// This overlay is cartographic context only. It never supplies simulation canals,
// hydraulic parameters, or an inferred canal spacing.
export function canalOverlay(data: OsmCanals | null | undefined): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  return {
    type: 'FeatureCollection',
    features: data?.available === false || !Array.isArray(data?.features) ? [] : data.features.filter(feature => {
      if (feature?.geometry?.type !== 'LineString') return false;
      const properties = feature.properties ?? {};
      const tags = properties.tags && typeof properties.tags === 'object' ? properties.tags as Record<string, unknown> : {};
      const waterway = properties.waterway ?? tags.waterway;
      const coordinates = feature.geometry.coordinates;
      return ['canal', 'ditch', 'drain'].includes(String(waterway)) && Array.isArray(coordinates) && coordinates.length >= 2 && coordinates.every(point => Array.isArray(point) && point.length >= 2 && Number.isFinite(point[0]) && Number.isFinite(point[1]) && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90);
    }),
  };
}

export function hotspotSourceLabel(data: { source?: string; available?: boolean; items: { source?: string }[] } | null, mode: 'live' | 'replay') {
  const period = mode === 'replay' ? 'Arsip titik panas' : 'Titik panas';
  if (!data) return `${period}: memuat sumber…`;
  if (data.available === false) return `${period}: belum tersedia`;
  const sources = [...new Set([data.source, ...data.items.map(item => item.source)].filter((source): source is string => !!source?.trim()))];
  return `${period}: ${sources.length ? sources.join(' · ') : 'sumber belum tersedia'} · bukan api terkonfirmasi`;
}
