// What the clinic refuses, on chains minted here rather than on the one real
// document — every one of these is a document somebody could serve, and the
// point of the check is that none of them is accepted.
//
//   node --test src/attestation.spec.mjs
import { strict as assert } from 'node:assert';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { X509Certificate, createPrivateKey, sign as cryptoSign } from 'node:crypto';
import { after, before, describe, it } from 'node:test';
import { AttestationError, verifySignedStatement } from './attestation.mjs';

let dir;
/** A certificate and its key, as PEM. */
const pem = {};

/** openssl, because minting an X.509 chain by hand in Node is not a test. */
function openssl(...args) {
  execFileSync('openssl', args, { cwd: dir, stdio: ['ignore', 'pipe', 'pipe'] });
}

function mint(name, { ca, issuer }) {
  openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
    '-keyout', `${name}.key`, '-out', `${name}.selfsigned.pem`, '-subj', `/CN=${name}`);
  if (!issuer) {
    pem[name] = readFileSync(join(dir, `${name}.selfsigned.pem`), 'utf8');
    return;
  }
  writeFileSync(join(dir, `${name}.ext`), `basicConstraints=critical,CA:${ca ? 'TRUE' : 'FALSE'}\n`);
  openssl('req', '-new', '-key', `${name}.key`, '-out', `${name}.csr`, '-subj', `/CN=${name}`);
  openssl('x509', '-req', '-in', `${name}.csr`, '-CA', `${issuer}.pem`, '-CAkey', `${issuer}.key`,
    '-CAcreateserial', '-days', '2', '-out', `${name}.pem`, '-extfile', `${name}.ext`);
  pem[name] = readFileSync(join(dir, `${name}.pem`), 'utf8');
}

/** A `DeploymentEvidence` document signed by `signerName`'s key. */
function document({ chain, signerName, alg = 'RS256', payload = {} }) {
  const header = Buffer.from(JSON.stringify({ alg }), 'utf8').toString('base64url');
  const body = Buffer.from(JSON.stringify({
    version: '1', kind: 'DeploymentEvidence', hostname: 'stand.example',
    certFingerprint: 'sha256/x', evidenceDigest: 'sha256/y', ...payload,
  }), 'utf8').toString('base64url');
  const signature = cryptoSign('RSA-SHA256', Buffer.from(`${header}.${body}`),
    createPrivateKey(readFileSync(join(dir, `${signerName}.key`)))).toString('base64url');
  return { jws: `${header}.${body}.${signature}`, certChain: chain };
}

const fingerprintOf = (certPem) => new X509Certificate(certPem).fingerprint256;

function refusal(fn) {
  try { fn(); } catch (error) {
    assert.ok(error instanceof AttestationError, `expected an AttestationError, got ${error}`);
    return error.reason;
  }
  return assert.fail('expected a refusal');
}

describe('the clinic verifying a caller\'s signed evidence', () => {
  before(() => {
    dir = mkdtempSync(join(tmpdir(), 'cbdh-attest-'));
    mint('root', { ca: true });
    // `req -x509` already writes a CA; copy it to the name the signer step wants.
    writeFileSync(join(dir, 'root.pem'), readFileSync(join(dir, 'root.selfsigned.pem')));
    mint('subroot', { ca: true, issuer: 'root' });
    mint('leaf', { ca: false, issuer: 'subroot' });
    mint('signedbyleaf', { ca: false, issuer: 'leaf' });
  });
  after(() => rmSync(dir, { recursive: true, force: true }));

  it('accepts a chain that ends at the pinned root', () => {
    const payload = verifySignedStatement(
      document({ chain: [pem.subroot, pem.root], signerName: 'subroot' }),
      fingerprintOf(pem.root),
    );
    assert.equal(payload.hostname, 'stand.example');
  });

  it('refuses a root it was not told to trust', () => {
    assert.equal(
      refusal(() => verifySignedStatement(
        document({ chain: [pem.subroot, pem.root], signerName: 'subroot' }),
        fingerprintOf(pem.leaf),
      )),
      'attestation_untrusted_root',
    );
  });

  it('refuses an issuer that is not a CA', () => {
    // leaf is CA:FALSE, so it may not stand as the issuer of anything, even
    // though it did in fact sign `signedbyleaf`.
    assert.equal(
      refusal(() => verifySignedStatement(
        document({ chain: [pem.signedbyleaf, pem.leaf], signerName: 'signedbyleaf' }),
        fingerprintOf(pem.leaf),
      )),
      'attestation_issuer_not_ca',
    );
  });

  it('refuses a chain whose links do not sign each other', () => {
    assert.equal(
      refusal(() => verifySignedStatement(
        document({ chain: [pem.leaf, pem.root], signerName: 'leaf' }),
        fingerprintOf(pem.root),
      )),
      'attestation_broken_chain',
    );
  });

  it('refuses a header that names another algorithm', () => {
    for (const alg of ['none', 'HS256', 'RS512']) {
      assert.equal(
        refusal(() => verifySignedStatement(
          document({ chain: [pem.subroot, pem.root], signerName: 'subroot', alg }),
          fingerprintOf(pem.root),
        )),
        'attestation_bad_algorithm',
        `alg=${alg}`,
      );
    }
  });

  it('refuses a signature made by a key that is not the signer certificate\'s', () => {
    const forged = document({ chain: [pem.subroot, pem.root], signerName: 'leaf' });
    assert.equal(refusal(() => verifySignedStatement(forged, fingerprintOf(pem.root))), 'attestation_bad_signature');
  });

  it('refuses a chain too short to have an issuer', () => {
    assert.equal(
      refusal(() => verifySignedStatement(
        document({ chain: [pem.root], signerName: 'root' }),
        fingerprintOf(pem.root),
      )),
      'attestation_malformed',
    );
  });
});
