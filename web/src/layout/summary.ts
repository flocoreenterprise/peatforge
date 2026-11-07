import type { Risk } from '../map/pilot';
export type SummaryAction = { id: string; village_id: string; mode: string; day: number; kind: string; status: string; days_bought: number | null; at_least_days?: number | null; simulation_reason?: string };
export function summaryFacts(villageIds: string[], risk: Risk | null, actions: SummaryAction[] | null, mode: 'live' | 'replay', day: number) {
  let critical = 0, unknown = 0;
  for (const id of new Set(villageIds)) {
    const status = risk?.features.find(feature => feature.properties.village_id === id)?.properties;
    if (!status || (status.mode && status.mode !== mode) || (mode === 'replay' && typeof status.day === 'number' && status.day !== day)) { unknown++; continue; }
    if (status.critical_now === true || status.critical_reason === 'wt' || status.critical_reason === 'pfvi' || status.critical_reason === 'both' || (typeof status.wt_cm === 'number' && Number.isFinite(status.wt_cm) && status.wt_cm >= 60) || (typeof status.pfvi === 'number' && Number.isFinite(status.pfvi) && status.pfvi >= 250)) critical++;
    else if (!Number.isFinite(status.wt_cm) || !Number.isFinite(status.pfvi)) unknown++;
  }
  const scoped = actions?.filter(action => action.mode === mode && (mode === 'live' || action.day === day) && action.status === 'proposed') ?? null;
  const benefits = scoped?.filter(action => action.kind !== 'verify' && typeof action.days_bought === 'number' && Number.isFinite(action.days_bought) && action.days_bought >= 0).sort((a, b) => b.days_bought! - a.days_bought!) ?? [];
  const known = new Set(villageIds).size - unknown;
  return { critical: risk && known > 0 ? critical : null, unknown, pending: scoped?.length ?? null, best: benefits[0] ?? null, hasLowerBounds: scoped?.some(action => action.at_least_days != null && action.at_least_days > 0) ?? false };
}
