# Cascade Card Transactions

A synthetic card-authorisation history: 320,176 transactions over 1,200 cardholders and 34
months, with merchant category, city, state and postcode, entry mode and a fraud label.

Everything here is synthetic. No cardholder, merchant or transaction is real or derived from a
real one — it is a demonstration corpus whose job is to make a **gated read** of the one data
shape an institution genuinely may not export into something a reader can check.

The **geography is real** and the transactions are not. The shipped 29M-parameter checkpoint's
vocabulary enumerates US state codes and 3-digit ZIP prefixes, so an invented geography tokenises
to `STATE_XX` and a ZIP bucket nobody trained on — the coverage report would then say the model
cannot read this data, for a reason that is an artefact of the generator. `generate/places.py` is
therefore 68 real (state, city, ZIP) triples and 67 real merchant category codes drawn from the
tokenizer's own `KNOWN_MCCS`. The merchant *names* are invented, which costs nothing: the
tokenizer hashes them into 2,000 buckets and every name hits one.

```
datasets/cascade-card-transactions/generate/generate.py --self-test
datasets/cascade-card-transactions/generate/generate.py --out transactions.tsv
```

Deterministic — one seed, one table, byte for byte:

| rows | sha256 of the TabSeparated output |
|---|---|
| 320,176 | `150c38bd13253a1a8ac179ffb94dd1f4fe7ef50b5ab41f8bec1768fad7b431a5` |

`--self-test` **asserts** those numbers rather than printing them, along with the coverage table
below, the fraud rate, the value domains the checkpoint's vocabulary depends on, and that this
README still names every column the generator writes — which matters more here than for a card
with a `schema:` block, because this README *is* the schema. It runs in CI on every change under
`datasets/` (`.github/workflows/dataset-definitions.yml`).

## The columns, and why they are spelled like that

TabFormer's own names, which is what
[Confidential Transaction Model](../../apps/confidential-transaction-model/) defaults every one
of its column parameters to. A deployment reading this dataset therefore answers **nothing** in
the listing's "Your transactions" section. That is the point: those parameters exist for an
institution whose extract is spelled its own way, and a demonstration that must also explain a
column mapping demonstrates two things badly.

```sql
create table cascade_tx.transactions (
  `User` UInt16, `Card` UInt8,
  `Year` UInt16, `Month` UInt8, `Day` UInt8, `Time` String,   -- 'HH:MM'
  `Amount` String,                                            -- '$123.45', negative for a refund
  `Use Chip` String,                                          -- Swipe / Chip / Online Transaction
  `Merchant Name` String, `Merchant City` String, `Merchant State` String,
  `Zip` Nullable(String),                                     -- NULL on a card-not-present row
  `MCC` Int32, `Is Fraud?` String                             -- 'Yes' / 'No'
) engine = MergeTree order by (`User`, `Card`, `Year`, `Month`, `Day`, `Time`)
```

Four of those names cannot be declared in a `DataDefinition` at all: the specification's
column-name pattern is `^[a-zA-Z_][a-zA-Z0-9_]*$`, and `Merchant Name` has a space in it while
`Is Fraud?` ends in a question mark. So `data.yaml` declares **no** `schema:` block rather than a
partial one that would describe a table this is not. Nothing is lost in matching — that listing
declares its slot with no schema constraint, for the same reason — but it is a real gap in the
specification and this table is the second place it has bitten.

## What the coverage report should say

`tfm_workspace.check_coverage()` measures how much of the data lands inside the checkpoint's
6,251-token vocabulary and refuses to continue below the deployment's floor (0.5 by default).
This table is built to land **above** it and not at a flat 100%, because a coverage report with
nothing to say teaches the reader that it never has anything to say:

| field | coverage | why it is not 1.0 |
|---|---|---|
| `mcc` | 0.9847 | 1.5% of rows use a real MCC outside `KNOWN_MCCS` — software, charity, subscription |
| `state` | 0.9933 | cards used in Toronto, Vancouver and Montreal: `ON`, `BC`, `QC` tokenise to `STATE_XX` |
| `zip3` | 0.9933 | the same foreign rows: `M5H`, `V6B`, `H3B` have no numeric prefix to tokenise |
| `entrymode` | 1.0000 | every row is one of the three modes the tokenizer maps |
| `user` | 1.0000 | 1,200 cardholders, inside the vocabulary's `CUST_0`–`CUST_2999` |
| `card` | 1.0000 | at most four cards per holder, inside `CARD_0`–`CARD_9` |

**Overall 0.9952**, against a 0.5 floor — verified against a real gated read from a deployment,
not computed here (SUP-900). The fraud rate is 0.210% — close to TabFormer's own
one-in-a-thousand and deliberately not the comfortable few percent that would make a baseline
classifier look like a fraud model.

One thing the table does **not** say, and it matters more than any number in it: **47,790 rows
(14.9%) carry no postcode at all**, exactly as TabFormer's card-not-present rows do, and
`coverage()` counts them as covered. It fills an absent `Zip` to `00000` and asks whether the
3-digit prefix is numeric, so every one of those rows tokenises — to `ZIP3_0`, one shared
bucket. That is a real property of the shipped tokenizer worth knowing before anybody reads a
ZIP feature's importance: a seventh of this table is one postcode as far as the model is
concerned. An earlier version of this README published `zip3` as 0.8507, which is the fraction
of rows that carry a postcode — a true number about the data, and the wrong answer to the
question the table asks.

## Where it runs

On the stand, `cascade_tx` on the marketplace host's own ClickHouse — the same server that holds
`handbook` and `kestrel_sc`, which is why the container carries SUP-840's name. One server, three
databases, three reader accounts that cannot read each other's data.

| | |
|---|---|
| Host | the marketplace host — Azure VM `swarm-marketplace`, `20.240.194.2`, subnet address `10.10.1.10` |
| Container | `sup840-handbook-ch`, image `clickhouse/clickhouse-server:24.8`, `--restart unless-stopped` |
| Listener | `10.10.1.10:8123` only. Not the host's public address and not `0.0.0.0`: the NSG does not open 8123 |
| Data | Docker volume `sup840-handbook-data` on `/var/lib/clickhouse` |
| Reader account | `cascade_reader`, declared in `/opt/marketplace/sup840-ch/users.xml`. Profile `readonly_bulk`, `allow_databases: cascade_tx`, and the subnet in `networks` |
| Consumers | the gate on `10.10.1.11` reads it as this account and serves it on a port of its own; nothing else has a credential for it |

Verified on 2026-11-24, after loading: `cascade_reader` reads `cascade_tx` and is refused
`kestrel_sc` with `497 Not enough privileges`; the table holds 320,176 rows; and `default` still
answers `516 Authentication failed` over the subnet address — see the handbook dataset's README
for why that is the check that matters most.

### Re-creating it from nothing

```bash
docker exec sup840-handbook-ch clickhouse-client -q "create database if not exists cascade_tx"
# the CREATE TABLE above, then:
datasets/cascade-card-transactions/generate/generate.py --out /tmp/transactions.tsv
docker exec -i sup840-handbook-ch clickhouse-client \
  -q "insert into cascade_tx.transactions format TabSeparated" < /tmp/transactions.tsv
```

`users.xml` is the one thing this repository cannot regenerate, because it carries the reader's
password — a stand secret, held on the host and in the dataset's publisher secrets.
