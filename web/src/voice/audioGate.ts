type Recorder = Pick<MediaRecorder, 'state' | 'ondataavailable' | 'onstop' | 'onerror' | 'start' | 'stop'>;
type ProviderEvent = { type?: string; response_id?: string; item_id?: string; response?: { id?: string; status?: string; output?: { type?: string }[] } };
type GateHooks = { recorder: () => Recorder; send: () => void; play: (url: string) => Promise<void>; stopPlayback: () => void; url: (blob: Blob) => string; revoke: (url: string) => void; busy: (value: boolean) => void; fail: () => void; settled?: () => void };
type Capture = { recorder: Recorder; id?: string; transcriptItem?: string; approved: boolean; audio: boolean; stopped: boolean; completed: boolean; flushing: boolean; chunks: Blob[]; bytes: number; timer: ReturnType<typeof setTimeout> };

// The server MUST disable automatic responses. Every response.create goes through
// this scheduler. A rejected/unknown response ends the peer, so its RTP tail can
// never enter a later approved recording. Remote RTP is never an audio DOM source.
export class ValidatedAudioGate {
  private closed = false;
  private ready = false;
  private queued = 0;
  private active: Capture | null = null;
  private playing = false;
  private urls = new Set<string>();
  private readyTimer: ReturnType<typeof setTimeout> | undefined;
  constructor(private hooks: GateHooks, private timeoutMs = 30000) {}
  streamReady() { this.ready = true; clearTimeout(this.readyTimer); this.run(); }
  request() {
    if (this.closed) return;
    if (++this.queued > 4) { this.reject(); return; }
    if (!this.ready && !this.readyTimer) this.readyTimer = setTimeout(() => this.reject(), 5000);
    this.run();
  }
  private run() {
    if (this.closed || !this.ready || this.active || this.playing || !this.queued) return;
    this.queued--;
    try {
      this.hooks.busy(true);
      const recorder = this.hooks.recorder();
      const capture: Capture = { recorder, approved: false, audio: false, stopped: false, completed: false, flushing: false, chunks: [], bytes: 0, timer: setTimeout(() => this.reject(), this.timeoutMs) };
      this.active = capture;
      recorder.ondataavailable = event => {
        if (this.closed || this.active !== capture) return;
        capture.bytes += event.data.size;
        if (capture.bytes > 2_000_000) { this.reject(); return; }
        if (event.data.size) capture.chunks.push(event.data);
      };
      recorder.onerror = () => this.reject();
      recorder.onstop = () => { void this.flushed(capture).catch(() => this.reject()); };
      recorder.start(); // Synchronous recording starts BEFORE the provider request.
      this.hooks.send();
    } catch { this.reject(); }
  }
  handle(event: ProviderEvent) {
    if (this.closed) return;
    if (event.type === 'input_audio_buffer.speech_started' && (this.active || this.playing)) { this.reject(); return; }
    if (event.type === 'response.created') {
      if (!this.active || this.active.id || !event.response?.id) { this.reject(); return; }
      this.active.id = event.response.id; return;
    }
    const relevant = ['output_audio_buffer.started', 'output_audio_buffer.stopped', 'output_audio_buffer.cleared', 'response.output_audio_transcript.done', 'response.done'];
    if (!event.type || !relevant.includes(event.type)) return;
    const capture = this.active;
    const id = event.response_id ?? event.response?.id;
    if (!capture || !id || capture.id !== id) { this.reject(); return; }
    if (event.type === 'output_audio_buffer.cleared') { this.reject(); return; }
    if (event.type === 'output_audio_buffer.started') capture.audio = true;
    if (event.type === 'response.output_audio_transcript.done') {
      if (!event.item_id || (capture.transcriptItem && capture.transcriptItem !== event.item_id)) { this.reject(); return; }
      capture.audio = true; capture.transcriptItem = event.item_id;
    }
    if (event.type === 'output_audio_buffer.stopped') capture.stopped = true;
    if (event.type === 'response.done') {
      if (event.response?.status !== 'completed') { this.reject(); return; }
      capture.completed = true;
      if (event.response.output?.some(item => item.type === 'message')) capture.audio = true;
    }
    this.finish(capture);
  }
  approved(responseId: string | undefined, itemId: string) {
    const capture = this.active;
    if (this.closed) return;
    if (!capture || !responseId || capture.id !== responseId || capture.transcriptItem !== itemId) { this.reject(); return; }
    capture.approved = true; this.finish(capture);
  }
  private finish(capture: Capture) {
    if (capture.flushing || !capture.completed || (capture.audio && (!capture.approved || !capture.stopped))) return;
    capture.flushing = true;
    try { capture.recorder.stop(); } catch { this.reject(); }
  }
  private async flushed(capture: Capture) {
    if (this.closed || this.active !== capture || !capture.flushing) return;
    clearTimeout(capture.timer);
    this.active = null;
    if (capture.audio) {
      if (!capture.approved || !capture.chunks.length) { this.reject(); return; }
      const blob = new Blob(capture.chunks, { type: capture.chunks[0].type || 'audio/webm' });
      capture.chunks = [];
      const url = this.hooks.url(blob); this.urls.add(url); this.playing = true;
      const playbackTimer = setTimeout(() => this.reject(), this.timeoutMs);
      try { await this.hooks.play(url); }
      catch { this.reject(); }
      finally { clearTimeout(playbackTimer); if (this.urls.delete(url)) this.hooks.revoke(url); this.playing = false; }
    }
    if (!this.closed) { this.hooks.busy(false); this.hooks.settled?.(); this.run(); }
  }
  reject() { if (this.closed) return; this.close(); this.hooks.fail(); }
  close() {
    if (this.closed) return;
    this.closed = true; clearTimeout(this.readyTimer); this.queued = 0;
    const capture = this.active; this.active = null;
    if (capture) {
      clearTimeout(capture.timer); capture.chunks = [];
      capture.recorder.ondataavailable = null; capture.recorder.onstop = null; capture.recorder.onerror = null;
      if (capture.recorder.state !== 'inactive') { try { capture.recorder.stop(); } catch { /* Already released. */ } }
    }
    this.hooks.stopPlayback();
    for (const url of this.urls) this.hooks.revoke(url);
    this.urls.clear(); this.hooks.busy(false);
  }
}
