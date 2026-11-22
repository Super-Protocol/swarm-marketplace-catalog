# Acme Engineering Handbook

A synthetic internal engineering handbook: 64 documents across runbooks, policies, architecture
notes, troubleshooting entries, incident postmortems and how-tos.

Everything here is written for this repository. No sentence is taken from a real company's
documentation or derived from one, and nothing in it is a secret — it is a demonstration corpus
whose only job is to make a retrieval answer **checkable**, by somebody who can open the row it
came from and see whether the answer is in it. The entries are therefore specific: a number, a
command, a named failure. Vague prose makes a demo that cannot be wrong.

The generator is deterministic — one seed, one corpus, byte for byte — so a regenerated copy is
the same data rather than similar data:

```
datasets/acme-engineering-handbook/generate/generate.py --self-test
datasets/acme-engineering-handbook/generate/generate.py --out handbook.tsv
```

## Why it is a ClickHouse table and not a bucket of files

Because that is the only shape the marketplace gatekeeper can protect. The gate injects
ClickHouse credentials into every request it forwards and rewrites the database the caller
named; it carries no other protocol. A corpus in object storage therefore **cannot be gated
today**, whatever a dataset's `access.mode` says — so a document corpus whose owner wants to
approve a specific build before it is read has to be a table, one row per document.

That is a platform limit rather than a preference, and it is the reason this dataset exists:
nothing else in this catalogue was that shape, and
[Confidential RAG](../../apps/confidential-rag/) needs a gated corpus to demonstrate the gate.

## The twenty-two entries, and the forty-two revisions

Twenty-two documents are written out. The rest are marked revisions of them — `(revision 2)` in
the title, with a line saying what they supersede — because a handbook has revisions, and a
retrieval corpus where every document is unique is an easier test than the real thing. Two
revisions of one runbook both match a query strongly, which is exactly where a reranker is worth
having and where a retriever on its own starts citing the superseded one.

## Where it runs

On the stand, `handbook.documents` on the marketplace host's own ClickHouse, reachable from the
subnet and from nothing else. The reader account behind the secrets above has SELECT on that one
table, is read-only, and is restricted to that one database — the gate holds it, and a consumer
never sees it.
