# Cypress Bay Digestive Health — scheduling system

The far end of the Latica demonstration stand's one outbound channel: a clinic's appointment book
with an MCP face. An autopilot inside the confidential deployment calls `schedule_recall` and a
surveillance appointment is written into the diary.

It exists to make one sentence true rather than asserted:

> Outbound traffic carries only the action the clinic authorised, for the patient it authorised,
> and only from code the clinic verified. Data never leaves.

How far "code the clinic verified" reaches is in *What this does not prove* below, and it belongs
beside the sentence wherever the sentence is said.

## What it is

Stateless Streamable HTTP, the shape `apps/mcp-weather` in the agent repository uses, so what
works here works against the product's MCP client.

| Route | |
|---|---|
| `POST /mcp` | MCP. `GET` / `DELETE` → `405`: there are no sessions. Needs a grant with scope `mcp`. |
| `GET /healthz` | open |
| `GET /appointments` | the diary. Needs a grant with scope `read`. |
| `GET /journal` | the clinic's own record of every call, accepted or refused. Needs scope `read`. |

**One tool, and it writes.** `schedule_recall(recall_id, mrn, patient_name, site, due_date?,
days_overdue?, reason, outreach_channel, requested_by, signal_url?)`. There is no read tool.

**And the two read routes are a different credential.** The diary holds names and medical record
numbers and the journal holds every grant's history, so they are behind scope `read`, which the
deployment's grant does not carry. That separation is what makes "handing the deployment a token
for this is not handing it a way back into the chart" true rather than nearly true: the token it
holds opens one write tool and nothing else, and a staff token reads and cannot call a tool.

Booking is **idempotent on `recall_id`** — the same recall booked twice returns the appointment
already made (`already_booked`), which is what keeps a repeating signal out of the diary.

Both files are append-only. The journal is the clinic's half of the evidence pair: one entry says
the gate released data to this code, the other says the clinic's own system accepted a recall
from it. One mechanism, both ends.

## How a call is admitted

Two independent things, and the clinic owns both.

**The token.** Issued by the clinic, sent as `Authorization: Bearer …` on every call, and listed
in `grants.json` with the rights it carries: its **scopes**, the tools it may call and the sites
it may book at. A call naming a site outside the grant is refused `site_outside_grant`. No token,
a token the clinic does not know, or a token whose scopes do not cover the route, is `401` — and
a grant written before scopes existed reads as `["mcp"]`, never as "everything".

**The code — Pull Attestation, from the receiving end.** Before the first call of each TTL window
(`ATTESTATION_TTL_MS`, 60 s by default — so a change of code is caught within the minute, and not
re-fetched per JSON-RPC frame) the clinic fetches the caller's `/.well-known/swarm-evidence`
itself and checks:

1. the JWS header names `RS256` — an algorithm is read and insisted on, not assumed;
2. the signature verifies against a certificate chain in which **every issuer is itself a CA**
   and whose last link is the pinned root (`pinnedRootFingerprint`, the Swarm cluster's own
   `Super Swarm Root CA`), with every certificate inside its validity window;
3. the signed `hostname` is the host that was connected to;
4. the signed `certFingerprint` equals the SHA-256 of the TLS certificate **this** connection
   presented — the channel binding, which is what stops a copy of somebody else's evidence
   document being replayed from another server;
5. `evidenceDigest` equals the digest the clinic approved.

Any of them failing is `403`, and the reason is in the journal: `attestation_bad_algorithm`,
`attestation_untrusted_root`, `attestation_issuer_not_ca`, `attestation_broken_chain`,
`attestation_expired_signer`, `attestation_bad_signature`, `attestation_wrong_host`,
`attestation_channel_mismatch`, `attestation_digest_mismatch`, `attestation_unavailable`.
`src/attestation.spec.mjs` mints its own chains and asserts each refusal.

**What this does not prove, stated plainly.** It binds the approved code to the hostname the
clinic calls back, and the token to a deployment that was given it. It does not bind the
individual MCP connection to the pod that opened it — there is no client certificate on that leg,
so a holder of the token outside the enclave could present it. The inbound direction has the
channel binding the gate performs; the outbound one does not, and should not be described as
though it did. Closing it properly means either mutual TLS on the MCP leg or the caller presenting
its namespace `evidenceToken` (`Secret/swarm-pull`) for the receiver to resolve at
`<cloud>/v1/evidence/<token>`, which the platform already serves.

## Configuration

| Variable | Default | |
|---|---|---|
| `PORT` | `8931` | |
| `GRANTS_PATH` | `/etc/clinic-scheduling/grants.json` | the tokens the clinic issued, `0400` |
| `DATA_DIR` | `/var/lib/clinic-scheduling` | the diary and the journal |
| `ATTESTATION_TTL_MS` | `60000` | how long one verification stands for |

`grants.json`:

```json
{
  "pinnedRootFingerprint": "4A:FA:…:FD",
  "grants": [
    {
      "label": "latica-analyst-stand",
      "token": "…",
      "scopes": ["mcp"],
      "tools": ["schedule_recall"],
      "sites": ["Cypress Bay Endoscopy Center", "…"],
      "evidenceUrl": "https://analyst-stand.conf-apps.superprotocol.dev/.well-known/swarm-evidence",
      "approvedDigest": "sha256/05HLhdgiR5f…"
    },
    {
      "label": "cypress-bay-staff",
      "token": "…",
      "scopes": ["read"],
      "note": "The practice's own coordinators. Reads the diary and the journal, calls nothing."
    }
  ]
}
```

A third grant with a digest the clinic has stopped approving is how the refusal is shown on
camera without touching the stand.

## Running it

```bash
npm install
npm test                       # the refusals, on chains minted by the test
GRANTS_PATH=./grants.json DATA_DIR=./data node src/main.mjs
```

One verification against a live caller, which is also the CI-shaped check:

```bash
node src/verify-once.mjs \
  https://analyst-stand.conf-apps.superprotocol.dev/.well-known/swarm-evidence \
  sha256/05HLhdgiR5f… 4A:FA:…:FD
# exit 0 and the attested identity, or exit 2 and {"refused": "<reason>"}
```

One call, through the official SDK client, the way the application makes it:

```bash
MCP_URL=https://…/mcp MCP_TOKEN=… CALL='{"recall_id":"RC-0004655", …}' node src/probe.mjs
```

## Where it runs today

As the container `clinic-scheduling` on the marketplace stand's host, behind that host's Caddy on
`cypress-bay-scheduling.demo.superprotocol.dev` (A record in the `superprotocol.dev` zone,
DNS-only). The MCP server address a deployment may be pointed at must be `https://` and reachable
from the internet — a private address is refused unless the installation administrator allowlists
it — which is why it is a public name and not the clinic host's vnet address.
