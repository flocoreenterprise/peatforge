type SourceData = { source?: string | null; assumptions?: string[]; data_as_of?: string | null; stale?: boolean; reason?: string | null; available?: boolean };
export function sourceName(data: SourceData): string {
  const source = data.source?.trim();
  if (source === 'fixture') return 'data contoh (fixture)';
  const assumptions = data.assumptions ?? [];
  const openMeteo = assumptions.some(value => value.startsWith('Open-Meteo model forcing'));
  const era5 = assumptions.some(value => value.startsWith('ERA5 historical perfect-forecast'));
  if (era5) return openMeteo ? 'ERA5 via Open-Meteo' : 'ERA5';
  if (openMeteo) return 'Open-Meteo';
  if (source === 'modelled') return 'perkiraan model (penyedia tidak dicantumkan)';
  if (source === 'measured') return 'pengamatan (penyedia tidak dicantumkan)';
  return source || 'belum tersedia';
}
function calendar(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Jakarta' }).format(date) : null;
}
export function sourceStamp(data: SourceData, context: { mode?: 'live' | 'replay'; historicalDate?: string | null } = {}): string {
  const archiveDate = context.historicalDate && /^\d{4}-\d{2}-\d{2}$/.test(context.historicalDate) ? calendar(`${context.historicalDate}T00:00:00Z`) : null;
  const prefix = context.mode === 'replay' ? `Arsip ${archiveDate ?? 'tanggal belum tersedia'} · ` : '';
  const source = sourceName(data);
  const value = data.data_as_of;
  const date = value ? new Date(value) : null;
  if (!date || !Number.isFinite(date.getTime()) || !/T\d{2}:\d{2}.*(?:Z|[+-]\d{2}:\d{2})$/.test(value!)) {
    return `${prefix}Belum ada data waktu · sumber ${source} — ${data.available === false && data.reason ? data.reason : 'timestamp data_as_of belum tersedia atau tidak lengkap'}`;
  }
  const parts = new Intl.DateTimeFormat('id-ID', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Asia/Jakarta' }).formatToParts(date);
  const time = `${parts.find(part => part.type === 'hour')!.value}:${parts.find(part => part.type === 'minute')!.value}`;
  return `${prefix}Data per ${calendar(value!)} ${time} WIB · sumber ${source}${data.stale ? ' · cache kedaluwarsa' : ''}`;
}
