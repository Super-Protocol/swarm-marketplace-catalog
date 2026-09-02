# Confidential Claims Fraud Detection

A payer and a hospital publish into one event bus that **neither of them operates**, and a
continuous SQL query finds the claims that contradict the clinical record.

Everything runs in one cluster space: the bus, the stream processing, and both parties' feeds. What
makes it a demonstration rather than a diagram is that the isolation is enforced by the broker
rather than asserted by a slide.

## Why this needs a confidential bus

The signal only exists across parties. A claim naming a provider is unremarkable; a claim naming a
provider *while the patient is admitted somewhere else* is not, and no single organisation holds
both halves. Pooling them is the obvious answer and the reason it is rarely done is not technical:
whoever holds the pool reads everyone's stream.

That is the whole argument for running the bus confidentially, and it is why this demonstration is
built from two identities rather than one process writing both topics.

## What it deploys

| | |
|---|---|
| **The bus** | Kafka in KRaft mode, SASL/PLAIN on every connection, the KRaft authorizer on, Schema Registry, and Control Center with its Prometheus backend |
| **Stream processing** | ksqlDB, running the join below as a continuous query |
| **The parties** | Two workloads with two credentials — a payer's claim feed and a hospital's admission feed |

Four accounts, each granted exactly what it needs:

| Account | May do | May not |
|---|---|---|
| `payer` | write `claims` | read anything, touch `clinical.events` |
| `hospital` | write `clinical.events` | read anything, touch `claims` |
| `analyst` | read `fraud.alerts` | read the claims or clinical events behind an alert |
| `admin` | everything | — (it is what ksqlDB and the console run as) |

## The rule

```sql
FROM claims_by_patient c
INNER JOIN events_by_patient e WITHIN 10 MINUTES ON c.patient_id = e.patient_id
WHERE e.status = 'INPATIENT' AND c.provider_id <> e.hospital_id
```

Deliberately something a person can check: no model, no threshold, no scoring. What the
confidential deployment protects is visibly the data and the join — not a secret algorithm whose
value has to be taken on trust.

Both sources are re-keyed by patient first, because a stream-stream join in Kafka is a join of
co-partitioned topics and each party keys its own events by its own identifiers.

## Running the demonstration

1. **Open Control Center.** Topics `claims` and `clinical.events` fill from two different
   identities; `fraud.alerts` holds only the contradictions — with the defaults, roughly one claim
   in four, about every forty seconds.
2. **Show the query** under ksqlDB: the join is running continuously, not on a button.
3. **Show the isolation.** A consumer presenting the `payer` credential against `clinical.events`
   is refused by the broker with `TOPIC_AUTHORIZATION_FAILED` — the two parties share a cluster and
   cannot read each other.
4. **Show what the deployment attests to.** Every deployment of this version renders identical
   manifests bar the namespace and the hostname, so the digest a data owner approved is a fact
   about the software, not about the run.

## Two feeds that agree without talking

Both wake on the same multiple of the interval and derive the same patient id from the clock, so a
claim and an admission for the same patient land inside the join window. The payer alone decides
whether that pair conflicts. They share nothing but the wall clock — no coordination topic, no
shared state — and that is on purpose: coordination would be a channel between two parties that are
supposed to be strangers.

Aligning them matters more than it looks. Each round spends a few seconds starting a JVM to send
one message, so feeds that simply slept between rounds drifted apart within a minute, stopped naming
the same patient, and produced a join that never matched — which is indistinguishable, from the
outside, from a rule that finds nothing.

## Limits worth stating before a demo

- **The wire inside the cluster space is not separately encrypted.** SASL here provides *identity*,
  which is what the ACLs are written against; confidentiality comes from the space itself. TLS
  between two pods of one attested deployment would defend against an attacker already inside the
  boundary.
- **The alert stream is where a decision would go, not a decision.** Wiring an agent onto
  `fraud.alerts` — reading it with the `analyst` credential and asking a confidential model what to
  do — is the next component, not a part of this one.
- **The data is synthetic and repetitive by construction.** It exists to make the mechanism
  visible, not to be realistic claims traffic.
