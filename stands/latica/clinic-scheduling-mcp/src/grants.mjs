// What a token the clinic issued is allowed to do.
//
// The clinic issues the token, so the clinic decides its rights, and they are
// the minimum the task needs: ONE tool, and the six sites the practice runs.
// There is no read tool at all — nothing on this server can be used to fetch a
// patient. A grant also names the code the clinic approved: the token is the
// handle, the digest is the condition.
import { readFileSync } from 'node:fs';

export function loadGrants(path) {
  const parsed = JSON.parse(readFileSync(path, 'utf8'));
  const grants = new Map();
  for (const grant of parsed.grants) {
    grants.set(grant.token, {
      label: grant.label,
      tools: grant.tools,
      sites: grant.sites,
      evidenceUrl: grant.evidenceUrl,
      approvedDigest: grant.approvedDigest,
      note: grant.note ?? null,
    });
  }
  return { grants, pinnedRootFingerprint: parsed.pinnedRootFingerprint };
}
