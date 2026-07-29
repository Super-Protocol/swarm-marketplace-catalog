#!/usr/bin/env python3
"""Ground the agent in the datasets that were bound to it.

The agent answers questions by finding relevant tables in a Neo4j knowledge graph, generating
SQL against them, and running that SQL through a registered data source. Both of those have to
exist before the first question, and both describe the *same* datasets — so both are built here,
from one input: the bindings the marketplace resolved for the agent's data slot.

Each binding carries the connection (how to reach the data) and the schema its publisher
declared (what the data means). That second half is why this can be automatic: the description
the knowledge graph needs is the description the dataset already publishes. Nobody re-types it,
and it cannot drift from the card the consumer chose.

Everything here is idempotent. A reconfigure that adds or removes a dataset re-runs it, and the
result is the graph and the registrations that match the current bindings — no more, no less.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import psycopg
from neo4j import GraphDatabase
from openai import OpenAI

BINDINGS = json.loads(os.environ["BINDINGS"] or "[]")
NEO4J_URI = os.environ["NEO4J_URI"]
NEO4J_USER = os.environ["NEO4J_USER"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
NEO4J_DB = os.environ.get("NEO4J_DB", "neo4j")
EMBED_MODEL = os.environ["EMBED_MODEL"]
EMBED_DIM = int(os.environ["EMBED_DIM"])
KEYCLOAK_URL = os.environ["KEYCLOAK_URL"].rstrip("/")
KEYCLOAK_REALM = os.environ["KEYCLOAK_REALM"]
KEYCLOAK_CLIENT_ID = os.environ["KEYCLOAK_CLIENT_ID"]
KEYCLOAK_CLIENT_SECRET = os.environ["KEYCLOAK_CLIENT_SECRET"]

openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def log(message):
    print(message, flush=True)


# ---------------------------------------------------------------------------
# The tenant
# ---------------------------------------------------------------------------


def post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data)) as response:
        return json.load(response)


def get_json(url, token):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def resolve_tenant(attempts=120, delay=5):
    """The agent's tenant is a Keycloak organization, and its id is generated at bootstrap.

    Asking Keycloak for it here, rather than threading it out of the bootstrap Job, means the two
    Jobs share no state and either can be re-run alone. It also means waiting: this runs while the
    rest of the deployment is still starting, and an identity provider that is not up yet is the
    normal first answer, not a failure.
    """
    for attempt in range(attempts):
        try:
            return ask_for_tenant()
        except Exception as error:  # noqa: BLE001 — anything here means "not ready yet"
            if attempt == attempts - 1:
                raise
            if attempt % 6 == 0:
                log(f"waiting for the identity provider ({error.__class__.__name__})")
            time.sleep(delay)


def ask_for_tenant():
    token = post_form(
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token",
        {
            "grant_type": "client_credentials",
            "client_id": KEYCLOAK_CLIENT_ID,
            "client_secret": KEYCLOAK_CLIENT_SECRET,
        },
    )["access_token"]

    organizations = get_json(f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/organizations", token)
    if not organizations:
        raise RuntimeError("Keycloak has no organization yet; the identity bootstrap has not finished")
    return organizations[0]["id"]


# ---------------------------------------------------------------------------
# Bindings -> the shapes the agent stores
# ---------------------------------------------------------------------------


def identifier(name):
    """A dataset name is a catalogue slug; a data source id has to be a SQL-safe identifier."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def source_config(connection):
    """The agent's ClickHouse client speaks HTTP and wants the protocol spelled out."""
    return {
        "host": connection["host"],
        "port": connection.get("port", 8123),
        "protocol": "https:" if connection.get("tls") else "http:",
        "username": connection.get("username", "default"),
        "password": connection.get("password", ""),
    }


def numeric(column):
    return column.get("type") in ("integer", "number")


def role_of(column):
    """`role` is what the publisher declared; the type is only a fallback for older cards."""
    declared = column.get("role")
    if declared in ("measure", "dimension"):
        return declared
    return "measure" if numeric(column) else "dimension"


