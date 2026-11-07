export type ModelSetup = {
  spacing_m: number; nodes: number; conductivity_m_day: number; specific_yield: number;
  effective_hydraulic_thickness_m: number;
  boundary_application: 'both_endpoints'; target_effect: 'single_symmetric_strip';
  horizon_days: number; provenance: 'team_assumption_not_field_calibrated';
};

const format = (value: number | null) => value === null ? 'belum tersedia' : new Intl.NumberFormat('id-ID', { maximumFractionDigits: 1 }).format(value);

export function scenarioGainText(simulation: { wt_gain_cm: number | null; model_setup?: ModelSetup }): string {
  const horizon = simulation.model_setup?.horizon_days;
  return `Hasil skenario ini pada akhir ${horizon === undefined ? 'horizon' : `${format(horizon)} hari`}: air tanah modelled lebih dangkal ${format(simulation.wt_gain_cm)} cm dibanding tanpa tindakan. Ini selisih model, belum terukur di lapangan.`;
}

export function hydraulicAssumptionText(setup?: ModelSetup): string {
  if (!setup) return 'Metadata susunan model belum tersedia; parameter belum dapat ditampilkan.';
  return `Jarak kanal ${format(setup.spacing_m)} m; ketebalan hidraulik efektif ${format(setup.effective_hydraulic_thickness_m)} m adalah asumsi benchmark tim, bukan kedalaman gambut. Parameter belum dikalibrasi independen atau disurvei di lapangan.`;
}
