export type Config = { mode: string; public_demo?: boolean; demo_public?: boolean; public_reporting_enabled?: boolean; public_report_url?: string; source?: string; outbound?: string; voice_mode?: string; poll_ms: number; field_poll_ms: number; voice_available: boolean; browser_voice_supported?: boolean };
export type Village = { id: string; name: string; code: string; lat: number; lon: number; source: string };
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

// Cookie authentication stays on the same origin. Never print request bodies or URLs.
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options, credentials: 'same-origin',
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
    signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(12000)]) : AbortSignal.timeout(12000),
  });
  if (response.status === 401) window.dispatchEvent(new Event('peatforge:unauthorized'));
  if (!response.ok) {
    let message = 'Layanan belum dapat dihubungi. Coba lagi.';
    try { const body = await response.json(); message = body.error?.message ?? message; } catch { /* Non-JSON proxy error. */ }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export const mockConfig: Config = { mode: 'mock', poll_ms: 4000, field_poll_ms: 3000, voice_available: false };
