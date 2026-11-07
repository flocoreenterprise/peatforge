// Pure API-to-control mapping; no requests or runtime-state mutations.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';
const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('./milestones.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
const choices = context.exports.milestoneChoices;
const critical = context.exports.criticalMilestone;
const items = [
  { kind: 'pfvi_250', day: null, date: null },
  { kind: 'critical', day: 2, date: '2019-06-17' },
  { kind: 'pfvi_175', day: 31, date: '2019-07-16' },
  { kind: 'wt_60', day: 11, date: '2019-06-26' },
];
const buttons = choices(items);
assert.equal(buttons[0].day, 11, 'WT button uses API WT crossing, not combined critical day or preset');
assert.equal(buttons[0].date, '2019-06-26');
assert.equal(buttons[1].day, 31, 'PFVI Tinggi uses its own computed API crossing');
assert.equal(buttons[1].date, '2019-07-16');
assert.equal(buttons[2].day, null, 'unreached threshold disables jump');
assert.match(buttons[2].unavailable, /tidak tercapai/);
assert.equal(choices([{ kind: 'wt_60', day: 0, date: '2019-06-15' }])[0].day, 0, 'crossing on day zero stays enabled');
assert.equal(choices([{ kind: 'wt_60', day: 18, date: '2019-07-03' }])[0].day, 18, 'changed API date/day updates jump without fixed preset');
assert.equal(choices([])[0].day, null, 'missing loading/error data never enables a preset');
assert.match(choices([])[0].unavailable, /belum tersedia/);
for (const invalid of [
  { day: -1, date: '2019-06-15' }, { day: 1.5, date: '2019-06-15' },
  { day: 9, date: null }, { day: 9, date: 'invalid' },
]) assert.equal(choices([{ kind: 'wt_60', ...invalid }])[0].day, null);
assert.equal(critical(items).day, 2, 'primary critical jump follows combined API crossing even when WT is later');
const indexFirst = [{ kind: 'critical', day: 4, date: '2019-06-19' }, { kind: 'pfvi_250', day: 4, date: '2019-06-19' }, { kind: 'wt_60', day: null, date: null }];
assert.equal(critical(indexFirst).day, 4, 'PFVI-first crossing enables primary critical CTA even when WT threshold never crosses');
assert.equal(critical(indexFirst).date, '2019-06-19');
assert.equal(choices(indexFirst)[0].day, null, 'secondary WT jump remains specific and disabled when WT does not cross');
assert.equal(critical([{ kind: 'wt_60', day: 1, date: '2019-06-16' }]).day, null, 'missing combined engine milestone is never inferred from another threshold');
console.log('PASS: 19 replay milestone assertions, including PFVI-first combined critical CTA with missing WT; no API requests');
