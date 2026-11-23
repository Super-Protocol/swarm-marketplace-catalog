// One pull-attestation check against a live caller, for the runbook and for CI.
import { verifyCaller, AttestationError } from './attestation.mjs';

const [evidenceUrl, approvedDigest, root] = process.argv.slice(2);
try {
  console.log(JSON.stringify(await verifyCaller({ evidenceUrl, approvedDigest, pinnedRootFingerprint: root }), null, 1));
} catch (error) {
  if (error instanceof AttestationError) {
    console.log(JSON.stringify({ refused: error.reason, detail: error.detail }, null, 1));
    process.exit(2);
  }
  throw error;
}
