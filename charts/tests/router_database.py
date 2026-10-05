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
  * the master Service must select the **router**, and the router's primary listener.
    Pointed at the database's own pods it would resolve to all three instances, two of
    which refuse writes — so a third of the queries fail and the rest work.
  * the three passwords come out of the Secret the api chart writes, under the keys
    the database chart is told to read. A key that does not exist is a pod that never
    starts, and the message is about the Secret rather than about the names.
  * three instances, and required anti-affinity. `preferred` renders the same and
    schedules two copies of the data onto one ephemeral disk.
  * no part of the database may need the Kubernetes API. That is what 0.1.0 got wrong
    and what SUP-215 is: a cluster space blocks pod → kube-apiserver, so the
    Endpoints-mode chart deployed cleanly and never elected anybody.

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
    router = by_kind_name.get(("Deployment", "confidential-router-postgresql-router"))
    dcs = by_kind_name.get(("StatefulSet", "confidential-router-postgresql-etcd"))
    if api is None or database is None or router is None or dcs is None:
        fail("the composed render is missing the API Deployment, the database StatefulSet, the router or the DCS")
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

        # The router's pods and nothing else: pointed at the database's own pods, two
        # of the three it resolved to would refuse every write.
        selector = master["spec"].get("selector")
        router_labels = router["spec"]["template"]["metadata"]["labels"]
        if not selector:
            fail(f"Service/{host} has no selector, so nothing is behind it")
        elif not all(router_labels.get(k) == v for k, v in selector.items()):
            fail(f"Service/{host} selects {selector}, which does not match the router's pods")
        elif selector.items() <= database["spec"]["template"]["metadata"]["labels"].items():
            fail(f"Service/{host} selects the database's own pods, two of which refuse writes")
        else:
            ok(f"Service/{host} selects the router's pods")

        ports = master["spec"]["ports"]
        if [p.get("name") for p in ports] != ["postgresql"]:
            fail(f"Service/{host} ports are {ports}: the API's DSN wants one named `postgresql`")
        elif ports[0]["port"] != 5432:
            fail(f"Service/{host} serves port {ports[0]['port']}, and the DSN says 5432")
        elif ports[0]["targetPort"] != "primary":
            fail(f"Service/{host} targets {ports[0]['targetPort']!r}, not the router's `primary` listener")
        else:
            ok("the master Service's port is `postgresql` on 5432, targeting the router's primary listener")

    # --- the Patroni scope is that same name: a convention, kept so the two read together ---
    spilo = {
        env["name"]: env
        for container in database["spec"]["template"]["spec"]["containers"]
        for env in container.get("env", [])
    }
    if spilo.get("SCOPE", {}).get("value") != host:
        fail(f"the Patroni scope is {spilo.get('SCOPE', {}).get('value')!r} and the master Service is {host!r}")
    else:
        ok(f"the Patroni scope is {host}, the same string as the master Service and the DSN's host")

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

    # --- the DCS is this release's etcd, and nothing here talks to the API server ---
    if spilo.get("DCS_ENABLE_KUBERNETES_API") is not None:
        fail("the database is configured with the Kubernetes API as its DCS, which a cluster space blocks (SUP-215)")
    elif any(name.startswith("KUBERNETES_") for name in spilo):
        fail(f"the database still carries Kubernetes-DCS environment: {sorted(n for n in spilo if n.startswith('KUBERNETES_'))}")
    elif "ETCD3_HOSTS" not in spilo:
        fail("the database has neither a Kubernetes DCS nor ETCD3_HOSTS: Patroni would have no store at all")
    else:
        hosts = spilo["ETCD3_HOSTS"]["value"].split(",")
        members = dcs["spec"]["replicas"]
        if len(hosts) != members:
            fail(f"Patroni is given {len(hosts)} etcd endpoint(s) and the DCS has {members} member(s)")
        elif not all(h.startswith("confidential-router-postgresql-etcd-") for h in hosts):
            fail(f"Patroni's etcd endpoints are {hosts}, which are not this release's members")
        else:
            ok(f"the DCS is {members} in-namespace etcd members, by their own names")

    # Not a preference. A token in the pod is the only way an API call could be
    # authenticated, so its absence is the check that the claim above stays true.
    for kind, document in (("database", database), ("DCS", dcs), ("router", router)):
        if document["spec"]["template"]["spec"].get("automountServiceAccountToken") is not False:
            fail(f"the {kind} pods mount a service account token, so an API call could still be made")
        else:
            ok(f"the {kind} pods are given no service account token")

    # A namespace rendered into a manifest makes the deployment's evidence digest a
    # property of who deployed it, and `$(POD_NAMESPACE)`/`${POD_NAMESPACE}` is how
    # every name in this chart avoids it. `database_drift.py` is what proves the
    # absence; this is what names the mechanism, so a future rewrite that hard-codes a
    # namespace fails here with a message about why.
    etcd_hosts = spilo.get("ETCD3_HOSTS", {}).get("value", "")
    if "$(POD_NAMESPACE)" not in etcd_hosts:
        fail(f"ETCD3_HOSTS is {etcd_hosts!r}: the namespace has to arrive from the downward API, not from the render")
    else:
        ok("the etcd endpoints take their namespace from the downward API")

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
