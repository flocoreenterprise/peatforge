export function deliveryLabel(status: string | undefined): string {
  return ({ received: 'Pesan masuk diterima', processing: 'Sedang diproses', mock: 'Simulasi; tidak dikirim nyata', queued: 'Dalam antrean; belum diterima', sent: 'Dikirim; penerimaan belum dikonfirmasi', delivered: 'Penerimaan dikonfirmasi', pending: 'Menunggu pengiriman', dispatching: 'Pengiriman sedang diproses', failed: 'Pengiriman gagal', suppressed: 'Ditahan oleh kebijakan', unknown: 'Status pengiriman belum diketahui' } as Record<string, string>)[status ?? ''] ?? 'Status pengiriman belum tersedia';
}

