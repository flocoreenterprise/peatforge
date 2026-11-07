export type Transcript = { id: string; role: 'user' | 'assistant'; text: string };
type Event = { type?: string; event_id?: string; item_id?: string; response_id?: string; transcript?: string; delta?: string; call_id?: string; name?: string; arguments?: string; response?: { status?: string; output?: Event[] } };
type Hooks = {
  persist: (entry: Transcript) => Promise<unknown>;
  tool: (callId: string, name: string, args: Record<string, unknown>) => Promise<unknown>;
  send: (event: unknown) => void;
  transcript: (entry: Transcript) => void;
  ended: () => void;
  validated?: (responseId: string | undefined, itemId: string) => void;
  failed?: () => void;
};

// Event delivery and transcription completion are asynchronous in Realtime.
// Tool mutations wait for the committed audio turn, including its server ACK.
export class VoiceEvents {
  private closed = false;
  private tools = Promise.resolve();
  private writes = Promise.resolve();
  private seen = new Set<string>();
  private drafts = new Map<string, string>();
  private inputs = new Map<string, { promise: Promise<void>; resolve: () => void; reject: (reason: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  private hasUser = false;
  private ending = false;
  private endTranscript = false;
  private endTimer: ReturnType<typeof setTimeout> | undefined;
  constructor(private hooks: Hooks, private waitMs = 12000) {}

  private beginInput(id = '__pending') {
    if (this.inputs.has(id)) return;
    const unnamed = this.inputs.get('__pending');
    if (id !== '__pending' && unnamed) { this.inputs.delete('__pending'); this.inputs.set(id, unnamed); return; }
    let resolve!: () => void;
    let reject!: (reason: Error) => void;
    const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
    // Attach a handler immediately; a tool may never arrive for this turn.
    void promise.catch(() => {});
    const timer = setTimeout(() => { reject(new Error('Transkripsi belum tersedia.')); this.hooks.failed?.(); }, this.waitMs);
    this.inputs.set(id, { promise, resolve, reject, timer });
  }

  handle(event: Event): Promise<void> {
    if (this.closed) return Promise.resolve();
    if (event.type === 'error' || (event.type === 'response.done' && event.response?.status === 'failed')) return Promise.reject(new Error('Layanan suara belum tersedia.'));
    if (event.type === 'input_audio_buffer.speech_started' || event.type === 'input_audio_buffer.committed') this.beginInput(event.item_id);
    if (event.type === 'conversation.item.input_audio_transcription.failed') {
      for (const input of this.inputs.values()) input.reject(new Error('Transkripsi belum tersedia.'));
      return Promise.reject(new Error('Transkripsi belum tersedia.'));
    }
    if (event.type === 'conversation.item.input_audio_transcription.completed') {
      const entry: Transcript = { id: event.item_id ?? event.event_id ?? '', role: 'user', text: event.transcript ?? '' };
      const key = this.inputs.has(entry.id) ? entry.id : '__pending';
      const pending = this.inputs.get(key);
      const wasSeen = this.seen.has(`user:${entry.id}`);
      const task = this.save(entry).then(() => { this.hasUser = true; pending?.resolve(); if (!this.closed && !wasSeen) this.hooks.send({ type: 'response.create' }); }).catch(error => { pending?.reject(error); throw error; }).finally(() => {
        if (pending) clearTimeout(pending.timer);
        if (this.inputs.get(key) === pending) this.inputs.delete(key);
      });
      return task;
    }
    if (event.type === 'response.output_audio_transcript.delta') {
      const id = event.item_id ?? '';
      if (id) this.drafts.set(id, (this.drafts.get(id) ?? '') + (event.delta ?? ''));
    }
    if (event.type === 'response.output_audio_transcript.done') {
      const id = event.item_id ?? event.event_id ?? '';
      const text = event.transcript ?? this.drafts.get(id) ?? '';
      this.drafts.delete(id);
      return this.save({ id, role: 'assistant', text }).then(() => {
        if (this.closed) return;
        if (this.ending) this.endTranscript = true;
        this.hooks.validated?.(event.response_id, id);
      });
    }
    if (event.type === 'response.function_call_arguments.done') return this.enqueueTool(event);
    if (event.type === 'response.done') {
      const calls = (event.response?.output ?? []).filter(item => item.type === 'function_call');
      return Promise.all(calls.map(call => this.enqueueTool(call))).then(() => {});
    }
    return Promise.resolve();
  }

  private save(entry: Transcript) {
    if (!entry.id || !entry.text.trim()) return Promise.reject(new Error('Transkripsi belum tersedia.'));
    const key = `${entry.role}:${entry.id}`;
    if (this.seen.has(key)) return this.writes;
    this.seen.add(key);
    this.writes = this.writes.then(async () => {
      if (this.closed) return;
      const ack = await this.hooks.persist(entry);
      if (!ack || typeof ack !== 'object' || !('accepted' in ack) || ack.accepted !== true) throw new Error('Transkripsi belum dapat diverifikasi.');
      if (!this.closed) this.hooks.transcript(entry);
    });
    return this.writes;
  }

  private enqueueTool(event: Event) {
    const { call_id: id, name } = event;
    if (!id || !name || !['get_village_status', 'record_report', 'confirm_report', 'end_call'].includes(name)) return Promise.reject(new Error('Perintah suara tidak tersedia.'));
    const key = `tool:${id}`;
    if (this.seen.has(key)) return this.tools;
    this.seen.add(key);
    // Capture this turn's barrier before any later incoming audio turn replaces it.
    const inputs = [...this.inputs.values()].map(input => input.promise);
    this.tools = this.tools.then(async () => {
      await Promise.all(inputs);
      await this.writes;
      if (this.closed) return;
      if (['record_report', 'confirm_report'].includes(name) && !this.hasUser) throw new Error('Transkripsi belum tersedia.');
      const args: unknown = JSON.parse(event.arguments ?? '{}');
      if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Perintah suara tidak tersedia.');
      const result = await this.hooks.tool(id, name, args as Record<string, unknown>);
      if (this.closed) return;
      this.hooks.send({ type: 'conversation.item.create', item: { type: 'function_call_output', call_id: id, output: JSON.stringify(result) } });
      if (name === 'end_call') { this.ending = true; this.endTimer = setTimeout(() => { if (!this.closed) this.hooks.ended(); }, 60000); }
      this.hooks.send({ type: 'response.create' });
    });
    return this.tools;
  }

  playbackSettled() { if (!this.closed && this.ending && this.endTranscript) this.hooks.ended(); }

  close() {
    this.closed = true;
    clearTimeout(this.endTimer);
    for (const input of this.inputs.values()) { clearTimeout(input.timer); input.reject(new Error('Sesi berakhir.')); }
    this.inputs.clear();
    this.drafts.clear();
    this.seen.clear();
  }
}

// Centralized, idempotent disposal also covers a stream arriving after cancellation.
export class VoiceResources {
  private disposed = false;
  private cleanups: (() => void)[] = [];
  add(cleanup: () => void) { if (this.disposed) cleanup(); else this.cleanups.push(cleanup); }
  close() { if (this.disposed) return; this.disposed = true; for (const cleanup of this.cleanups.reverse()) { try { cleanup(); } catch { /* Continue releasing other resources. */ } } this.cleanups = []; }
}
