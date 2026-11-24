# Measuring a gated evidence digest

A listing with a data slot has to declare `evidence.expectedDigest`, and if the slot accepts an
interface the gatekeeper can serve, that number has to be the **gated** one. This is how it is
measured, in the order the steps actually have to happen, and what each step is for.

It exists because the same measurement has now been done three times — SUP-842 and SUP-870
measured ungated numbers and left the gated ones for later, SUP-895 measured the first gated one
for Confidential RAG, and SUP-900 is paying for both deferrals at once — and each round
rediscovered the same four facts about the order of operations.

## Why the gated number, not the ungated one

`evidence.expectedDigest` is not documentation. The marketplace admits a deployment to the gate
only when the listing version it was created from declares one (`gate-reconciler.service.ts`,
`clientsFor()`), and the gate then fetches that deployment's own published evidence and compares.

A deployment that mounts **gateway-mode** data does not render what an ungated one renders: the
marketplace adds the egress sidecar's own objects to it — a ConfigMap, a Deployment, a Service,
and for some listings the Secret holding its client certificate (`gatekeeper/egress-sidecar.ts`)
— and the cloud digests those too. So:

* a gated deployment and an ungated one **cannot** share a digest;
* the gate only ever compares the declared number against a **gated** deployment;
* therefore an ungated number on a gateable listing refuses every gated read of it, which is the
  only thing the slot exists for.

What that costs is small and worth saying out loud on the listing: an **ungated** deployment of
such a listing does not match the number on its own listing page. Nothing refuses it — there is
no gate in front of it to do the refusing — and the README says which configuration the digest
describes.

`apps/tests/gated_digest.py` enforces this, and because `evidence` is
`additionalProperties: false` in the specification the declaration is a comment inside the block:

```yaml
evidence:
  # evidence-configuration: gated
  expectedDigest: "…"
```

## Before the card is raised

GPU time is the expensive part of this, so everything that is not a deployment is done first.
SUP-870 measured the saving at 59% on one listing.

1. **A gated dataset of the right shape has to exist, with rows in it.** `access.mode: gateway`
   and a `clickhouse-http/v1` connection; anything else cannot be gated whatever its access mode
   says. See `datasets/kestrel-pbmc-panel` and `datasets/cascade-card-transactions` for the two
   shapes, and their READMEs for how the server, the scoped reader account and the rows are set
   up on the stand.
2. **The dataset has to be published AND granted** — to the owner's organisation and to the
   organisation that will own the deployment. `isBindable` wants a grant to the **consuming**
   organisation, which on the marketplace stand is `big-pharma`; a compatible-but-ungranted
   dataset shows on the configure form and cannot be bound.
3. **The listing version that will be deployed has to be the published one.** A reconfigure
   re-renders the version a deployment was *created* from, so a newer listing reaches only new
   deployments.
4. **The reference parameter set has to be decided and written down**, in the listing's own
   evidence comment. Everything the operator chooses is attested on purpose, so the digest is a
   property of the version *and* the answers; a parameter decided at the keyboard is a window
   spent deciding.
5. **Check what the chart renders is unchanged.** If the gated number is being measured for a
   version whose render is identical to the published one — a comment or a digest edit only —
   the two measurement deployments can be created from the **published** version, and the new
   version only has to carry the number afterwards.

## The window

The card is serial. Creating a second deployment while the first is live is refused outright —
`400 No cluster has a free GPU of an accepted type: NVIDIA H100 NVL, NVIDIA H200 NVL` — so a
pair is measured in sequence on one card, not side by side. That refusal is a good one: it is
fast and legible, rather than a deployment that comes up with a Pending engine and attests
perfectly well anyway.

**Two deployments per listing, and the second one is also the proof.** The temptation is three —
a pair to measure, then a third created from the listing that carries the number, to show the
gate admitting it. That third deployment is avoidable, because the render of version N and
version N+1 is identical when the only change between them is the declared digest and its
comments. So the pair is measured *across* the version bump, and the second half of the pair is
the deployment the gate admits:

1. **Deploy A** from the published version N, with the gated dataset bound, on its own hostname.
   Poll `GET …/cluster-spaces/<id>/hostnames` until it names every hostname you expect before
   writing DNS — the ingress-sync worker runs once a minute and the list answers `[]` until it
   has. Then write the records, poll until each reads `verified: true, delegated: true`, and
   publish.
2. **Read A's evidence**: `GET <cloud baseUrl>/api/v1/deployments/<cloudDeploymentId>/evidence`
   → `{ digest, jws, snapshot, matchesExpected }`. Keep the whole snapshot, not the digest. It
   will read `matchesExpected: false`, because N still declares the ungated number — and A's
   gated read will be **refused**, which is worth recording: it is the defect being fixed,
   observed.
3. **Declare the number** as version N+1: the digest, `# evidence-configuration: gated`, the
   reference parameter set, and the previous value recorded as the ungated one. Merge, and seed
   N+1 to the stand.
4. **Delete A**, which frees the card, then **deploy B from N+1** — a different hostname, a
   different namespace, its own generated credentials, the same dataset and the same answers.
5. **B's evidence is the whole result.** Its digest must equal A's, and `matchesExpected` must
   now read `true`. Compare the two snapshots **field by field**, as JSON Pointers, and not only
   by digest: an equal digest is one comparison, and a field-by-field diff is what makes the
   exclusion set demonstrably complete. Record the canonical document's byte length and the
   resource count and kinds.

Measuring across the bump is a stronger claim than measuring twice on one version, not a weaker
one: it shows the digest is a property of the render rather than of the listing version it was
declared in. `apps/confidential-rag/app.yaml` records the same thing the expensive way, with a
third deployment.

If the render is *not* identical across the bump — a chart pin, an image, a parameter — then the
pair has to be measured on one version and the gate shown separately. Two ways, and the first is
cheaper: make the published version carry the measured digest (back up the database, then
replace the version row's definition), because the gate re-reads it live and admits a deployment
already running; or spend a third deployment.

## Making the gate actually admit it, and proving the read

The gate reads the declared digest **live**, from the `definitionJson` of the version the
deployment was created from, on every reconcile (30 s) — `declaredDigestOf(version)` in
`deployments.service.ts`. So nothing has to be recreated for a number to take effect.

What does have to be waited for is admission. It is **not** instant: for about eight minutes
after publication the read fails with a `502` from the deployment's own egress sidecar, because
the gate has not yet been told about the client and the sidecar has nothing upstream. A read
taken once, early, reads as a failure of the whole exercise.

Then read through the gate from inside the deployment — the notebook cell, the indexer, the
thing the listing exists to do — and record what came back. `matchesExpected: true` on the
evidence endpoint is the marketplace agreeing with itself; rows arriving in the application is
the claim.

## What to record

* The digest, the resource count and kinds, and the canonical snapshot's byte length.
* That the pair was byte-identical, and how that was established.
* The reference parameter set, including which dataset was bound and that it was gated.
* The other configuration's number, named as such, so nobody re-measures it to find out.
* GPU hours and dollars. The card is shared and serial, and the next person needs to know what
  the window cost.
