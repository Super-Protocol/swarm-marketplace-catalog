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
| `GET /appointments` | the diary, as JSON. Needs a grant with scope `read`. |
| `GET /journal` | the clinic's own record of every call, accepted or refused, as JSON. Needs scope `read`. |
| `GET /`, `GET /staff/appointments`, `GET /staff/journal` | **the screen** — the same two things, for a coordinator rather than a `curl`. Same scope `read`, through a sign-in form and a session cookie. |
| `POST /staff/session`, `POST /staff/sign-out` | sign in and out |

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

## The screen

`src/staff.mjs`. Two views over the same two files, for the practice's own coordinators:
**Appointments**, the diary — patient, site, how overdue, the appointment, when it was booked and
by whom, with the digest each booking was accepted from; and **Journal**, every call with its
attestation verdict, the digest the caller presented and, where they differ, the digest the clinic
approved. Refusals are in the journal beside the acceptances and coloured as refusals, which is
the half of the mechanism a screen showing only successes would hide.

It is **read-only and has no route that writes**. A window that could also book would be a second
way into the scheduling system, and the demonstration does not need one.

Three decisions worth knowing before changing it:

- **It is not HTTP Basic.** A browser fetches some subresources in a mode where it attaches no
  credentials, so the prompt returns forever; and ingress-nginx strips `Authorization` when it
  performs basic auth itself. So the credential is collected once by a form and carried in a
  session cookie — `HttpOnly`, `SameSite=Strict`, `Secure` when the connection was TLS (read from
  `X-Forwarded-Proto`, because the TLS ends at Caddy), idle timeout `STAFF_SESSION_TTL_MS`.
  Sessions are in memory: a restart asks for the token again, which is the safe way to fail.
- **The credential check is `grants.mjs` and nothing else.** A session is only ever opened from a
  token that already carries scope `read`, so the screen cannot show anything its holder could not
  already fetch from the JSON routes, and the deployment's `mcp` token is refused at the form.
- **Server-rendered, no client JavaScript, no subresources** — the stylesheet is inline. The
  response carries `default-src 'none'`, `frame-ancestors 'none'`, `no-store` and `no-referrer`,
  because the pages hold patient names.

A refused sign-in is recorded in the journal as `no_staff_credential`; a successful one is not.
The journal is the record of *calls*, and page views would bury the entries the demonstration
turns on.

**What the screen says about its own limits**, because the claim must not outrun the mechanism and
a screen is where an overclaim does the most damage: the Journal view carries, in the page itself,
that the check binds the approved code to the **hostname** and the **token** binds the individual
call; that the code is re-checked **once per 60-second window**, not per call; and that the
deployment's outbound destinations are **not** all part of its attested configuration today. The
wording is asserted by `src/staff.spec.mjs` — so is the absence of "nothing leaves".

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
| `STAFF_SESSION_TTL_MS` | `43200000` | how long a staff screen session survives being idle |
| `STAFF_JOURNAL_ROWS` | `200` | how many journal lines one page shows, newest first |

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
npm test                       # the refusals, the scopes, the screen — 49 assertions
GRANTS_PATH=./grants.json DATA_DIR=./data node src/main.mjs
```

`npm test` is four files: `attestation.spec.mjs` mints its own certificate chains and asserts
each refusal; `grants.spec.mjs` covers the three ways a grants file can be wrong; `staff.spec.mjs`
covers the session, the cookie and what each page is required to say; and `server.spec.mjs` starts
`main.mjs` as a child on a free port with its own grants file and data directory and asserts the
scopes end to end — the deployment's token still gets `401` on the read routes, the staff token
still gets `401` on `/mcp`.

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
