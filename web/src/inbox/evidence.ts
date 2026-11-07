export type Evidence = { confidence_label?: string; checks?: { confidence_label?: string; qualitative?: boolean }[] };
export function evidenceText(verifier: Evidence): string | null {
  const low = verifier.confidence_label === 'LOW' || verifier.checks?.some(check => check.confidence_label === 'LOW');
  if (!low) return null;
  return verifier.checks?.some(check => check.qualitative === true)
    ? 'Laporan kualitatif · bukti awal rendah (LOW). Tidak diubah menjadi angka pengukuran; perlu pemeriksaan lapangan.'
    : 'Bukti awal rendah (LOW). Laporan atau lokasi perlu diperiksa di lapangan.';
}
