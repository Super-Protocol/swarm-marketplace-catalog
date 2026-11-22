#!/usr/bin/env python3
"""Generate the Acme engineering handbook: a document corpus, as ClickHouse rows.

    datasets/acme-engineering-handbook/generate/generate.py --out handbook.tsv
    datasets/acme-engineering-handbook/generate/generate.py --self-test

Why this exists: the marketplace gatekeeper can only carry ClickHouse over HTTP, so a document
corpus a data owner wants to gate has to BE a ClickHouse table — one row per document. Nothing
in this catalogue was that shape, and `Confidential RAG` needs one to demonstrate anything at
all. So: a synthetic handbook of the kind a platform team actually keeps, in the four columns
that listing's parameters default to (`doc_id`, `title`, `text`, `updated_at`).

Everything here is written for this repository. No sentence is taken from a real company's
documentation or derived from one, and nothing in it is a secret — it is a demonstration corpus
whose only job is to make a retrieval answer checkable by somebody reading the row it came from.

Deterministic: one seed, one corpus, byte for byte, so a regenerated copy is the same data
rather than similar data.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from datetime import date, timedelta

SEED = 20261122
START = date(2025, 4, 7)

# Each entry is (kind, title, body). The bodies are deliberately specific — a number, a
# command, a name — because a retrieval demo over vague prose cannot be checked by a reader.
DOCUMENTS: list[tuple[str, str, str]] = [
    ("runbook", "Rotating the gatekeeper service token", """
The gatekeeper's admin token is rotated every 90 days, and out of band whenever somebody who
held it leaves. Generate the replacement with `openssl rand -hex 32`, write it to
`SWM_API_gatekeeper__apiToken` in the marketplace environment file, and restart the API
container. The gate re-reads its admitted-client document within thirty seconds, so no
deployment loses access during the rotation and there is no window to schedule.

What does NOT work: rotating the token on the gate first. The gate refuses an unrecognised
caller, the marketplace stops being able to push, and every deployment keeps the access it had
until somebody notices — which is a fail-static the gate is designed for and not a state to
leave it in.
"""),
    ("runbook", "What to do when a pod is evicted for quota", """
An evicted pod is almost always a `resources.min` that does not cover every container's limits
at once. The cluster space's quota is fixed when the space is created, it is taken from the
listing, and the heaviest pod is the one the scheduler gives up on first.

Read the space's quota, add up the limits of every container the listing renders — including
the init containers, counted as the heaviest rather than the sum, and including the surge pod
of any Deployment that rolls — and compare. If the quota is short, the listing has to declare
more and the deployment has to be recreated: a quota is not something a reconfigure can widen.

A vector store evicted this way comes back with its index intact, because unpublishing deletes
workloads and never PersistentVolumeClaims.
"""),
    ("runbook", "Restoring the marketplace database from a backup", """
Backups live beside the compose file as `backup-<timestamp>.sql` and are taken before every
mutation, without exception. To restore: stop the API and UI containers, leave Postgres up,
`dropdb` and `createdb` the database as the `marketplace` role, then `psql -f` the dump. Start
the API and watch for the migration runner to report nothing to do — a dump from an older
schema will try to run migrations against data that already has them.

The one thing a restore does not bring back is anything the cloud holds. A deployment row
restored from a backup whose cluster space has since been deleted points at nothing, and the
deployment view shows it as running.
"""),
    ("policy", "Which actions stay human", """
Four things are never done by an agent, however routine they look: spending real money,
deploying to production, force-pushing a branch, and sending a message to anybody outside the
company. Each has the same property — the damage is not visible in the artefact, so review
after the fact cannot catch it.

Everything else is fair game, and the test is whether a reviewer can see what happened from the
diff and the issue. A deploy to a demonstration stand is not a production deploy. A squash merge
to `develop` is not a force push.
"""),
    ("policy", "Attribution in commits and pull requests", """
A commit, a pull request and an issue comment record the people who worked on the change and
nothing else. No tool attribution trailer, no "generated with" line, no co-author naming a
model. `Co-authored-by:` is for a human who worked on the change.