# ---------------------------------------------------------------------------
# PostgreSQL: what the agent must have registered
# ---------------------------------------------------------------------------


def dsn():
    return (
        f"host={os.environ['PG_HOST']} port={os.environ['PG_PORT']} "
        f"user={os.environ['PG_USER']} password={os.environ['PG_PASSWORD']} "
        f"dbname={os.environ['PG_DATABASE']}"
    )


def wait_for_schema(attempts=120, delay=5):
    """The agent creates its own tables on first boot; this Job has none of its own to create.

    Waiting here rather than ordering the deployment means a slow first start delays grounding
    instead of failing it.
    """
    for attempt in range(attempts):
        try:
            with psycopg.connect(dsn(), autocommit=True) as connection:
                connection.execute("SELECT 1 FROM llm_models LIMIT 1")
                connection.execute("SELECT 1 FROM tenant_data_sources LIMIT 1")
                return
        except Exception as error:  # noqa: BLE001 — any failure here means "not ready yet"
            if attempt == attempts - 1:
                raise
            if attempt % 6 == 0:
                log(f"waiting for the agent's schema ({error.__class__.__name__})")
            time.sleep(delay)


def seed_postgres(tenant, bindings):
    with psycopg.connect(dsn(), autocommit=True) as connection:
        cursor = connection.cursor()

        log("== default model ==")
        cursor.execute("DELETE FROM llm_models WHERE provider = %s", (os.environ["LLM_PROVIDER"],))
        cursor.execute(
            """
            INSERT INTO llm_models
              (id, provider, name, config, "graphType", "largeDataRowThreshold",
               "largeDataSizeThreshold", "previewRowCount", "contextWindowSize",
               "memoryTokenBudget", "summarizeTokenThreshold", enabled, "isDefault")
            VALUES (gen_random_uuid(), %s, %s, %s, 'default', 100, 102400, 20, 1000000, 32000, 40000, true, true)
            """,
            (
                os.environ["LLM_PROVIDER"],
                os.environ["LLM_MODEL"],
                json.dumps(
                    {
                        "provider": os.environ["LLM_PROVIDER"],
                        "apiKey": os.environ["LLM_API_KEY"],
                        "model": os.environ["LLM_MODEL"],
                        "temperature": 1,
                        "topP": 1,
                    }
                ),
            ),
        )

        log("== knowledge base provider ==")
        cursor.execute(
            """
            INSERT INTO knowledge_base_providers
              (id, name, uri, username, password, database, "maxConnectionPoolSize",
               "connectionAcquisitionTimeout", "maxTransactionRetryTime", "logLevel",
               "isActive", "createdAt", "updatedAt")
            VALUES (gen_random_uuid(), 'marketplace_kb', %s, %s, %s, %s, 50, 60000, 30000, 'info', true, now(), now())
            ON CONFLICT (name) DO UPDATE SET uri = EXCLUDED.uri, password = EXCLUDED.password, "updatedAt" = now()
            RETURNING id
            """,
            (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DB),
        )
        kb_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO tenant_knowledge_bases (id, "tenantId", "knowledgeBaseProviderId", "createdAt", "updatedAt")
            VALUES (gen_random_uuid(), %s, %s, now(), now())
            ON CONFLICT ("tenantId") DO UPDATE
              SET "knowledgeBaseProviderId" = EXCLUDED."knowledgeBaseProviderId", "updatedAt" = now()
            """,
            (tenant, kb_id),
        )

        log("== data sources ==")
        wanted = []
        for binding in bindings:
            source_id = identifier(binding["dataset"])
            wanted.append(source_id)
            cursor.execute(
                """
                INSERT INTO data_source_providers
                  (id, name, type, config, description, "isActive", "createdAt", "updatedAt")
                VALUES (gen_random_uuid(), %s, 'clickhouse', %s, %s, true, now(), now())
                ON CONFLICT (name) DO UPDATE
                  SET config = EXCLUDED.config, description = EXCLUDED.description, "updatedAt" = now()
                RETURNING id
                """,
                (
                    source_id,
                    json.dumps(source_config(binding["connection"])),
                    binding.get("title", binding["dataset"]),
                ),
            )
            provider_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO tenant_data_sources
                  (id, "tenantId", "datasourceId", "dataSourceProviderId", "createdAt", "updatedAt")
                VALUES (gen_random_uuid(), %s, %s, %s, now(), now())
                ON CONFLICT ("tenantId", "datasourceId") DO UPDATE
                  SET "dataSourceProviderId" = EXCLUDED."dataSourceProviderId", "updatedAt" = now()
                """,
                (tenant, source_id, provider_id),
            )
            log(f"  {source_id} -> {binding['connection']['host']}/{binding['connection']['database']}")

        # Unbinding a dataset has to revoke the agent's access to it, not merely stop mentioning
        # it: a stale row would leave working credentials for data the consumer no longer holds.
        cursor.execute(
            'DELETE FROM tenant_data_sources WHERE "tenantId" = %s AND NOT ("datasourceId" = ANY(%s))',
            (tenant, wanted),
        )

        log("== suggestions ==")
        cursor.execute('DELETE FROM initial_suggestions WHERE "knowledgeBaseProviderId" = %s', (kb_id,))
        for index, text in enumerate(suggestions(bindings)):
            cursor.execute(
                """
                INSERT INTO initial_suggestions
                  (id, "knowledgeBaseProviderId", text, "orderIndex", "isActive", "createdAt", "updatedAt")
                VALUES (gen_random_uuid(), %s, %s, %s, true, now(), now())
                """,
                (kb_id, text, index),
            )

        log("== system messages ==")
        cursor.execute("DELETE FROM system_messages WHERE name = 'Technical part' AND \"tenantId\" IS NULL")
        cursor.execute(
            'INSERT INTO system_messages (id, name, index, "tenantId", "modelRegex", text)'
            " VALUES (gen_random_uuid(), 'Technical part', 0, NULL, NULL, %s)",
            (open("/scripts/technical-brief.md").read(),),
        )
        cursor.execute("DELETE FROM system_messages WHERE name = 'Domain part' AND \"tenantId\" = %s", (tenant,))
        cursor.execute(
            'INSERT INTO system_messages (id, name, index, "tenantId", "modelRegex", text)'
            " VALUES (gen_random_uuid(), 'Domain part', 10, %s, NULL, %s)",
            (tenant, domain_brief(bindings)),
        )


