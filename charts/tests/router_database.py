#!/usr/bin/env python3
"""
The seam between confidential-router-api and the database chart it installs.

Two charts, composed by a dependency, and nothing at deploy time checks that they
agree. A golden diff cannot check it either: the api chart's goldens keep only the api
chart's own documents, exactly so that bumping the database chart does not rewrite
eleven of them. So this reads the composed render and compares the handful of strings
that have to be identical for the API to find its database at all.

Every one of these has been a real failure somewhere:

  * the DSN's host and the master Service's name are one string. A rename on either
    side deploys cleanly and the API retries a name that does not resolve.
  * the master Service must have **no** selector. Patroni writes the leader's address
    into its Endpoints itself; a selector hands that object to the endpoints
    controller, which points the name at all three instances — two of which refuse
    writes, so a third of the queries fail and the rest work.
  * the three passwords come out of the Secret the api chart writes, under the keys
    the database chart is told to read. A key that does not exist is a pod that never
    starts, and the message is about the Secret rather than about the names.
  * three instances, and required anti-affinity. `preferred` renders the same and
    schedules two copies of the data onto one ephemeral disk.

    cat manifests.yaml | charts/tests/router_database.py
"""

import sys

import yaml

EXPECTED_SECRET_KEYS = {
    "PGPASSWORD_SUPERUSER": "postgres-password",
    "PGPASSWORD_STANDBY": "replication-password",
}


def main() -> int:
    documents = [d for d in yaml.safe_load_all(sys.stdin) if d]
    by_kind_name = {(d["kind"], d["metadata"]["name"]): d for d in documents}
    failures = 0

    def fail(message: str) -> None:
        nonlocal failures
        print(f"  FAIL  {message}")
        failures += 1

    def ok(message: str) -> None:
        print(f"  ok    {message}")

    api = by_kind_name.get(("Deployment", "confidential-router-api"))
    database = by_kind_name.get(("StatefulSet", "confidential-router-postgresql"))
    if api is None or database is None:
        fail("the composed render is missing the API Deployment or the database StatefulSet")
        return 1

    # --- the DSN's host is the master Service, and the master Service exists ---
    containers = api["spec"]["template"]["spec"]["containers"]
    init_containers = api["spec"]["template"]["spec"].get("initContainers", [])
    dsn = next(
        env["value"]
        for container in containers + init_containers
        for env in container.get("env", [])
        if env["name"] == "ROUTER_DATABASE_URL" and "value" in env
    )
    host = dsn.split("@", 1)[1].split(":", 1)[0]
    master = by_kind_name.get(("Service", host))
    if master is None:
        fail(f"the API connects to {host}, and no Service of that name is rendered")
    else:
        ok(f"the API's DSN points at Service/{host}")

        if "selector" in master["spec"]:
            fail(
                f"Service/{host} has a selector. Patroni maintains its Endpoints itself; "
                "with a selector the name resolves to the standbys as well"
            )
        else:
            ok(f"Service/{host} has no selector, so Patroni's leader Endpoints is what answers")

        ports = master["spec"]["ports"]
        if [p.get("name") for p in ports] != ["postgresql"]:
            fail(f"Service/{host} ports are {ports}: Patroni writes one named `postgresql`")
        elif ports[0]["port"] != 5432:
            fail(f"Service/{host} serves port {ports[0]['port']}, and Patroni writes 5432")
        else:
            ok("the master Service's port is named `postgresql` on 5432, which is what Patroni writes")

    # --- the Patroni scope is that same name: in Endpoints mode they cannot differ ---
    spilo = {
        env["name"]: env
        for container in database["spec"]["template"]["spec"]["containers"]
        for env in container.get("env", [])
    }
    if spilo.get("SCOPE", {}).get("value") != host:
        fail(f"the Patroni scope is {spilo.get('SCOPE', {}).get('value')!r} and the master Service is {host!r}")
    else:
        ok(f"the Patroni scope is {host}, so the leader lock and the master Service are one object")

    # --- the passwords come out of the api chart's Secret, under keys it writes ---
    secret = by_kind_name.get(("Secret", "confidential-router-api"))
    written = set((secret or {}).get("stringData", {}))
    for variable, key in EXPECTED_SECRET_KEYS.items():
        reference = spilo.get(variable, {}).get("valueFrom", {}).get("secretKeyRef", {})
        if reference.get("name") != "confidential-router-api":
            fail(f"{variable} does not read from Secret/confidential-router-api")
        elif reference.get("key") != key:
            fail(f"{variable} reads {reference.get('key')!r}, expected {key!r}")
        elif key not in written:
            fail(f"{variable} reads {key!r}, which Secret/confidential-router-api does not write")
        else:
            ok(f"{variable} ← Secret/confidential-router-api[{key}]")

    volumes = {v["name"]: v for v in database["spec"]["template"]["spec"]["volumes"]}
    items = volumes.get("credentials", {}).get("secret", {}).get("items", [])
    if [i["key"] for i in items] != ["password"] or [i["path"] for i in items] != ["app-password"]:
        fail(f"the bootstrap credential mount is {items}, expected the `password` key as `app-password`")
    elif "password" not in written:
        fail("the bootstrap mount reads `password`, which Secret/confidential-router-api does not write")
    else:
        ok("the application role's password ← Secret/confidential-router-api[password]")

    # --- three instances, one per node, required ---
    if database["spec"]["replicas"] != 3:
        fail(f"the database is {database['spec']['replicas']} instance(s), expected 3")
    else:
        ok("three instances")

    anti = database["spec"]["template"]["spec"].get("affinity", {}).get("podAntiAffinity", {})
    required = anti.get("requiredDuringSchedulingIgnoredDuringExecution") or []
    if not required:
        fail("the database has no required pod anti-affinity: two instances could share one node's disk")
    elif required[0]["topologyKey"] != "kubernetes.io/hostname":
        fail(f"the anti-affinity topology key is {required[0]['topologyKey']!r}, expected the hostname")
    elif anti.get("preferredDuringSchedulingIgnoredDuringExecution"):
        fail("the database carries a preferred anti-affinity term alongside the required one")
    else:
        ok("one instance per node, required")

    # --- synchronous replication is on, and asks for one standby of the two ---
    configuration = yaml.safe_load(spilo["SPILO_CONFIGURATION"]["value"])["bootstrap"]["dcs"]
    if configuration.get("synchronous_mode") is not True:
        fail("synchronous_mode is not on: a leader that disappears would take acknowledged writes with it")
    elif configuration.get("synchronous_node_count") != 1:
        fail(f"synchronous_node_count is {configuration.get('synchronous_node_count')}, expected 1")
    else:
        ok("synchronous replication, one standby of the two required to confirm a commit")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