This is enforced in CI for the confidential-router repository, which reads the pull request
title, the body and every commit on the branch. A trailer there fails the merge, which is the
point: the rule is not a preference anybody has to remember.
"""),
    ("policy", "Data classification and where each class may run", """
Four classes. **Public** may run anywhere. **Internal** may run on managed infrastructure in
any region we hold a contract for. **Confidential** may only be processed inside a confidential
cluster space, with the deployment's evidence published and the data owner's approval pinned to
its digest. **Regulated** adds a named data processing agreement and a retention schedule
agreed before the first byte is loaded.

The classification travels with the dataset, not with the application. An application that
reads a regulated dataset inherits its constraints for as long as it is mounted.
"""),
    ("architecture", "Why evidence is a digest and not a report", """
A deployment publishes a signed snapshot of the objects applied to its namespace, canonicalised,
and a digest over it. The digest is what a data owner's approval is pinned to, and the reason it
is a digest rather than a human-readable report is that approval has to be checkable by a
machine on every request, thirty times a second, without a person in the loop.

What the digest is NOT is a measure of trustworthiness. It says "this is the same thing you
approved". Whether the thing was worth approving is a question for whoever read the snapshot
once, which is why the snapshot is published beside it.
"""),
    ("architecture", "Why a listing declares its digest in advance", """
The marketplace admits a deployment to the gatekeeper only when its listing version declares an
expected digest. Without one there is nothing to hold the deployment to, so it is left out
rather than admitted on the marketplace's word — and every read of a gated dataset then fails,
with nothing in the deployment, the chart or the schema to say why.

A digest cannot be derived from the charts, because a cluster defaults fields no template
writes. It takes a real deployment, read back from the cloud, and then a second one to prove the
number is a property of the version rather than of the deployment.
"""),
    ("architecture", "What the egress sidecar admits, and what it eats", """
A deployment that mounts a gated dataset gains three objects it did not render: the egress
sidecar's ConfigMap, Deployment and Service. The sidecar admits exactly the gate's data listener
and nothing else.

So an application that also needs to reach an SMTP relay, an external model endpoint or a
package index must have that host added to the egress policy, or the connection is silently
eaten — no error, no refusal, a timeout. This has cost two production trains an hour each. The
fix is to list every outbound host a listing needs at the time the listing is written, not the
first time somebody notices mail is not arriving.
"""),
    ("troubleshooting", "An answer came back with no citations", """
No citations means the retriever returned nothing above the score threshold. Check three things,
in this order:

1. The collection exists and holds chunks. The indexer's status endpoint says how many.
2. The embedder serving the query is the same checkpoint that embedded the corpus. This is the
   common one, and it is invisible in every log: two different models produce vectors of the
   same width that mean different things, so the search succeeds and returns nonsense.
3. The retriever's top-k is not above the vector store's candidate list. Milvus refuses a search
   whose top-k exceeds `ef`, and the refusal surfaces as an empty result.
"""),
    ("troubleshooting", "A hostname was published and answers nothing", """
Four causes, in the order they actually occur.

The Ingress has no `ingressClassName`: it is created, its hostname is registered, DNS is written
for it, and no controller claims it. This reads as a DNS fault for about an hour.

The hostname was never verified. A published hostname that never completed its TXT and CNAME
verification is dark, and the deployment view shows it as healthy.

The deployment is not published. Publication is what makes the cloud gateway serve a
deployment's hostnames — which is also why unpublishing is the kill switch, and why deleting the
DNS record is not: the gateway routes on the Host header.

The pod behind it is not ready, and the readiness probe is on a path that requires a session.
"""),
    ("troubleshooting", "A rebuilt deployment serves no models", """
An import writes every external endpoint switched off and with an empty API-key envelope,
because the export bundle carries no secret of any kind. The rebuilt deployment therefore
advertises no models at all: the models endpoint answers with an empty list and every completion
is refused, while the deployment reads as running and published and its own health endpoint says
it is fine.

Two mutations per endpoint fix it: write the model deployment's own key, then enable the
endpoint. It goes from disabled to verified in about seventy seconds. A completion through the
production hostname returning 200 is the last step of a deploy train — not the hostname
answering.
"""),
    ("reference", "Image pinning: tags name releases, digests name bytes", """
