export type FireClass = 'gambut' | 'lahan_vegetasi' | 'industri' | 'gunung_api' | 'tidak_pasti';
export type FireEvent = { id: string; classification: FireClass; detection_ids: string[]; reasons: string[]; sources: { name: string; url?: string }[]; confirmed: false };
export type FireEvents = { items: FireEvent[]; available?: boolean; partial?: boolean; data_as_of?: string | null; source?: string; assumptions?: string[]; stale?: boolean };
export const classLabel = (value?: FireClass) => ({ gambut: 'Kandidat kebakaran gambut', lahan_vegetasi: 'Kandidat kebakaran vegetasi', industri: 'Kandidat panas industri', gunung_api: 'Kandidat panas gunung api', tidak_pasti: 'Klasifikasi belum pasti' }[value ?? 'tidak_pasti']);
export function eventForDetection(events: FireEvents | null | undefined, id: string) { return events?.available === false ? undefined : events?.items.find(event => event.detection_ids.includes(id)); }
export type FireHeadline = { raw_count: number | null; event_count: number | null; land_event_count: number | null; peat_event_count?: number | null; excluded_detection_count?: number | null; excluded_industry_count: number | null; excluded_volcano_count: number | null; uncertain_event_count: number | null };
export type FireOverview = FireEvents & { headline: FireHeadline; classification_counts?: Partial<Record<FireClass, number>>; by_province?: { province: string }[]; coverage?: unknown; reason?: string };
const count = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? new Intl.NumberFormat('id-ID').format(value) : 'belum tersedia';
export function fireHeadline(data: FireOverview | null) {
  if (!data || data.available === false) return null;
  const h = data.headline;
  if (!h || typeof h.land_event_count !== 'number') return null;
  return `${count(h.land_event_count)} kejadian kebakaran lahan terklasifikasi (${count(h.peat_event_count)} di atas gambut) · ${count(h.excluded_detection_count)} deteksi dikecualikan: industri/gunung api`;
}