def suggestions(bindings):
    """Opening questions, drawn from what the bound datasets actually contain.

    Generic prompts ("what can you do?") teach a user nothing about their own data; naming a
    real table from a real card shows them the shape of a question that will work.
    """
    lines = []
    for binding in bindings:
        title = binding.get("title", binding["dataset"])
        tables = binding.get("schema", {}).get("tables", [])
        if not tables:
            continue
        lines.append(f"What does the {title} dataset contain, and how many records are in it?")
        largest = max(tables, key=lambda table: len(table["columns"]))
        measures = [column["name"] for column in largest["columns"] if role_of(column) == "measure"]
        if measures:
            lines.append(f"In {title}, show the distribution of {measures[0].replace('_', ' ')}.")
    return lines[:6] or ["What data do I have access to?"]


def domain_brief(bindings):
    """The per-tenant half of the system prompt, written from the cards.

    The agent needs to know which data sources exist, what each is about, and which tables it
    holds — the same facts the publisher wrote on the listing. Generating it means a newly
    connected dataset is described to the model the moment it is bound.
    """
    parts = [
        "### Domain: the datasets connected to this workspace",
        "",
        "You answer questions about the data sources below. Each is a separate data source; "
        "`datasourceId` is the source id given here and equals the knowledge base "
        "`system.systemId`. Always ground table and column names in the knowledge base — never "
        "invent them.",
        "",
    ]
    for binding in bindings:
        source_id = identifier(binding["dataset"])
        title = binding.get("title", binding["dataset"])
        database = binding["connection"]["database"]
        parts.append(f"**{title}** — data source `{source_id}`, database (namespace) `{database}`.")
        if binding.get("description"):
            parts.append("")
            parts.append(binding["description"])
        tables = binding.get("schema", {}).get("tables", [])
        if tables:
            parts.append("")
            parts.append("Tables:")
            for table in tables:
                description = table.get("description", "")
                parts.append(f"- `{table['name']}` — {description}" if description else f"- `{table['name']}`")
        parts.append("")

    parts.append(
        "Answer at cohort level: counts, averages, distributions and trends. Do not return or "
        "reconstruct individual records."
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Neo4j: the graph the agent searches
# ---------------------------------------------------------------------------

# Marks every node this job owns, so pruning can tell them from a semantic layer someone else
# curated in the same graph.
MANAGED_BY = "swarm-marketplace"

CONSTRAINTS = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (s:System)    REQUIRE s.systemId    IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Namespace) REQUIRE n.namespaceId IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (d:Dataset)   REQUIRE d.datasetId   IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Field)     REQUIRE f.fieldId     IS UNIQUE",
]

