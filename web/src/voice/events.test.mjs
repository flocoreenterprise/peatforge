// Offline fakes only: no browser microphone, network, provider, or demo-state writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';
const context = { exports: {}, setTimeout, clearTimeout };
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('./events.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
const { VoiceEvents, VoiceResources } = context.exports;
const pause = () => new Promise(resolve => setTimeout(resolve, 0));
let acknowledge;
const order = [], outgoing = [], rendered = [];
let ended = 0;
const events = new VoiceEvents({
  persist: async entry => { order.push(`persist:${entry.role}`); if (entry.role === 'user') await new Promise(resolve => { acknowledge = resolve; }); order.push(`ack:${entry.role}`); return { accepted: true }; },
  tool: async (id, name, args) => { order.push(`tool:${name}`); assert.equal(Object.keys(args).length, 0); return { accepted: true }; },
  send: event => outgoing.push(event), transcript: entry => rendered.push(entry), ended: () => { ended++; },
});
await events.handle({ type: 'input_audio_buffer.speech_started' });
const tool = events.handle({ type: 'response.function_call_arguments.done', call_id: 'c1', name: 'record_report', arguments: '{}' });
await pause(); assert.equal(outgoing.length, 0, 'early function call waits for final input transcription');
const transcription = events.handle({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'u1', transcript: 'air enam puluh' });
await pause(); assert.equal(order.join(','), 'persist:user', 'tool waits for server persistence ACK');
acknowledge(); await transcription; await tool;
assert.equal(order.join(','), 'persist:user,ack:user,tool:record_report');
assert.equal(outgoing[0].type, 'response.create', 'manual response starts only after user transcript ACK');
assert.equal(outgoing[1].item.type, 'function_call_output'); assert.equal(outgoing[1].item.call_id, 'c1');
assert.equal(outgoing[2].type, 'response.create');
await events.handle({ type: 'response.done', response: { output: [{ type: 'function_call', call_id: 'c1', name: 'record_report', arguments: '{}' }] } });
assert.equal(order.filter(item => item === 'tool:record_report').length, 1, 'duplicate tool events execute once');
await events.handle({ type: 'response.output_audio_transcript.delta', item_id: 'a1', delta: 'Mohon ' });
assert.equal(rendered.length, 1, 'unvalidated assistant partials are not displayed');
await events.handle({ type: 'response.output_audio_transcript.done', item_id: 'a1', transcript: 'Mohon konfirmasi.' });
assert.equal(rendered[1].text, 'Mohon konfirmasi.');
await events.handle({ type: 'response.output_audio_transcript.done', item_id: 'a1', transcript: 'Mohon konfirmasi.' });
assert.equal(rendered.length, 2, 'duplicate final transcript is persisted/displayed once');
await events.handle({ type: 'response.function_call_arguments.done', call_id: 'c2', name: 'end_call', arguments: '{}' });
assert.equal(ended, 0, 'end tool leaves time for final spoken response');
await events.handle({ type: 'output_audio_buffer.stopped' }); assert.equal(ended, 0, 'provider drain alone never ends before validated playback');
await events.handle({ type: 'response.output_audio_transcript.done', item_id: 'thanks', transcript: 'Terima kasih.' });
events.playbackSettled(); assert.equal(ended, 1);
events.close();
await events.handle({ type: 'response.function_call_arguments.done', call_id: 'c3', name: 'record_report', arguments: '{}' });
assert.equal(outgoing.length, 5, 'closed event handler cannot mutate pipeline');

const rejected = new VoiceEvents({ persist: async () => { throw new Error('unsafe'); }, tool: async () => { throw new Error('must not execute'); }, send: () => {}, transcript: () => { throw new Error('must not display'); }, ended: () => {} });
await assert.rejects(rejected.handle({ type: 'response.output_audio_transcript.done', item_id: 'unsafe', transcript: 'unsafe digits 999' }), /unsafe/);
await assert.rejects(rejected.handle({ type: 'response.function_call_arguments.done', call_id: 'unsafe-tool', name: 'record_report', arguments: '{}' }), /unsafe/);
rejected.close();
const unsafeAck = new VoiceEvents({ persist: async () => ({ accepted: false, reason: 'numeric_guard' }), tool: async () => {}, send: () => {}, transcript: () => { throw new Error('must not display rejected numbers'); }, ended: () => {} });
await assert.rejects(unsafeAck.handle({ type: 'response.output_audio_transcript.done', item_id: 'rejected-ack', transcript: 'angka tidak sah' }), /diverifikasi/);
unsafeAck.close();
for (const ack of [undefined, null, {}, { accepted: 'true' }, { accepted: 1 }]) {
  let displayed = false, approved = false;
  const malformed = new VoiceEvents({ persist: async () => ack, tool: async () => {}, send: () => {}, transcript: () => { displayed = true; }, validated: () => { approved = true; }, ended: () => {} });
  await assert.rejects(malformed.handle({ type: 'response.output_audio_transcript.done', response_id: 'bad-response', item_id: 'bad-ack', transcript: 'Belum terverifikasi.' }), /diverifikasi/);
  assert.equal(displayed, false, 'malformed ACK never displays assistant transcript');
  assert.equal(approved, false, 'malformed ACK never releases recorded audio');
  malformed.close();
}
const noInput = new VoiceEvents({ persist: async () => {}, tool: async () => { throw new Error('must not execute'); }, send: () => {}, transcript: () => {}, ended: () => {} });
await assert.rejects(noInput.handle({ type: 'response.function_call_arguments.done', call_id: 'missing', name: 'confirm_report', arguments: '{}' }), /Transkripsi/);
noInput.close();

const overlapOrder = [];
const overlap = new VoiceEvents({ persist: async entry => { overlapOrder.push(entry.id); return { accepted: true }; }, tool: async () => { overlapOrder.push('tool'); return {}; }, send: () => {}, transcript: () => {}, ended: () => {} });
await overlap.handle({ type: 'input_audio_buffer.speech_started', item_id: 'turn-one' });
await overlap.handle({ type: 'input_audio_buffer.committed', item_id: 'turn-one' });
await overlap.handle({ type: 'input_audio_buffer.speech_started', item_id: 'turn-two' });
const overlappingTool = overlap.handle({ type: 'response.function_call_arguments.done', call_id: 'overlap', name: 'record_report', arguments: '{}' });
await overlap.handle({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'turn-one', transcript: 'laporan pertama' });
await pause(); assert.equal(overlapOrder.join(','), 'turn-one', 'a second pending audio turn still blocks mutation');
await overlap.handle({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'turn-two', transcript: 'koreksi laporan' });
await overlappingTool; assert.equal(overlapOrder.join(','), 'turn-one,turn-two,tool');
overlap.close();

const cancelOrder = [], resources = new VoiceResources();
resources.add(() => cancelOrder.push('stream-stopped')); resources.add(() => { cancelOrder.push('connection-closed'); throw new Error('disposal continues'); }); resources.add(() => cancelOrder.push('events-closed'));
resources.close(); resources.close(); resources.add(() => cancelOrder.push('late-stream-stopped'));
assert.equal(cancelOrder.join(','), 'events-closed,connection-closed,stream-stopped,late-stream-stopped', 'idempotent cleanup releases late microphone tracks and survives individual errors');

let releaseTool;
const cancelSend = [];
const cancel = new VoiceEvents({ persist: async () => {}, tool: async () => new Promise(resolve => { releaseTool = resolve; }), send: event => cancelSend.push(event), transcript: () => {}, ended: () => {} });
const inflight = cancel.handle({ type: 'response.function_call_arguments.done', call_id: 'status', name: 'get_village_status', arguments: '{}' });
await pause(); cancel.close(); releaseTool({ status: 'modelled' }); await inflight;
assert.equal(cancelSend.length, 0, 'late server response after scope/unmount cannot write to data channel');
console.log('PASS: transcription/tool ACK ordering, tool deduplication, validated final transcript UI, end-call playback, unsafe transcript fail-closed, absent user transcript, disposal and late-response cleanup');
