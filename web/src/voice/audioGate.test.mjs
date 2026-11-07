// Pure fake RTP recorder and playback: never accesses a microphone or network.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';
const timers = new Set();
const trackTimeout = (fn, delay) => { const id = setTimeout(() => { timers.delete(id); fn(); }, delay); timers.add(id); return id; };
const trackClear = id => { timers.delete(id); clearTimeout(id); };
const context = { exports: {}, setTimeout: trackTimeout, clearTimeout: trackClear, Blob };
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('./audioGate.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
const { ValidatedAudioGate } = context.exports;
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
function fixture(timeout = 1000) {
  const log = [], recorders = [], urls = [], revoked = [];
  let finishPlayback;
  const gate = new ValidatedAudioGate({
    recorder: () => {
      const recorder = { state: 'inactive', ondataavailable: null, onstop: null, onerror: null,
        start() { this.state = 'recording'; log.push('record-start'); },
        stop() { this.state = 'inactive'; log.push('record-stop'); const callback = this.onstop; queueMicrotask(() => callback?.()); },
      }; recorders.push(recorder); return recorder;
    },
    send: () => log.push('response.create'),
    play: url => { log.push(`play:${url}`); return new Promise(resolve => { finishPlayback = resolve; }); },
    stopPlayback: () => { log.push('playback-stop'); finishPlayback?.(); },
    url: blob => { assert.ok(blob.size); const url = `blob:fake-${urls.length}`; urls.push(url); return url; },
    revoke: url => revoked.push(url), busy: value => log.push(`busy:${value}`), fail: () => log.push('failed'), settled: () => log.push('settled'),
  }, timeout);
  return { gate, log, recorders, urls, revoked, finish: () => finishPlayback?.() };
}
function response(f, id = 'r1') {
  f.gate.handle({ type: 'response.created', response: { id } });
  f.gate.handle({ type: 'output_audio_buffer.started', response_id: id });
  f.recorders.at(-1).ondataavailable({ data: new Blob(['provider audio'], { type: 'audio/webm' }) });
  f.gate.handle({ type: 'response.output_audio_transcript.done', response_id: id, item_id: `${id}-item` });
  f.gate.handle({ type: 'response.done', response: { id, status: 'completed', output: [{ type: 'message' }] } });
}
const approved = fixture();
approved.gate.streamReady(); approved.gate.request();
assert.ok(approved.log.indexOf('record-start') < approved.log.indexOf('response.create'), 'capture begins before generation request');
response(approved);
approved.gate.approved('r1', 'r1-item');
await tick(); assert.equal(approved.urls.length, 0, 'ACK alone cannot release audio before provider drained');
approved.gate.handle({ type: 'output_audio_buffer.stopped', response_id: 'r1' });
await tick(); assert.equal(approved.urls.length, 1); assert.equal(approved.log.filter(x => x.startsWith('play:')).length, 1, 'approved matching response plays once');
approved.gate.request(); await tick(); assert.equal(approved.recorders.length, 1, 'next generation waits until validated playback ends');
approved.finish(); await tick(); assert.equal(approved.revoked.join(','), approved.urls.join(',')); assert.equal(approved.recorders.length, 2);
approved.gate.close();

const rejected = fixture(); rejected.gate.streamReady(); rejected.gate.request(); response(rejected);
rejected.gate.handle({ type: 'output_audio_buffer.stopped', response_id: 'r1' }); rejected.gate.reject(); await tick();
assert.equal(rejected.urls.length, 0, 'rejected transcript NEVER becomes a playable blob URL');
assert.equal(rejected.log.filter(x => x.startsWith('play:')).length, 0, 'rejected response NEVER plays');
rejected.gate.request(); assert.equal(rejected.recorders.length, 1, 'rejected response closes gate; rejected tail cannot enter another recording');

const unknown = fixture(); unknown.gate.streamReady(); unknown.gate.request(); response(unknown);
unknown.gate.approved('other-response', 'r1-item'); await tick();
assert.equal(unknown.urls.length, 0); assert.ok(unknown.log.includes('failed'), 'mismatched response ACK closes gate');

const cleanup = fixture(); cleanup.gate.streamReady(); cleanup.gate.request(); response(cleanup); cleanup.gate.approved('r1', 'r1-item'); cleanup.gate.handle({ type: 'output_audio_buffer.stopped', response_id: 'r1' }); await tick();
cleanup.gate.close(); cleanup.gate.close(); await tick(); assert.equal(cleanup.revoked.length, 1, 'cleanup revokes each approved object URL once');

const toolOnly = fixture(); toolOnly.gate.streamReady(); toolOnly.gate.request(); toolOnly.gate.request();
toolOnly.gate.handle({ type: 'response.created', response: { id: 'tool' } });
toolOnly.gate.handle({ type: 'response.done', response: { id: 'tool', status: 'completed', output: [{ type: 'function_call' }] } });
await tick(); assert.equal(toolOnly.urls.length, 0); assert.equal(toolOnly.recorders.length, 2, 'tool-only response discards capture and advances queue'); toolOnly.gate.close();

const interrupted = fixture(); interrupted.gate.streamReady(); interrupted.gate.request();
interrupted.gate.handle({ type: 'input_audio_buffer.speech_started', item_id: 'barge-in' });
assert.ok(interrupted.log.includes('failed')); assert.equal(interrupted.urls.length, 0, 'barge-in discards entire response');

const timed = fixture(10); timed.gate.streamReady(); timed.gate.request(); response(timed); timed.gate.handle({ type: 'output_audio_buffer.stopped', response_id: 'r1' });
await new Promise(resolve => setTimeout(resolve, 25)); assert.equal(timed.urls.length, 0); assert.ok(timed.log.includes('failed'), 'unacknowledged response expires without playback');
assert.equal(timers.size, 0, 'closing pending approved playback settles its promise and clears every capture/playback timer');
console.log('PASS: recorder-before-generation, matched ACK+completed+drained gate, approved plays once, rejected NEVER plays, serialized playback, tool-only response, mismatch/barge-in/timeout fail-closed, blob cleanup');
