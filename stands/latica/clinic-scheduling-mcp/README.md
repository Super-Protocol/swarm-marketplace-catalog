# Cypress Bay Digestive Health — scheduling system

The far end of the Latica demonstration stand's one outbound channel: a clinic's appointment book
with an MCP face. An autopilot inside the confidential deployment calls `schedule_recall` and a
surveillance appointment is written into the diary.

It exists to make one sentence true rather than asserted:

> Outbound traffic carries only the action the clinic authorised, for the patient it authorised,
> and only from code the clinic verified. Data never leaves.

## What it is

Stateless Streamable HTTP, the shape `apps/mcp-weather` in the agent repository uses, so what
works here works against the product's MCP client.

| Route | |
|---|---|
| `POST /mcp` | MCP. `GET` / `DELETE` → `405`: there are no sessions. |
| `GET /healthz` | open |
| `GET /appointments` | the diary, for a token holder |
| `GET /journal` | the clinic's own record of every call, accepted or refused, for a token holder |

**One tool, and it writes.** `schedule_recall(recall_id, mrn, patient_name, site, due_date?,
days_overdue?, reason, outreach_channel, requested_by, signal_url?)`. There is no read tool:
nothing here can be used to fetch a patient record, which is why handing the deployment a token
for it is not handing it a way back into the chart.

Booking is **idempotent on `recall_id`** — the same recall booked twice returns the appointment
already made (`already_booked`), which is what keeps a repeating signal out of the diary.

Both files are append-only. The journal is the clinic's half of the evidence pair: one entry says
the gate released data to this code, the other says the clinic's own system accepted a recall
from it. One mechanism, both ends.

## How a call is admitted

Two independent things, and the clinic owns both.

**The token.** Issued by the clinic, sent as `Authorization: Bearer …` on every call, and listed
in `grants.json` with the rights it carries: the tools it may call and the sites it may book at.
A call naming a site outside the grant is refused `site_outside_grant`. No token, or a token the
clinic does not know, is `401`.

**The code — Pull Attestation, from the receiving end.** Before the first call of each TTL window
the clinic fetches the caller's `/.well-known/swarm-evidence` itself and checks four things:

1. the JWS verifies against a certificate chain ending at the pinned root
   (`pinnedRootFingerprint`, the Swarm cluster's own `Super Swarm Root CA`);
2. the signed `hostname` is the host that was connected to;
3. the signed `certFingerprint` equals the SHA-256 of the TLS certificate **this** connection
   presented — the channel binding, which is what stops a copy of somebody else's evidence
   document being replayed from another server;
4. `evidenceDigest` equals the digest the clinic approved.

Any of the four failing is `403`, and the reason is in the journal: `attestation_untrusted_root`,
`attestation_broken_chain`, `attestation_expired_signer`, `attestation_bad_signature`,
`attestation_wrong_host`, `attestation_channel_mismatch`, `attestation_digest_mismatch`,
`attestation_unavailable`.

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
      "tools": ["schedule_recall"],
      "sites": ["Cypress Bay Endoscopy Center", "…"],
      "evidenceUrl": "https://analyst-stand.conf-apps.superprotocol.dev/.well-known/swarm-evidence",
      "approvedDigest": "sha256/05HLhdgiR5f…"
    }
  ]
}
```

A second grant with a digest the clinic has stopped approving is how the refusal is shown on
camera without touching the stand.

## Running it

```bash
npm install
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
