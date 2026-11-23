// What a token the clinic issued is allowed to do.
//
// The clinic issues the token, so the clinic decides its rights, and they are
// the minimum each holder needs. Two kinds of holder, and they do not overlap:
//
//   `scopes: ["mcp"]`  — the deployment. It may call `schedule_recall` at the
//                        sites named in the grant and nothing else. It cannot
//                        read the diary or the journal, so the token it holds
//                        is not a way back into a chart.
//   `scopes: ["read"]` — the practice's own staff. They read `/appointments`
//                        and `/journal` and may not call a tool.
//
// A grant also names the code the clinic approved: the token is the handle,
// the digest is the condition.
import { readFileSync } from 'node:fs';

/** Everything a grant may be allowed to do; anything else in a file is a typo. */
export const SCOPES = ['mcp', 'read'];

export function loadGrants(path) {
  const parsed = JSON.parse(readFileSync(path, 'utf8'));
  const grants = new Map();
  for (const grant of parsed.grants) {
    // Absent `scopes` is not "everything": an old file without the field is a
    // caller grant, which is what every grant was before staff reads existed.
    const scopes = grant.scopes ?? ['mcp'];
    for (const scope of scopes) {
      if (!SCOPES.includes(scope)) {
        throw new Error(`grant "${grant.label}" names an unknown scope "${scope}"`);
      }
    }
    grants.set(grant.token, {
      label: grant.label,
      scopes,
      tools: grant.tools ?? [],
      sites: grant.sites ?? [],
      evidenceUrl: grant.evidenceUrl ?? null,
      approvedDigest: grant.approvedDigest ?? null,
      note: grant.note ?? null,
    });
  }
  return { grants, pinnedRootFingerprint: parsed.pinnedRootFingerprint };
}
