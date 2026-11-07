import { useEffect, useRef, useState } from 'react';
import QRCode from 'qrcode';
import { publicLinkInfo } from './link';
import './public.css';

export function CommandPublicLink({ url }: { url?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  const link = publicLinkInfo(url);
  useEffect(() => {
    let active = true; setFailed(false);
    if (canvas.current) void QRCode.toCanvas(canvas.current, link.url, { width: 160, margin: 2, errorCorrectionLevel: 'M' }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [link.url]);
  return <section className="public-link" aria-label="Bagikan laporan warga">
    {!failed && <canvas ref={canvas} role="img" aria-label="Kode QR halaman laporan warga"/>}
    <div><strong>Laporan warga tanpa nama</strong><p>{link.local ? 'QR lokal · belum bisa dibuka dari ponsel' : 'QR HTTPS · pastikan alamat server aktif'}</p><a href={link.url} target="_blank" rel="noreferrer">Buka halaman laporan</a>{failed && <p>Kode QR belum tersedia; gunakan tautan laporan.</p>}<p>Warga memilih desa dan menyetujui pemrosesan percakapan oleh AI sebelum mulai.</p></div>
  </section>;
}
export default CommandPublicLink;
