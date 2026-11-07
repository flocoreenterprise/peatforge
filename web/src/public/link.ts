export function publicLinkInfo(value?: string) {
  const fallback = 'http://localhost:8001/lapor';
  try {
    const url = new URL(value || fallback);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !/^\/lapor\/?$/.test(url.pathname)) throw new Error('Invalid public link');
    const loopback = ['localhost', '127.0.0.1', '[::1]', '0.0.0.0'].includes(url.hostname) || url.hostname.endsWith('.localhost');
    return { url: url.toString(), local: loopback || url.protocol !== 'https:' };
  } catch { return { url: fallback, local: true }; }
}
