#!/usr/bin/env python3
"""Check the generated corpus against the properties the demonstration depends on.

    ./selftest.py              # a small corpus, roughly fifteen seconds
    ./selftest.py --patients 12000

What it asserts is not "the script ran". It is the set of claims the dataset card
makes and the demonstration relies on, every one of which has been wrong at some
point while this was being written:

  * **Determinism.** Two runs with the same seed produce identical bytes, tables
    and documents alike. Without this the card's reproducibility claim is false
    and nobody can tell a regenerated corpus from a different one.
  * **Referential integrity.** Every foreign key resolves. A text-to-SQL agent
    joins on what the card declares in `references`, and a dangling key turns
    into a silently empty result rather than an error.
  * **The abstraction gap is real.** A procedure performed elsewhere has no
    findings, no pathology and no surveillance recommendation anywhere in the
    structured record. If any leaks in, the scenario proves nothing.
  * **The overdue cohort is real.** Every recall marked overdue has a due date in
    the past, a positive day count and no completed examination.
  * **The planted contradictions are present**, all five, and each names a patient
    who exists.
  * **Nothing is dated in the future** relative to the as-of date.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import generate  # noqa: E402

AS_OF = generate.AS_OF.isoformat()

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  ok    {message}")
    else:
        print(f"  FAIL  {message}")
        failures.append(message)


def columns_of(schema: str, table: str) -> list[str]:
    """Columns of one table, read out of the rendered schema.sql by its bare name."""
    match = re.search(
        r"CREATE TABLE \w+\.\w*?" + table + r"\s*\((.*?)\n\) ENGINE", schema, re.S
    )
    assert match, f"no DDL for {table}"
    return [line.strip().split()[0] for line in match.group(1).strip().split("\n") if line.strip()]


def load(directory: str, schema: str, table: str) -> list[dict]:
    names = columns_of(schema, table)
    rows = []
    with open(os.path.join(directory, f"{table}.tsv"), encoding="utf-8") as handle:
        for line in handle:
            rows.append(dict(zip(names, line.rstrip("\n").split("\t"))))
    return rows


def tree_digest(directory: str) -> str:
    digest = hashlib.sha256()
    for root, dirs, names in os.walk(directory):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            digest.update(os.path.relpath(path, directory).encode())
            with open(path, "rb") as handle:
                digest.update(handle.read())
    return digest.hexdigest()


def run(out: str, patients: int, seed: int) -> None:
    code = generate.main(["--out", out, "--patients", str(patients), "--seed", str(seed)])
    assert code == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patients", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=generate.DEFAULT_SEED)
    parser.add_argument("--keep", action="store_true", help="do not delete the generated corpora")
    args = parser.parse_args()

    workdir = tempfile.mkdtemp(prefix="cbdh-selftest-")
    first = os.path.join(workdir, "a")
    second = os.path.join(workdir, "b")

    print("generating twice with the same seed")
    run(first, args.patients, args.seed)
    run(second, args.patients, args.seed)

    print("\ndeterminism")
    check(tree_digest(first) == tree_digest(second), "two runs of one seed are byte-identical")

    schema = open(os.path.join(first, "schema.sql"), encoding="utf-8").read()

    tables = {name: load(first, schema, name) for name in generate.TABLES}

    print("\nshape")
    for name in generate.TABLES:
        check(len(tables[name]) > 0, f"{name} is not empty ({len(tables[name]):,} rows)")
    for name in generate.TABLES:
        width = len(columns_of(schema, name))
        bad = [r for r in tables[name] if len(r) != width]
        check(not bad, f"{name}: every row has {width} columns")

    print("\nreferential integrity")
    patients = {r["patient_id"] for r in tables["patients"]}
    procedures = {r["procedure_id"] for r in tables["procedures"]}
    findings = {r["finding_id"] for r in tables["findings"]}
    recommendations = {r["recommendation_id"] for r in tables["surveillance_recommendations"]}
    recalls = {r["recall_id"] for r in tables["recalls"]}
    providers = {r["provider_id"] for r in tables["providers"]}
    sites = {r["site_id"] for r in tables["sites"]}

    for table, column, universe, label in [
        ("procedures", "patient_id", patients, "patients.patient_id"),
        ("encounters", "patient_id", patients, "patients.patient_id"),
        ("encounters", "provider_id", providers, "providers.provider_id"),
        ("encounters", "site_id", sites, "sites.site_id"),
        ("findings", "procedure_id", procedures, "procedures.procedure_id"),
        ("findings", "patient_id", patients, "patients.patient_id"),
        ("pathology", "finding_id", findings, "findings.finding_id"),
        ("pathology", "procedure_id", procedures, "procedures.procedure_id"),
        ("surveillance_recommendations", "procedure_id", procedures, "procedures.procedure_id"),
        ("surveillance_recommendations", "superseded_by", procedures, "procedures.procedure_id"),
        ("recalls", "recommendation_id", recommendations, "recommendations"),
        ("recalls", "patient_id", patients, "patients.patient_id"),
        ("recalls", "completed_procedure_id", procedures, "procedures.procedure_id"),
        ("recall_outreach", "recall_id", recalls, "recalls.recall_id"),
        ("clinical_documents", "procedure_id", procedures, "procedures.procedure_id"),
        ("clinical_documents", "patient_id", patients, "patients.patient_id"),
    ]:
        orphans = [r for r in tables[table] if r[column] not in ("", "\\N") and r[column] not in universe]
        check(not orphans, f"{table}.{column} resolves to {label} ({len(orphans)} orphans)")

    print("\nthe abstraction gap")
    outside = {r["procedure_id"] for r in tables["procedures"] if r["report_source"] == "Outside report"}
    check(len(outside) > 0, f"the corpus contains outside reports ({len(outside)})")
    check(
        not (outside & {r["procedure_id"] for r in tables["findings"]}),
        "no outside report has a findings row",
    )
    check(
        not (outside & {r["procedure_id"] for r in tables["pathology"]}),
        "no outside report has a pathology row",
    )
    check(
        not (outside & {r["procedure_id"] for r in tables["surveillance_recommendations"]}),
        "no outside report has a surveillance recommendation",
    )
    for row in tables["procedures"]:
        if row["report_source"] != "Outside report":
            continue
        if any(row[c] not in ("", "\\N") for c in ("bbps_total", "cecal_intubation", "withdrawal_time_min")):
            check(False, f"{row['procedure_id']} leaks a structured field into an outside report")
            break
    else:
        check(True, "no outside report carries prep, intubation or withdrawal time")

    print("\nthe overdue cohort")
    overdue = [r for r in tables["recalls"] if r["recall_status"] in ("overdue", "lost_to_follow_up")]
    check(len(overdue) >= 20, f"there is a cohort to find ({len(overdue)} overdue recalls)")
    check(all(r["due_date"] < AS_OF for r in overdue), "every overdue recall is past its due date")
    check(all(int(r["days_overdue"]) > 0 for r in overdue), "every overdue recall has a positive day count")
    check(
        all(r["completed_procedure_id"] == "" for r in overdue),
        "no overdue recall has a completed examination",
    )
    completed = [r for r in tables["recalls"] if r["recall_status"] == "completed"]
    check(
        all(r["completed_procedure_id"] != "" for r in completed),
        "every completed recall names the examination that answered it",
    )
    check(
        all(int(r["days_overdue"]) == 0 for r in tables["recalls"] if r["recall_status"] not in
            ("overdue", "lost_to_follow_up")),
        "no recall outside the overdue cohort carries a day count",
    )

    print("\ndates")
    for table, column in [
        ("procedures", "procedure_date"),
        ("encounters", "encounter_date"),
        ("pathology", "reported_date"),
        ("recall_outreach", "attempt_date"),
        ("patients", "registered_on"),
    ]:
        future = [r for r in tables[table] if r[column] >= AS_OF]
        check(not future, f"{table}.{column} is never on or after the as-of date ({len(future)})")
    backwards = [
        r for r in tables["surveillance_recommendations"] if r["due_date"] < r["recommended_on"]
    ]
    check(not backwards, "a surveillance recommendation is never due before it was made")
    backwards = [r for r in tables["pathology"] if r["reported_date"] < r["received_date"]]
    check(not backwards, "pathology is never reported before the specimen arrived")

    print("\ndocuments")
    notes = os.path.join(first, "notes")
    registered = {r["document_filename"] for r in tables["clinical_documents"] if r["document_filename"]}
    on_disk = set(os.listdir(notes))
    check(registered == on_disk, f"clinical_documents names exactly the files on disk ({len(on_disk)})")
    check(
        all(os.path.getsize(os.path.join(notes, name)) > 500 for name in on_disk),
        "no generated document is a stub",
    )
    formats = {os.path.splitext(name)[1] for name in on_disk}
    check(formats == {".pdf", ".docx", ".txt"}, f"all three formats are produced ({sorted(formats)})")

    manifest = open(os.path.join(first, "MANIFEST.md"), encoding="utf-8").read()
    import notes as notes_module

    for contradiction in notes_module.CONTRADICTIONS:
        check(
            contradiction["kind"] in manifest,
            f"the planted contradiction {contradiction['key']!r} reached the manifest",
        )

    # Every identifying document must actually identify somebody who exists: a
    # note naming an MRN that is not in `patients` would make the reconciliation
    # scenario unanswerable.
    mrns = {r["mrn"] for r in tables["patients"]}
    named = 0
    for name in sorted(on_disk):
        if not name.endswith(".txt"):
            continue
        text = open(os.path.join(notes, name), encoding="utf-8").read()
        found = re.findall(r"CB-\d{7}", text)
        check(bool(found) and all(m in mrns for m in found), f"{name} names a real MRN")
        named += 1
    check(named > 0, "the text notes carry medical record numbers")

    print("\nthe dataset card is the agent's only grounding")
    card_path = os.path.join(os.path.dirname(HERE), "data.yaml")
    try:
        import yaml
    except ImportError:
        print("  skip  PyYAML not installed — card checks skipped")
    else:
        card = yaml.safe_load(open(card_path, encoding="utf-8"))
        prefix = generate.DEFAULT_TABLE_PREFIX
        declared = {
            t["name"]: [c["name"] for c in t["columns"]]
            for t in card["spec"]["schema"]["tables"]
        }
        actual = {prefix + name: columns_of(schema, name) for name in generate.TABLES}
        check(
            set(declared) == set(actual),
            f"the card declares exactly the tables the schema creates "
            f"(card only: {sorted(set(declared) - set(actual))}, "
            f"schema only: {sorted(set(actual) - set(declared))})",
        )
        mismatched = [t for t in sorted(set(declared) & set(actual)) if declared[t] != actual[t]]
        check(not mismatched, f"every table's columns match the card in name and order {mismatched}")
        incomplete = [
            f"{t['name']}.{c['name']}"
            for t in card["spec"]["schema"]["tables"]
            for c in t["columns"]
            if c.get("role") not in ("measure", "dimension") or not c.get("description")
        ]
        check(not incomplete, f"every column declares a role and a description {incomplete[:5]}")
        unresolved = [
            c["references"]
            for t in card["spec"]["schema"]["tables"]
            for c in t["columns"]
            if "references" in c
            and (
                c["references"].split(".")[0] not in declared
                or c["references"].split(".")[1] not in declared[c["references"].split(".")[0]]
            )
        ]
        check(not unresolved, f"every declared foreign key resolves inside the card {unresolved}")
        connection = card["spec"]["provides"][0]["connection"]["static"]
        check(
            connection["database"] == generate.DEFAULT_DATABASE,
            f"the card points at the database the generator loads into "
            f"({connection['database']})",
        )
        check(
            all(name.startswith(prefix) for name in declared),
            f"every table the card declares carries the published prefix {prefix!r}",
        )

    print("\npdftotext round-trip" if shutil.which("pdftotext") else "\npdftotext not installed — skipped")
    if shutil.which("pdftotext"):
        sample = sorted(n for n in on_disk if n.endswith(".pdf"))[0]
        text = subprocess.run(
            ["pdftotext", "-layout", os.path.join(notes, sample), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        check("MRN:" in text, f"{sample} extracts as text with its identifiers intact")
        check("?" not in text.replace("? ", ""), f"{sample} extracts without mojibake")

    if not args.keep:
        shutil.rmtree(workdir)
    else:
        print(f"\ncorpora kept in {workdir}")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
