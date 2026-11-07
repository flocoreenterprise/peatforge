import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import postcss from 'postcss';
const css = postcss.parse(readFileSync(new URL('../layout/command.css', import.meta.url), 'utf8'));
const declarations = new Map();
css.walkRules(rule => {
  if (rule.selector === '.command-v2 .command-map .village-marker.maplibregl-marker') rule.walkDecls(declaration => declarations.set(declaration.prop, declaration.value));
});
assert.equal(declarations.get('position'), 'absolute', 'MapLibre projected markers must stay out of normal flow; relative buttons add an accumulating 44px offset');
assert.equal(declarations.get('top'), '0');
assert.equal(declarations.get('left'), '0');
console.log('PASS: projected village buttons retain absolute MapLibre positioning with no normal-flow offsets');
