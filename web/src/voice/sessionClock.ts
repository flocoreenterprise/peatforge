const epochMs = (value: number | string) => typeof value === 'number' ? value * 1000 : Date.parse(value);
export const ephemeralCurrent = (expiresAt: number | string, now = Date.now()) => Number.isFinite(epochMs(expiresAt)) && epochMs(expiresAt) > now;
// Mint expiry only limits connection establishment. A connected call has its own
// server deadline, capped locally, and does not end when the mint credential expires.
export const callLifetimeMs = (sessionExpiresAt?: number | string, now = Date.now()) => sessionExpiresAt === undefined ? 300000 : Math.max(1, Math.min(300000, epochMs(sessionExpiresAt) - now || 1));
