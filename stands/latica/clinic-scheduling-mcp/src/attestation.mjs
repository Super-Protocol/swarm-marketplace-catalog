// Pull Attestation, from the receiving end.
//
// The clinic does not take the caller's word for what code is running. Before
// it accepts a recall it fetches the caller's signed Deployment Evidence from
// the caller's own hostname and checks four things:
//
//   1. the JWS is signed by a certificate chaining to the root CA the clinic
//      pinned — not merely well-formed JSON;
//   2. `hostname` in the signed payload is the host the connection was made to;
//   3. `certFingerprint` in the signed payload is the SHA-256 of the TLS
//      certificate this very connection presented — the channel binding, which
//      is what stops a copy of somebody else's evidence document being replayed
//      from another server;
//   4. `evidenceDigest` is the digest the clinic approved.
//
// Any of the four failing is a refusal. What this does NOT prove is stated in
// the README: it binds the approved code to the hostname the clinic calls back,
// not the individual MCP connection to the pod that opened it.
import { createHash, X509Certificate, verify as cryptoVerify } from 'node:crypto';
import { request as httpsRequest } from 'node:https';

export class AttestationError extends Error {
  constructor(reason, detail) {
    super(detail ? `${reason}: ${detail}` : reason);
    this.reason = reason;
    this.detail = detail ?? null;
  }
}

/** Fetch a document over TLS and return it with the leaf certificate that served it. */
function fetchWithPeerCertificate(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    const req = httpsRequest(
      {
        host: target.hostname,
        port: target.port || 443,
        path: `${target.pathname}${target.search}`,
        method: 'GET',
        servername: target.hostname,
        headers: { accept: 'application/json', 'user-agent': 'cypress-bay-scheduling/0.1' },
        timeout: timeoutMs,
        // A fresh TLS handshake per verification, deliberately. The check below
        // binds the signed evidence to the certificate THIS connection
        // presented; a connection the global agent kept alive from an earlier
        // request would hand back a certificate from a handshake that happened
        // at some other time, and on a reused socket Node does not return it at
        // all — which read as `no peer certificate` rather than as a mismatch.
        agent: false,
      },
      (res) => {
        // Read the peer certificate here, not in `end`: by then the socket has
        // been released back to the agent and `res.socket` is null.
        const peer = res.socket.getPeerCertificate();
        const peerDer = peer && peer.raw ? peer.raw : null;
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString('utf8'), peerDer });
        });
      },
    );
    req.on('timeout', () => req.destroy(new AttestationError('attestation_unavailable', 'timed out')));
    req.on('error', (err) => reject(new AttestationError('attestation_unavailable', err.message)));
    req.end();
  });
}

function sha256Base64Url(buffer) {
  return `sha256/${createHash('sha256').update(buffer).digest('base64url')}`;
}

/** The JWS, verified against a chain that must end at the pinned root. */
function verifySignedStatement(document, pinnedRootFingerprint) {
  const parts = String(document.jws ?? '').split('.');
  if (parts.length !== 3) throw new AttestationError('attestation_malformed', 'jws is not three parts');
  const chain = (document.certChain ?? []).map((pem) => new X509Certificate(pem));
  if (chain.length < 2) throw new AttestationError('attestation_malformed', 'certificate chain too short');

  const root = chain[chain.length - 1];
  if (root.fingerprint256.replace(/:/g, '').toLowerCase() !== pinnedRootFingerprint.replace(/:/g, '').toLowerCase()) {
    throw new AttestationError('attestation_untrusted_root', root.subject.replace(/\n/g, ','));
  }
  for (let i = 0; i < chain.length - 1; i += 1) {
    if (!chain[i].checkIssued(chain[i + 1]) || !chain[i].verify(chain[i + 1].publicKey)) {
      throw new AttestationError('attestation_broken_chain', `link ${i}`);
    }
  }
  const now = Date.now();
  for (const certificate of chain) {
    if (now < Date.parse(certificate.validFrom) || now > Date.parse(certificate.validTo)) {
      throw new AttestationError('attestation_expired_signer', certificate.subject.replace(/\n/g, ','));
    }
  }
  const signed = cryptoVerify(
    'RSA-SHA256',
    Buffer.from(`${parts[0]}.${parts[1]}`),
    chain[0].publicKey,
    Buffer.from(parts[2], 'base64url'),
  );
  if (!signed) throw new AttestationError('attestation_bad_signature');

  return JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
}

/**
 * Verify one caller. Returns what the journal records about the code that was
 * admitted; throws {@link AttestationError} when it must be refused.
 */
export async function verifyCaller({ evidenceUrl, approvedDigest, pinnedRootFingerprint, timeoutMs = 8000 }) {
  const expectedHost = new URL(evidenceUrl).hostname;
  const { status, body, peerDer } = await fetchWithPeerCertificate(evidenceUrl, timeoutMs);
  if (status !== 200) throw new AttestationError('attestation_unavailable', `HTTP ${status}`);

  let document;
  try {
    document = JSON.parse(body);
  } catch {
    throw new AttestationError('attestation_malformed', 'not JSON');
  }

  const payload = verifySignedStatement(document, pinnedRootFingerprint);

  if (payload.hostname !== expectedHost) {
    throw new AttestationError('attestation_wrong_host', `signed for ${payload.hostname}`);
  }
  if (!peerDer) throw new AttestationError('attestation_unavailable', 'no peer certificate');
  const observed = sha256Base64Url(peerDer);
  if (payload.certFingerprint !== observed) {
    throw new AttestationError('attestation_channel_mismatch', `observed ${observed}`);
  }
  if (payload.evidenceDigest !== approvedDigest) {
    throw new AttestationError('attestation_digest_mismatch', `observed ${payload.evidenceDigest}`);
  }

  return {
    hostname: payload.hostname,
    evidenceDigest: payload.evidenceDigest,
    certFingerprint: payload.certFingerprint,
    issuedAt: payload.issuedAt,
    signer: new X509Certificate(document.certChain[0]).subject.replace(/\n/g, ','),
    resources: Array.isArray(payload.evidence?.resources) ? payload.evidence.resources.length : null,
  };
}