Every image a listing deploys is declared by digest. A tag is a label somebody can move: a
rebuilt `latest`, or a re-pushed release tag, replaces the software while leaving the manifests
— and therefore the attested digest — unchanged. That is the one substitution the evidence
exists to make impossible, and it is invisible in a diff.

The tag stays in the values beside the digest so that a human reading the chart can tell which
release it is without resolving a hash. The render path is fail-closed on the declared list: an
image in the manifests that the listing did not declare refuses the whole deployment.
"""),
    ("reference", "Weights are fetched against a manifest, not baked in", """
Model weights are downloaded at first boot by an init container, against a manifest that names
every file by sha256, rendered into a ConfigMap the cloud digests and signs. The fetcher hashes
while writing, deletes anything the manifest does not name, and refuses to mark the volume ready
on any mismatch. The engine then runs offline, so a missing or renamed file fails loudly rather
than being quietly re-fetched unverified.

The alternative — baking the weights into the image — gives the same pin by a different route
and costs a build pipeline nobody has: a hosted runner has about 14 GiB of free disk and a
30B checkpoint plus its engine is nearer 56.
"""),
    ("reference", "A streaming writer's memory is not its resident set", """
A downloader that streams in 8 MiB chunks and never holds more than 40 MiB of its own was still
OOMKilled under a 1Gi limit. A container's page cache is charged to its cgroup, the cache of a
file being written stays dirty until writeback completes, and at a few hundred megabytes a
second the kernel kills the writer rather than waiting.

The fix is to sync every 64 MiB and hand the range back to the kernel. The peak is then bounded
by that window rather than by the size of the file, which is why one number serves a 6 GiB
download and a 31 GiB one. The lesson generalises: a limit that made a symptom go away is not a
measurement.
"""),
    ("reference", "Model warm-up allocates for the batch, not the request", """
An inference server with an 8192-token context, started with a 16384-token batch budget, is
OOMKilled three seconds into start-up under an 8Gi limit — before it serves a single request.
The warm-up pushes a full batch through the model and the activation it allocates goes as the
square of the sequence length, so a 512-token model takes that budget happily and an
8192-token one cannot.

A render test passes. A readiness probe never gets the chance to fail usefully. The batch
ceiling and the memory limit are properties of the checkpoint, so they belong beside it and they
have to be measured rather than copied from the model next to it.
"""),
    ("incident", "Twelve minutes dark on a hostname rename", """
The deploy train assumed a hostname could be renamed on a published cluster space. It cannot: a
published space refuses every workload change, and a hostname lives in the Ingress, so a rename
costs unpublish, reconfigure, publish — two pod bring-ups, not one Ingress edit. The window was
planned as ninety seconds and took twelve minutes and forty-six seconds.

The fix is to avoid the rename. Create the replacement deployment on the production hostnames
from the start and give the OLD one temporary names, so the only deployment ever renamed is the
one not serving anybody.
"""),
    ("incident", "A rollback that reported itself published and was not", """
A script read the deployment's hostname list once, immediately after creating it, and wrote the
DNS it named. The list is populated by a worker that runs once a minute, so it was empty, the
script wrote nothing, and the deployment was published with no verification record. Both
hostnames answered nothing for eighteen minutes while the view showed healthy.

Poll the hostname list until it names every hostname expected, write the records, then poll
again until each one reads verified and delegated. Only then publish. A published hostname that
never verified is dark, and nothing in the deployment says so.
"""),
    ("incident", "The grounding job that never ran", """
A configuration init container listed a ConfigMap mount as if it were a flat directory of keys
and died with EISDIR on the kubelet's own timestamped entries. The five services waiting on that
container restarted behind it for as long as the deployment existed.

Two things made it expensive. The failure was in a vendored subchart, so it was invisible in the
listing and in every golden render of the chart above it. And the restart loop looked like a
resource problem, so the first two hours went into quota.
"""),
    ("howto", "Adding a data interface", """
A slot may only name an interface the platform registry already holds. The registry is not this
repository: the directory here is a copy, and the publishing service refuses a definition naming
a contract the registry does not have. So adding one is a three-repository change — the
specification, then the platform's mirror, then the copy here and the slot that names it.

