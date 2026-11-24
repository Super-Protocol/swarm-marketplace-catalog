# The researcher deployment's configuration, as exported

What was configured by hand on `researcher-stand.conf-apps.superprotocol.dev` and lives only in
that deployment's own PostgreSQL. The analyst stand's equivalent is `../configuration-backup`;
this is the other deployment's, kept separate because `harness/restore-stand.js` restores one
`APP_BASE` from one `IN` directory and the two must never be crossed.

Exported with:

```bash
APP_BASE=https://researcher-stand.conf-apps.superprotocol.dev \
APP_USER=gate-rehearsal@local.test PASS_FILE=./researcher-stand.administratorPassword \
OUT=./researcher-configuration-backup node harness/export-stand.js
```

Restored with the same two values and `IN=./researcher-configuration-backup`, plus
`CONFIRM_TARGET` repeating `APP_BASE`. The restore's step 0b registers the three release sources
before the model is imported, which is the order that matters: a semantic model binds to a data
source, and importing one into a tenant whose sources are not registered yet binds it to nothing.

## What is in it

`semantic-model.clinical-research-network.json` is the cross-source model: six datasets (each
release's monthly fact and its sites table), three dimensions, **nine bindings** and twelve
metrics.

The point of it is the `network` dimension, which is bound to **all three** releases on
`network_key` — one dimension, three bindings — so "by network" means the same thing in each of
them. `clinical_site` is bound three times as well, each through its own release's sites table, so
a site has a name rather than an id whichever network it belongs to.

**What the model cannot do, and the reason is in the model's own validator.** A metric belongs to
one dataset, a dataset belongs to one `datasourceId`, and each release is a separate gated
connection whose reader cannot read the other two — so there is no single pooled metric to define.
A derived metric does not help: *"a derived metric stays within one dataset"*. Each network
therefore has its own `*_adenoma_detection_rate`, and a pooled comparison is three modelled
queries whose results are put side by side. Asked for one chart across all three, the agent said
so itself: *"The model does not support querying across multiple datasets in one semantic query."*

What the model buys, given that: the three releases share a vocabulary, and a modelled answer
carries a **reusable query** rather than a dead number. Before the model the same question was
answered from hand-written SQL and the agent refused to chart it — *"a chart block with inline
data must include a reusable query, which is not applicable here because the figures came from
hand-written SQL over raw tables rather than a modelled semantic dataset"*. After it, the answer
comes back as a chart with a live semantic query behind it, per network.

The empty files are not mistakes: this deployment has no dashboards, library items or autopilots.
It exists to answer one question from three releases.
