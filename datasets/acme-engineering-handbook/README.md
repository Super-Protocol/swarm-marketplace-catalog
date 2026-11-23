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

Concretely, so that whoever finds this table down can bring it back:

| | |
|---|---|
| Host | the marketplace host — Azure VM `swarm-marketplace`, `20.240.194.2`, subnet address `10.10.1.10` |
| Container | `sup840-handbook-ch`, image `clickhouse/clickhouse-server:24.8`, `--restart unless-stopped` |
| Listener | `10.10.1.10:8123` only. Not the host's public address and not `0.0.0.0`: the NSG does not open 8123, and binding the subnet address rather than every address is the second reason the corpus is not on the internet |
| Data | Docker volume `sup840-handbook-data` on `/var/lib/clickhouse` — a named volume, so it is identifiable, backed up and not something `docker system prune` collects |
| Reader account | `handbook_reader`, defined in `/opt/marketplace/sup840-ch/users.xml`, bind-mounted read-only onto `/etc/clickhouse-server/users.d/sup840.xml`. Read-only profile, `allow_databases: handbook`, and the subnet in `networks` |
| `default` | **loopback only** — `127.0.0.1` and `::1`, declared in the same `users.xml`. It has every grant `WITH GRANT OPTION` and no password, so an address restriction is the whole of its security |
| Consumers | the gate on `10.10.1.11` reads it as this account and serves it on port 9507; nothing else has a credential for it |

It is a plain `docker run` rather than part of `/opt/marketplace/compose.yaml` because it belongs
to a dataset in this catalogue, not to the marketplace service: the stand's compose file is the
marketplace, and a corpus that comes and goes with a listing should not be able to take the API
down with it.

### What actually protects the corpus, and what does not

Three things, and it is worth being exact about which does what, because the read-only reader
account is the one people name and the weakest of the three on its own:

1. **The NSG**, which does not open 8123 at all. Nothing outside the stand's subnet reaches this
   server.
2. **The listener**, bound to `10.10.1.10` rather than every address.
3. **`default` confined to loopback.** Inside the subnet, ClickHouse's own `default` user is the
   thing worth attacking: it holds every grant `WITH GRANT OPTION` and has no password, so
   reaching it from any host in `10.10.1.0/24` is a `DROP TABLE` and not merely a read. It is
   restricted to `127.0.0.1` and `::1`, verified by asking for it over the subnet address and
   getting `516 Authentication failed` back.

The reader account matters once a caller is already a legitimate one: it is what makes the gate's
credential useless for anything but `SELECT` on this one database — checked by asking it to
`truncate` the table and getting `164 Cannot execute query in readonly mode`. It is not what
keeps the subnet out.

The trap is in the image, and it is quiet. `clickhouse/clickhouse-server` ships `default` as
`<ip>::/0</ip>` and its entrypoint narrows that to loopback on startup — **unless
`CLICKHOUSE_SKIP_USER_SETUP=1` is set**, which this container was originally run with. Nothing
says so: the server starts healthy, the reader account works exactly as documented, and the
dataset serves correctly, with `default` wide open to every host on the subnet the whole time.
The container therefore no longer sets that variable, and the restriction is also written into
`users.xml` so it does not depend on remembering. Either one is enough on its own; note that the
entrypoint's own step stands down (silently) when it sees that `users.default` has already been
changed by a `users.d` file, so **the way back to the bad state is both the flag and a container
without that file.**

### Restarting it

`--restart unless-stopped` covers a host reboot and a crash, so the ordinary answer is that
nothing has to be done. When it does:

```bash
ssh denisbezrukov@20.240.194.2
docker start sup840-handbook-ch                 # or: docker restart sup840-handbook-ch
docker exec sup840-handbook-ch clickhouse-client -q 'select count() from handbook.documents'
```

64 is the answer. Two more lines are worth running after any re-create, because both failures
they catch are silent:

```bash
# `default` must NOT answer over the subnet address. `Code: 516` back is the pass
curl -s "http://10.10.1.10:8123/?user=default" --data-binary 'select 1'

# and the accounts must read as: `default` with no host_ip at all (loopback is reported
# under host_names as `localhost`), `handbook_reader` with the subnet
docker exec sup840-handbook-ch clickhouse-client -q \
  "select name, host_ip, host_names from system.users order by name"
```

Both paths it mounts — the volume and `/opt/marketplace/sup840-ch/users.xml`
— are durable, which is the whole point of them being where they are: an earlier incarnation
kept the users file under `/tmp`, where a reboot would have left Docker creating an empty
*directory* in its place and ClickHouse starting with no reader account and no error anyone
would connect to the dataset.

### Re-creating it from nothing

If the container and its volume are both gone, this rebuilds the corpus the gate expects. Steps
1 and 2 run on the marketplace host; step 3 needs a checkout of this repository there, or the
`.tsv` generated anywhere and copied over. The generator is deterministic, so the rebuilt table
is the same data rather than similar data:

```bash
# 1. the container, on the two durable paths
# No CLICKHOUSE_SKIP_USER_SETUP here — see "What actually protects the corpus" above
docker run -d --name sup840-handbook-ch --restart unless-stopped \
  -p 10.10.1.10:8123:8123 \
  -v sup840-handbook-data:/var/lib/clickhouse \
  -v /opt/marketplace/sup840-ch/users.xml:/etc/clickhouse-server/users.d/sup840.xml:ro \
  clickhouse/clickhouse-server:24.8

# 2. the table
docker exec sup840-handbook-ch clickhouse-client -q "create database if not exists handbook"
docker exec sup840-handbook-ch clickhouse-client -q "
  create table if not exists handbook.documents
  (doc_id String, title String, text String, updated_at DateTime)
  engine = MergeTree order by doc_id"

# 3. the rows, from this repository
datasets/acme-engineering-handbook/generate/generate.py --self-test     # sha256 08afca80885ea9cd…
datasets/acme-engineering-handbook/generate/generate.py --out handbook.tsv
docker exec -i sup840-handbook-ch clickhouse-client -q \
  "insert into handbook.documents format TabSeparated" < handbook.tsv
```

The generator escapes tabs, backslashes and newlines the way `TabSeparated` wants, so the 64
documents survive the round trip with their line breaks intact — verified by reading the table
back out and comparing it to the generator's own output, which differ only in ClickHouse's own
escaping of `'` on export.

`users.xml` is the one thing this repository cannot regenerate: it carries the reader's password,
which is a stand secret and lives on the host and in the dataset's publisher secrets. Recovering
it means writing a new password into both.
