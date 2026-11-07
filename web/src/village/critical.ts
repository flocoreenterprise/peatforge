export type CriticalStatus = {
  level: string; pfvi_level?: string; pfvi: number | null; wt_cm: number | null;
  critical_now?: boolean; critical_reason?: 'wt' | 'pfvi' | 'both' | null;
  days_to_critical: number | null;
};

export function criticalPresentation(s: CriticalStatus) {
  const wtCritical = s.critical_reason === 'wt' || s.critical_reason === 'both' || (s.wt_cm !== null && s.wt_cm >= 60);
  const pfviCritical = s.critical_reason === 'pfvi' || s.critical_reason === 'both' || (s.pfvi !== null && s.pfvi >= 250);
  const criticalNow = s.critical_now ?? (wtCritical || pfviCritical);
  const urgency = criticalNow
    ? 'Ambang kritis model tercapai sekarang.'
    : s.days_to_critical === null
      ? 'Prakiraan: ambang kritis belum tercapai dalam horizon 14 hari.'
      : s.days_to_critical === 0
        ? 'Prakiraan: ambang kritis tercapai pada hari ini; kondisi kini belum ditandai kritis.'
        : `Prakiraan: ambang kritis diperkirakan tercapai dalam ${s.days_to_critical} hari; belum kritis sekarang.`;
  return { pfviLevel: s.pfvi_level ?? s.level, wtCritical, criticalNow, urgency };
}