Writing the contract first and naming it in a slot makes the listing unpublishable everywhere,
not merely incomplete. If a listing needs an interface that does not exist yet, the honest move
is to say so in the listing copy and ship the interface it does have.
"""),
    ("howto", "Publishing a listing to the demonstration stand", """
Branch, pull request, CI, merge. Merging republishes the chart repository, which is append-only:
a change means a new chart version, never an edit in place. Then seed the listing.

The seeder is re-runnable for applications — a version already published is left alone — and it
rewrites datasets and grants unconditionally, so on a shared stand seed a one-application slice
rather than the whole repository. A chart on a branch cannot be fetched from the published
repository, which only serves the default branch, so measuring a digest before merge means
serving the packaged chart from somewhere the renderer can reach.
"""),
    ("howto", "Reading what a deployment actually attests", """
Ask the cloud, not the render. The deployment's evidence endpoint answers with the digest, the
signed snapshot and whether it matches what the listing declared. The render is what was sent;
the evidence is what is there, including every field a cluster defaulted that no template wrote.

A reconfigure re-renders the version the deployment was created from, not the latest one — so
the reconfigure form can offer parameters the render will not honour. Check the evidence for
what a reconfigure will produce, never the form.
"""),
]


def paragraphs(body: str) -> str:
    return "\n\n".join(block.strip() for block in body.strip().split("\n\n"))


def rows(count: int) -> list[tuple[str, str, str, str]]:
    """One row per document. Beyond the written set, documents are revisions of them.

    A handbook has revisions, and a retrieval corpus that contains two versions of the same
    runbook is a more honest test than one where every document is unique: it is where a
    reranker earns its keep.
    """
    random.seed(SEED)
    out: list[tuple[str, str, str, str]] = []
    for index, (kind, title, body) in enumerate(DOCUMENTS):
        doc_id = f"{kind[:3].upper()}-{index + 1:03d}"
        when = START + timedelta(days=random.randint(0, 560))
        out.append((doc_id, title, paragraphs(body), f"{when} {random.randint(8, 18):02d}:00:00"))

    # Revisions, deterministic and clearly marked, to take the corpus to `count`.
    index = 0
    while len(out) < count:
        kind, title, body = DOCUMENTS[index % len(DOCUMENTS)]
        revision = index // len(DOCUMENTS) + 2
        doc_id = f"{kind[:3].upper()}-{index % len(DOCUMENTS) + 1:03d}-r{revision}"
        when = START + timedelta(days=random.randint(560, 600))
        note = (f"Revision {revision}, superseding the entry above. Reviewed on {when} and left "
                "unchanged in substance; the wording below is the one in force.")
        out.append((doc_id, f"{title} (revision {revision})",
                    paragraphs(note + "\n\n" + body), f"{when} {random.randint(8, 18):02d}:00:00"))
        index += 1
    return out[:count]


def tsv(records: list[tuple[str, str, str, str]]) -> str:
    lines = []
    for doc_id, title, text, when in records:
        flat = text.replace("\t", " ").replace("\\", "\\\\").replace("\n", "\\n")
        lines.append("\t".join((doc_id, title.replace("\t", " "), flat, when)))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write TabSeparated rows here")
    parser.add_argument("--count", type=int, default=64, help="how many documents")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    records = rows(args.count)

    if args.self_test:
        again = rows(args.count)
        assert records == again, "the generator is not deterministic"
        assert len({r[0] for r in records}) == len(records), "duplicate doc_id"
        assert all(r[2].strip() for r in records), "an empty document"
        body = tsv(records)
        assert body.count("\n") == len(records), "a row contains a raw newline"
        digest = hashlib.sha256(body.encode()).hexdigest()
        print(f"  ok    {len(records)} documents, deterministic, sha256 {digest[:16]}…")
        print(f"  ok    {len(DOCUMENTS)} written entries, "
              f"{len(records) - len(DOCUMENTS)} marked revisions")
        return 0

    out = tsv(records)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(out)
        print(f"{len(records)} documents -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