# The agent's search probes all of these labels; the indexes must exist even for the labels
# this loader never writes, or a search against them fails instead of returning nothing.
VECTOR_INDEXES = [
    ("kb_field_embedding_idx", "Field"),
    ("kb_dataset_embedding_idx", "Dataset"),
    ("kb_searchable_embedding_idx", "Searchable"),
    ("kb_measure_embedding_idx", "Measure"),
    ("kb_dimension_embedding_idx", "Dimension"),
    ("kb_concept_embedding_idx", "Concept"),
]


def embed(texts):
    vectors = []
    for start in range(0, len(texts), 128):
        chunk = texts[start : start + 128]
        response = openai.embeddings.create(model=EMBED_MODEL, input=chunk)
        vectors.extend(item.embedding for item in response.data)
    return vectors


def build_graph(bindings):
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    to_embed = []

    with driver.session(database=NEO4J_DB) as session:
        for statement in CONSTRAINTS:
            session.run(statement)
        for name, label in VECTOR_INDEXES:
            session.run(
                f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON n.embedding "
                "OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: 'cosine'}}",
                dim=EMBED_DIM,
            )

        # A dataset that is no longer bound must stop being findable; leaving it in the graph
        # would have the agent generate SQL against data it can no longer reach.
        #
        # Scoped to nodes this job wrote. The agent's graph can also hold a semantic layer —
        # concepts, terms, curated measures — that nobody here created, and a graph belonging to
        # more than one source is exactly the case where "delete what I did not just write" is
        # the difference between pruning and wiping.
        keep = [identifier(binding["dataset"]) for binding in bindings]
        session.run(
            "MATCH (s:System {managedBy:$owner}) WHERE NOT s.systemId IN $keep "
            "OPTIONAL MATCH (n:Namespace {managedBy:$owner})-[:BELONGS_TO]->(s) "
            "OPTIONAL MATCH (d:Dataset {managedBy:$owner})-[:BELONGS_TO]->(n) "
            "OPTIONAL MATCH (f:Field {managedBy:$owner})-[:BELONGS_TO]->(d) "
            "DETACH DELETE f, d, n, s",
            keep=keep,
            owner=MANAGED_BY,
        )

        for binding in bindings:
            system_id = identifier(binding["dataset"])
            namespace = binding["connection"]["database"]
            title = binding.get("title", binding["dataset"])

            session.run(
                "MERGE (s:System {systemId:$id}) SET s.displayName=$name, s.engine='clickhouse', s.managedBy=$owner",
                id=system_id,
                name=title,
                owner=MANAGED_BY,
            )
            session.run(
                "MERGE (n:Namespace {namespaceId:$ns}) SET n.name=$ns, n.managedBy=$owner "
                "WITH n MATCH (s:System {systemId:$id}) MERGE (n)-[:BELONGS_TO]->(s)",
                ns=namespace,
                id=system_id,
                owner=MANAGED_BY,
            )

            for table in binding.get("schema", {}).get("tables", []):
                dataset_id = f"{namespace}.{table['name']}"
                description = table.get("description", table["name"])
                search_text = f"table: {dataset_id} | description: {description}"
                session.run(
                    "MERGE (d:Dataset {datasetId:$id}) SET d:Searchable, d.name=$name, "
                    "d.description=$description, d.searchText=$text, d.managedBy=$owner "
                    "WITH d MATCH (n:Namespace {namespaceId:$ns}) MERGE (d)-[:BELONGS_TO]->(n)",
                    id=dataset_id,
                    name=table["name"],
                    description=description,
                    text=search_text,
                    ns=namespace,
                    owner=MANAGED_BY,
                )
                to_embed.append(("Dataset", "datasetId", dataset_id, search_text))

                for column in table["columns"]:
                    field_id = f"{dataset_id}.{column['name']}"
                    role = role_of(column)
                    comment = column.get("description", "")
                    field_text = (
                        f"field: {column['name']} | table: {dataset_id} | role: {role} | description: {comment}"
                    )
                    session.run(
                        "MERGE (f:Field {fieldId:$id}) SET f:Searchable, f.name=$name, f.role=$role, "
                        "f.type=$type, f.comment=$comment, f.searchText=$text, f.managedBy=$owner "
                        "WITH f MATCH (d:Dataset {datasetId:$dataset}) MERGE (f)-[:BELONGS_TO]->(d)",
                        id=field_id,
                        name=column["name"],
                        role=role,
                        type=column.get("type", "string"),
                        comment=comment,
                        text=field_text,
                        dataset=dataset_id,
                        owner=MANAGED_BY,
                    )
                    to_embed.append(("Field", "fieldId", field_id, field_text))

            # Joins, declared by the publisher rather than guessed from column names.
            for table in binding.get("schema", {}).get("tables", []):
                for column in table["columns"]:
                    if not column.get("references"):
                        continue
                    target_table, target_column = column["references"].split(".", 1)
                    session.run(
                        "MATCH (source:Field {fieldId:$source}) MATCH (target:Field {fieldId:$target}) "
                        "MERGE (source)-[:REFERENCES]->(target)",
                        source=f"{namespace}.{table['name']}.{column['name']}",
                        target=f"{namespace}.{target_table}.{target_column}",
                    )

        if to_embed:
            log(f"== embedding {len(to_embed)} nodes ==")
            for (label, key, node_id, _), vector in zip(to_embed, embed([item[3] for item in to_embed])):
                session.run(
                    f"MATCH (n:{label} {{{key}:$id}}) "
                    "CALL db.create.setNodeVectorProperty(n, 'embedding', $vector) "
                    "SET n.embeddingUpdatedAt = datetime()",
                    id=node_id,
                    vector=vector,
                )

        counts = session.run(
            "MATCH (s:System) WITH count(s) AS systems "
            "MATCH (n:Namespace) WITH systems, count(n) AS namespaces "
            "MATCH (d:Dataset) WITH systems, namespaces, count(d) AS datasets "
            "MATCH (f:Field) RETURN systems, namespaces, datasets, count(f) AS fields"
        ).single()
        log(
            f"graph: systems={counts['systems']} namespaces={counts['namespaces']} "
            f"datasets={counts['datasets']} fields={counts['fields']}"
        )

    driver.close()


def main():
    if not BINDINGS:
        log("no datasets are bound to this deployment; nothing to ground")
        return

    log(f"grounding {len(BINDINGS)} dataset(s)")
    wait_for_schema()
    tenant = resolve_tenant()
    log(f"tenant = {tenant}")
    build_graph(BINDINGS)
    seed_postgres(tenant, BINDINGS)
    log("done")


if __name__ == "__main__":
    sys.exit(main())
