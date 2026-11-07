// Pure in-memory request accounting; no HTTP, server or demo-state writes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';
const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('./simulationCache.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
const { simulationKey, createSimulationCache } = context.exports;
const inputs = { villageId: 'v1', mode: 'replay', day: 9, intervention: { type: 'canal_block', targets: ['K4'], delta_hc_cm: 60, days: 14 }, inputVersion: 'weather-and-params-a', historyVersion: 1 };
const cache = createSimulationCache();
let requests = 0;
const load = async () => ({ request: ++requests });
const key = simulationKey(inputs);
const first = await cache.get(key, load);
for (let poll = 0; poll < 5; poll++) assert.equal(await cache.get(simulationKey(inputs), load), first);
assert.equal(requests, 1, 'five unchanged 4s polls reuse one simulation');
for (const change of [
  { villageId: 'v2' }, { mode: 'live' }, { day: 10 },
  { inputVersion: 'future-forcing-changed-current-WT-identical' },
  { intervention: { ...inputs.intervention, delta_hc_cm: 50 } },
  { intervention: { ...inputs.intervention, targets: ['K5'] } },
  { intervention: { ...inputs.intervention, days: 7 } },
]) await cache.get(simulationKey({ ...inputs, ...change }), load);
assert.equal(requests, 8, 'actual input, day, village and scenario changes rerun');
assert.equal(await cache.get(simulationKey({ ...inputs, historyVersion: 2 }), load), first, 'live A6 history does not change replay simulation');
assert.equal(requests, 8, 'replay history changes reuse the cached result');
await cache.get(simulationKey({ ...inputs, mode: 'live', historyVersion: 2 }), load);
assert.equal(requests, 9, 'confirmed A6 history version changes invalidate live simulation');
assert.equal(await cache.get(key, load), first, 'remount reuses prior completed request');
assert.equal(simulationKey({ ...inputs, mode: 'live', day: 19 }), simulationKey({ ...inputs, mode: 'live', day: 0 }));
let complete;
let pendingRequests = 0;
const pending = () => { pendingRequests++; return new Promise(resolve => { complete = resolve; }); };
const consumerOne = cache.get('shared-inflight', pending);
const consumerTwo = cache.get('shared-inflight', pending);
await Promise.resolve();
assert.equal(pendingRequests, 1, 'remount while request pending shares one POST');
// First consumer may unmount; the shared request remains owned by the cache.
complete({ request: 'shared' });
assert.equal(await consumerOne, await consumerTwo);
let attempts = 0;
const flaky = async () => { if (++attempts === 1) throw new Error('temporary'); return 'recovered'; };
await assert.rejects(cache.get('retry', flaky), /temporary/);
assert.equal(await cache.get('retry', flaky), 'recovered');
assert.equal(attempts, 2, 'failed request retries next poll');
await cache.get(simulationKey({ ...inputs, inputVersion: undefined }), load);
await cache.get(simulationKey({ ...inputs, inputVersion: undefined }), load);
assert.equal(requests, 11, 'missing full-input fingerprint never trusts stale cached simulation');
const bounded = createSimulationCache(2);
let boundedRequests = 0;
const boundedLoad = async () => ++boundedRequests;
await bounded.get('a', boundedLoad); await bounded.get('b', boundedLoad);
await bounded.get('a', boundedLoad); await bounded.get('c', boundedLoad);
await bounded.get('b', boundedLoad);
assert.equal(boundedRequests, 4, 'least recently used entry evicted at capacity');
console.log('PASS: simulation request counts for unchanged polling, seven input changes, live A6 invalidation/replay reuse, completed/pending remounts, failure retry, absent fingerprint and bounded LRU');
