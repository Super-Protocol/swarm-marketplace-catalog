// The grant file decides who may do what, so a mistake in it is a mistake in
// the access rules. These are the three ways it can be wrong.
//
//   node --test src/grants.spec.mjs
import { strict as assert } from 'node:assert';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, it } from 'node:test';
import { loadGrants } from './grants.mjs';

let dir;
const write = (name, doc) => {
  const path = join(dir, name);
  writeFileSync(path, JSON.stringify(doc));
  return path;
};

const file = (grants) => ({ pinnedRootFingerprint: 'AA:BB', grants });

describe('the grants a clinic issues', () => {
  before(() => { dir = mkdtempSync(join(tmpdir(), 'cbdh-grants-')); });
  after(() => rmSync(dir, { recursive: true, force: true }));

  it('reads a caller and a reader as two different holders', () => {
    const { grants, pinnedRootFingerprint } = loadGrants(write('two.json', file([
      { label: 'deployment', token: 'a', scopes: ['mcp'], tools: ['schedule_recall'], sites: ['One'] },
      { label: 'staff', token: 'b', scopes: ['read'] },
    ])));
    assert.equal(pinnedRootFingerprint, 'AA:BB');
    assert.deepEqual(grants.get('a').scopes, ['mcp']);
    assert.deepEqual(grants.get('b').scopes, ['read']);
    // The point of the split: neither holder has the other's right.
    assert.ok(!grants.get('a').scopes.includes('read'));
    assert.ok(!grants.get('b').scopes.includes('mcp'));
  });

  it('treats a grant written before scopes existed as a caller, not as everything', () => {
    const { grants } = loadGrants(write('old.json', file([
      { label: 'legacy', token: 'a', tools: ['schedule_recall'], sites: ['One'] },
    ])));
    assert.deepEqual(grants.get('a').scopes, ['mcp']);
  });

  it('refuses a scope nobody defined rather than ignoring it', () => {
    assert.throws(
      () => loadGrants(write('typo.json', file([{ label: 'oops', token: 'a', scopes: ['readonly'] }]))),
      /unknown scope "readonly"/,
    );
  });
});
