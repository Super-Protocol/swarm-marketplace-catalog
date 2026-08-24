# Confluent Platform

Apache Kafka as Confluent packages it, with **Control Center** — the browser console for topics,
schemas, consumer lag and message inspection — published on a hostname of its own.

One broker in KRaft mode (no ZooKeeper), Schema Registry, and Control Center 2.x with the
Prometheus backend it now reads its metrics from.

## The licence question, answered

A single-broker cluster runs under Confluent's **developer licence**: every commercial feature,
including Control Center, free of charge and with **no expiry**. This listing deploys exactly one
broker, so a deployment needs no licence key at all.

If you have an Enterprise key, there are two ways in and neither has to be decided at deploy time:

- **In Control Center, at any time** — *Administration → About Control Center → Licence → Update
  licence key*. It takes effect immediately, with nothing restarted: the key is written to the
  cluster's own `_confluent-command` topic and every other component reads it from there.
- **As a deploy parameter**, if you would rather the key never be typed into a browser. It is a
  sensitive parameter, so it is sealed for the target and stripped from the deployment evidence.
  Left blank at first, it can be filled in later by reconfiguring the deployment — a blank
  sensitive field means "keep what is running", not "clear it".

One thing worth knowing before scaling up: **adding a second broker starts a 30-day trial licence,
and the switch cannot be reverted.** Growing this deployment is therefore a licensing decision,
not only a capacity one.

## Why a chart of our own

Confluent's supported route onto Kubernetes is the CFK operator, which installs
CustomResourceDefinitions and cluster-scoped RBAC. A cluster space is a single namespace with
namespace-scoped rights, so that route is unavailable — and `confluentinc/cp-helm-charts`, the old
plain-manifest chart, no longer exists.

So `charts/confluent-platform` drives the official `confluentinc/cp-*` images directly, with every
environment variable taken from Confluent's own `cp-all-in-one` compose for 8.3.1 rather than
invented. All five images are pinned by digest.

## What is published, and what is not

Only the console. The broker, Schema Registry and Prometheus stay inside the cluster space:

- A Kafka listener here has **no authentication**. Published, it would hand anyone who found the
  name the ability to read every topic and write to any of them.
- Control Center is an **administrative** UI — create and delete topics, read any message. It is
  therefore served behind HTTP Basic authentication, with two accounts whose passwords the
  marketplace generates: `admin`, which may change the cluster, and `viewer`, which may only watch
  it. Both are shown once, after deploying.

Applications in the same cluster space reach Kafka at the bootstrap address in the outputs.

## Sizing

The cluster space is created with the quota this definition declares, and that quota has to cover
every container's limit at once — the broker, Schema Registry, and the console pod's three
processes. Below roughly 2 CPU and 10 GB the console is the first thing to be evicted.

Control Center is the expensive half of this deployment, not Kafka.

## Two deliberate limitations

**Metrics do not survive a restart.** Control Center, its Prometheus and its Alertmanager run as
three containers in one pod, sharing the directory Control Center writes trigger rules and
Alertmanager configuration into. Across separate pods that directory would need a read-write-many
volume; in one pod it is an `emptyDir`, and so is Prometheus' own storage. The cluster keeps
running; its monitoring history starts again.

**This listing declares no evidence digest.** The Kafka cluster id is derived per deployment — it
formats the log directory and the broker will not open it under another — so two deployments cannot
render identical manifests. Gating a dataset for this application would first need that field
excluded from the evidence snapshot.
