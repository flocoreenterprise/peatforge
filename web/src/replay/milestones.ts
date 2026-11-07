export type Milestone = {
  kind: 'wt_60' | 'pfvi_175' | 'pfvi_250' | 'critical';
  day: number | null; date: string | null;
};
export type Milestones = {
  village_id: string; items: Milestone[]; source: string;
  stale: boolean; data_as_of: string | null;
};

const jumps = [
  { kind: 'wt_60', label: 'Lompat ke WT kritis' },
  { kind: 'pfvi_175', label: 'PFVI Tinggi' },
  { kind: 'pfvi_250', label: 'PFVI Ekstrem' },
] as const;

export function milestoneChoices(items: Milestone[]) {
  return jumps.map(jump => {
    const item = items.find(item => item.kind === jump.kind);
    const valid = item !== undefined && item.day !== null && Number.isInteger(item.day)
      && item.day >= 0 && item.date !== null && /^\d{4}-\d{2}-\d{2}$/.test(item.date);
    return { ...jump, day: valid ? item.day : null, date: valid ? item.date : null,
      unavailable: item?.day === null ? 'Ambang tidak tercapai dalam periode replay' : 'Data ambang belum tersedia' };
  });
}
