# verify-pins

Every first-party image this catalog pins is traced from its digest to the source
revision an attestation says built it. `.github/workflows/verify-pins.yml` runs it on
every pull request touching `charts/**` or `apps/**`, and on every push to `main`.

A listing version reaches production by being merged to `main`: `publish-charts.yml`
packages the charts, and a deployment pulls the images those charts pin by digest. The
provenance of those digests is the whole difference between "the train we reviewed" and
"whatever was in the registry" — and until this existed it was a sentence in a pull
request body, verified by whoever read it and by nobody when nobody did. Listing 0.18.3
merged and deployed with no recorded review (SUP-313, #66). Its three digests were
correct; that was a careful QA pass and some luck, not a gate.

## The direction, and why it is the only one

**digest → attestation → commit.** Never tag → digest.

Resolving a tag to a digest and comparing it against the pin used to look like the same
check. It is not, and it is now wrong: a scheduled release can re-push the same
`main-<sha>` tag onto a freshly built image (SUP-316), so a tag no longer identifies one
image and agreeing with one proves nothing. A digest names bytes. An attestation is the
only statement about where those bytes came from that the workflow which produced them
cannot forge — and it is signed over the digest, which is why the check runs in this
direction or not at all.

## What it checks

1. **The pin is a digest.** A `digest:` key holding an empty string, a tag, or the
   all-zero placeholder fails. A first-party image block declaring no `digest` key at all
   fails too: ours are digest-pinned or they are not pinned. A vendored third-party
   subchart naming its own image by tag is the vendor's contract and the listing rewrites
   it, so that shape is reported rather than failed.

   A pin written as one string — `image: ghcr.io/super-protocol/…/router-ui:main-deadbeef`
   — counts as a pin, not only a mapping with `repository`. Recognising only mappings let
   that form through with the job green and the chart simply absent from the verdict, which
   is this check failing by not looking. Four charts here write their third-party images
   that way, so the form is in use and not hypothetical.

2. **The attestation verifies, for this digest.** `gh attestation verify
   oci://<registry>/<repository>@<digest> --repo <source repository>` must exit 0, and the
   attestation's subject must be the declared digest under the declared name. The source
   repository is derived from the image path — `ghcr.io/super-protocol/<repo>/<image>` is
   built by `Super-Protocol/<repo>` — rather than from a table, which would be one more
   thing to forget to extend when a chart gains an image.

   The bundle is fetched from the registry (`--bundle-from-oci`) first and from the GitHub
   API second. That order is not a preference: `Super-Protocol/confidential-s3` is private,
   so this repository's workflow token cannot read its attestations through the API, and the
   first CI run of this job returned HTTP 404 for all three of its digests while the public
   router's verified fine. The registry copy needs no such access and is trusted no more —
   it is checked against the same Sigstore certificate identity and the same subject digest,
   so a withheld or swapped bundle is a verification failure rather than a pass. The API
   fallback is there for an image whose release workflow pushes no bundle to the registry.

3. **One bundle, one source revision.** Every first-party pin a listing bundles has to
   agree about the commit, per source repository. A half-rebased re-pin — the API moved,
   the console left on the revision before it — renders, lints, passes every golden and
   deploys two halves of one contract built from two different commits. That is the
   realistic mistake, and nothing else here can see it.

4. **The certificate and the predicate agree.** The commit and signer workflow are read
   from the signing certificate (`sourceRepositoryDigest`, `buildConfigURI`), which GitHub
   populates from the OIDC token. The same commit also appears in the SLSA predicate,
   which the attested workflow can set. Both are read and a disagreement fails, so reading
   the convenient one is not what the verdict rests on.

   Every entry of `resolvedDependencies` is scanned, and an attestation naming no
   `gitCommit` anywhere fails. Reading `resolvedDependencies[0]` and skipping the
   comparison when it held no commit made this guarantee switch itself off: one decoy
   entry ahead of the git dependency, and a forged commit behind it passes. Every release
   workflow puts the git dependency first today, so that was latent — but an upstream
   predicate change would have disabled an advertised check with no signal. `subject` is
   searched the same way and for the same reason.

5. **The attestation names a revision at all.** A signing certificate without
   `sourceRepositoryDigest` fails rather than being reported as attested beside an empty
   commit — and rather than falling back to the predicate's copy, which is the field the
   attested workflow writes.

6. **One digest, one revision.** A digest carrying attestations from two different commits
   fails: whichever rebuild or re-push produced it, "the revision this digest came from"
   then has no single answer, and the claim a reviewer would read cannot be made.

Third-party images — `ollama/ollama`, `berriai/litellm`, Bitnami, Confluent — are
reported, not attested: nothing in Super-Protocol signs them. They appear in the job
summary under what the gate *saw and does not vouch for*, so the verdict is explicit about
its own edges instead of silently omitting them.

Both sides of a pin are read: `charts/*/values.yaml`, which is what the chart would pull,
and each listing's `components[].images[]`, which is the allow-list the cloud enforces at
deploy time (marketplace specification §2.7). `charts/tests/declared_images.py` already
holds the two to the same digest; reading both here means the verdict does not depend on
that check having run.

## Running it

```bash
scripts/verify-pins/verify_pins.py            # this tree, live gh attestation verify
scripts/verify-pins/run.sh                    # the self-test, offline, ~1s
scripts/verify-pins/run.sh --live             # the self-test, then this tree live
```

A live run is about thirty seconds: one `gh attestation verify` per distinct image, and
the answers are kept, so a digest pinned in both a chart and its listing is looked up once.

## The fixtures

`fixtures/main-attestations.json` is the real `gh attestation verify --format json` output
for the pins on `main`, trimmed to the fields a verdict reads. Each case under `fixtures/`
is a small repository tree — one listing, two charts — read with that recording overlaid by
the case's own `attestations.json`.

Recordings rather than live lookups, because most of these shapes cannot be produced live
at all: nobody can push an image to `ghcr.io/super-protocol` that has no attestation, or
an attestation whose predicate disagrees with its own signing certificate. Those are
exactly the paths a provenance gate has to get right and exactly the ones that otherwise
never run.

| Case | What it pins down |
| --- | --- |
| `good-train` | the 0.18.3 train: three first-party pins, one source revision, and the commit printed in full — a reviewer reads the summary instead of re-running anything |
| `tag-not-digest` | `digest: ""` with the tag filled in, which is what a chart looks like mid-re-pin and what every other check here passes |
| `first-party-tag-only` | the same mistake made by deleting the key rather than emptying it |
| `no-attestation` | a digest nothing attests — a local build, or a registry the release workflow never pushed to |
| `split-train` | the half-rebased listing: two real digests, two valid attestations, two commits |
| `forged-predicate` | the SLSA predicate's commit against the signing certificate's |
| `no-source-commit` | a certificate naming no source revision, with the predicate's right there to be read instead |
| `subject-mismatch` | an attestation that verifies, for an image other than the one pinned, with a decoy subject ahead of ours |
| `string-form-pin` | the pin written as one string, which recognising only mappings made invisible |
| `decoy-dependency` | a decoy resolved dependency ahead of the git one, with the commit behind it forged |
| `no-predicate-commit` | a predicate resolving no commit at all, where skipping the cross-check is the convenient reading |

Re-record when a listing re-pins, from a tree where the live gate passes:

```bash
scripts/verify-pins/verify_pins.py --record scripts/verify-pins/fixtures/main-attestations.json
```

and move the digests and the commit in `fixtures/*/` with it. `good-train` is the
regression that says the 0.18.3 train traces to one commit, and it only says that while it
carries that train's digests.

## What this does not do

It does not constrain **which workflow, on which ref** built the image. `--repo` pins the
source repository and nothing else, so what a green verdict actually says is "built by some
workflow on some ref in `Super-Protocol/<repo>`" — not "built by a release workflow on a
release branch". Anyone who can run any workflow in that repository can mint an attestation
this gate accepts. The run prints the signer workflow and its ref in every row, so the
difference is visible; it is not enforced. Tightening it means `--signer-workflow` or
`--cert-identity`, and the signer set is genuinely plural today — `release-router.yml` and
`release-gatekeeper.yml` on `main`, `release-images.yml` on `develop` — so it needs the
per-repository allow-list this deliberately avoids. Worth revisiting once the release
workflows settle.

It does not make the check **required**. `main` in this repository carries no branch
protection, no rulesets and no CODEOWNERS, so a red `verify-pins` blocks nothing — it
replaces a claim with machine output, which is worth having, and it is not yet a gate.
Making it required is a repository-admin change (SUP-318), and it has a trap in it worth
knowing before that moment: **a path-filtered workflow cannot be a required status check.**
A pull request touching neither `charts/**` nor `apps/**` — a README fix — never starts
`digest provenance` at all, so a ruleset requiring it leaves that pull request pending
forever. Every check in this repository has that shape. The answers are either an
always-running job that short-circuits green, or a ruleset that accounts for skipped checks;
neither is a change to this job.

It also does not replace a reviewer. The job answers "which revision is this, and does the
bundle agree about it" — not whether that revision is the one the listing's notes describe.

And it does not hold a **third-party** image to a digest. It cannot: a vendored subchart
naming its own image by tag is the vendor's contract, and the listing is what rewrites it.
Where a listing does declare the image, the declared digest is checked for shape and the
chart render is held to it by `charts/tests/declared_images.py`. Where a listing declares
no `images:` at all — `conversational-analyst` today — its charts' tag pins are reported
here and enforced nowhere, which is a real gap in a different check's territory and not one
this job can close by failing the catalog as it stands.

One shape of pin is still unresolved rather than checked: a mapping under an `…image` key
that declares a `digest` or `tag` but no `repository`, which is how a chart overrides only
part of a vendored subchart's default — `conversational-analyst-gateway` does it with a bare
`image.tag`. The repository then lives in `charts/vendor/**`, which this does not read, so
the pin cannot be named and is not reported. It cannot hide one of ours today, because every
vendored default here is third-party; a first-party image introduced that way would be
invisible, and resolving it means reading the vendored chart's own values.
