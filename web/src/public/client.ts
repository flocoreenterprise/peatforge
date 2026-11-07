export type PublicVillage = { id: string; name: string; lat: number; lon: number };
export type PublicMessage = { id: string; direction: string; body: string; created_at?: string; status?: string };
export type PublicAction = { id: string; title: string; status: string; kind?: string; priority?: string };
export type PublicThread = { items: PublicMessage[]; actions?: PublicAction[]; state?: string };
export type StoredPublicSession = { token: string; village_id: string; village_name: string };
export const PUBLIC_STORAGE_KEY = 'peatforge-public-report';
export class PublicError extends Error { constructor(public status: number) { super('Layanan laporan belum tersedia.'); } }
export async function publicRequest<T>(path: string, token: string | null, signal: AbortSignal, body?: unknown): Promise<T> {
  const response = await fetch(`/api/public/${path}`, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.any([signal, AbortSignal.timeout(12000)]),
  });
  if (!response.ok) throw new PublicError(response.status);
  return response.json() as Promise<T>;
}
export function parseStoredSession(raw: string | null): StoredPublicSession | null {
  try {
    const data: unknown = JSON.parse(raw ?? 'null');
    if (!data || typeof data !== 'object' || !('token' in data) || !('village_id' in data) || !('village_name' in data)) return null;
    if (typeof data.token !== 'string' || !/^[A-Za-z0-9_-]{20,200}$/.test(data.token) || typeof data.village_id !== 'string' || !data.village_id || typeof data.village_name !== 'string') return null;
    return data as StoredPublicSession;
  } catch { return null; }
}
export const publicSessionDenied = (error: unknown) => error instanceof PublicError && [401, 403, 404].includes(error.status);
export const publicActionLabel = (status: string) => ({ proposed: 'Menunggu keputusan operator', approved: 'Disetujui · pelaksanaan belum dikonfirmasi', rejected: 'Tidak disetujui', done: 'Pelaksanaan dikonfirmasi', expired: 'Usulan kedaluwarsa', superseded: 'Digantikan usulan lain' }[status] ?? 'Status sedang diperiksa');
