#!/usr/bin/env python3
"""Check the generated corpus against the properties the demonstration depends on.

    ./selftest.py                            # cypress-bay, a small corpus, under a second
    ./selftest.py --network ridgeline        # the network with the incomplete record
    ./selftest.py --all-networks             # every network in profiles.py
    ./selftest.py --published-size          # the size the card declares and the stand loads

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
  * **Nothing was found where the scope never went.** A flexible sigmoidoscopy is
    not a failed colonoscopy and earns no surveillance interval; an abandoned
    colonoscopy reports no polyp beyond the point it was abandoned at. Both of
    these were wrong in the first draft, and the first put 265 phantom patients
    into the overdue cohort the demonstration is about.
  * **A planted document is about a record that can still be acted on** — a live
    recall, a recommendation no later colonoscopy has answered, and an active
    patient. The showcase incomplete-exam document landed on a patient who had
    died once, and the manifest went on telling the operator the right answer was
    "a repeat within three months".
  * **Nobody is screened before screening starts** — "average-risk screening,
    age-appropriate" never appears on a patient under 45.
  * **An outreach outcome is possible on its channel**, and attempts ascend in
    date order.
  * **Incomplete capture is incomplete in the RECORD and nowhere else.** On a
    network whose forms are half-filled, a null bowel-preparation score does not
    come with a null completion status, a missing withdrawal time does not come
    with a missing cecal-intubation result, and a surveillance interval that
    never reached the structured record produces no recommendation row and
    therefore no recall at all. Each declared rate is checked against what the
    corpus actually contains, because a knob that silently stopped working would
    leave the cross-network comparison with nothing to compare.
  * **A lagging feed lags.** No structured procedure in a network declaring
    `feed_lag_days` is dated inside that window.

Run it for every network, not just the one you changed: the knobs are guarded on
being non-zero precisely so that the completely-captured networks take a
different path through the generator, and a single network's pass says nothing
about the other two.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import aggregate  # noqa: E402
import constants  # noqa: E402
import generate  # noqa: E402
import notes  # noqa: E402
import profiles  # noqa: E402
import vocabulary  # noqa: E402

AS_OF = generate.AS_OF.isoformat()

failures: list[str] = []

#: Suppresses the `ok` lines. The seed sweep runs the same assertions over a
#: dozen corpora and only the failures are worth reading.
QUIET = False


#: The pooled adenoma-detection table the stand answered on 2026-11-24 and
#: reported on SUP-909, per network: (examinations with an adenoma, examinations
#: with an adequate preparation, examinations excluded for a missing preparation
#: score). Over the three colonoscopy types, structured capture only, between
#: the two dates in ADR_WINDOW.
#:
#: It is pinned here because the aggregate release exists to answer exactly this
#: question for a researcher who holds no records, and "the counts reproduce the
#: records" is a weaker claim than "the counts reproduce THE ANSWER WE SHOWED".
#: If a change to the generator moves these, the stand's published figures moved
#: with it and the issue they were reported on is now wrong — which is the thing
#: worth failing a build over.
#: Column names that identify a patient, a specimen, a single procedure or a
#: clinician. Taken from the identifying corpus's own schema, and asserted
#: ABSENT from the aggregate release's card. A name check and a value check are
#: different checks: `aggregate.assert_no_record` catches an identifier that
#: reached the data, this catches a column that was declared to carry one.
IDENTIFYING_COLUMNS = {
    "patient_id", "mrn", "family_name", "given_name", "date_of_birth", "sex",
    "phone", "email", "address_line", "postal_code", "primary_provider_id",
    "encounter_id", "procedure_id", "provider_id", "finding_id", "pathology_id",
    "accession_number", "pathologist_id", "recommendation_id", "recall_id",
    "outreach_id", "document_id", "document_filename", "assigned_coordinator_id",
    "recommending_provider_id", "assigned_abstractor_id", "npi", "provider_name",
}

PUBLISHED_ADR = {
    "cypress-bay": (1825, 4788, 0),
    "harbor-point": (1396, 3052, 0),
    "ridgeline": (664, 2365, 1587),
}
ADR_WINDOW = ("2024-01-01", "2026-11-30")


def adr_from_records(tables: dict[str, list[dict]]) -> tuple[int, int, int]:
    """The researcher's question answered from the identifying corpus — the thing
    only a tenant holding the records can do."""
    start, end = ADR_WINDOW
    adenoma = {row["procedure_id"] for row in tables["pathology"] if row["adenoma"] == "1"}
    numerator = denominator = excluded = 0
    for row in tables["procedures"]:
        if row["procedure_type"] not in aggregate.COLONOSCOPY_TYPES:
            continue
        if not start <= row["procedure_date"] <= end:
            continue
        if row["report_source"] != aggregate.STRUCTURED:
            continue
        if aggregate.null(row["bbps_total"]):
            excluded += 1
        elif int(row["bbps_total"]) >= aggregate.ADEQUATE_BBPS:
            denominator += 1
            if row["procedure_id"] in adenoma:
                numerator += 1
    return numerator, denominator, excluded


def adr_from_release(rows: dict[str, list[list]]) -> tuple[int, int, int]:
    """The same question answered from the aggregate release — the thing the
    researcher's tenant can do, holding counts and nothing else.

    The month filter works because the window starts and ends on month
    boundaries, which is a property of the question and is asserted below rather
    than assumed."""
    start, end = ADR_WINDOW
    at = {name: index for index, name in enumerate(aggregate.release_columns("procedure_monthly"))}
    numerator = denominator = excluded = 0
    for row in rows["procedure_monthly"]:
        if row[at["procedure_type"]] not in aggregate.COLONOSCOPY_TYPES:
            continue
        if not start <= row[at["month"]] <= end:
            continue
        denominator += row[at["prep_adequate"]]
        numerator += row[at["prep_adequate_with_adenoma"]]
        excluded += row[at["prep_missing"]]
    return numerator, denominator, excluded


def check_release(corpus: str, tables: dict[str, list[dict]], profile, published: bool) -> None:
    """The aggregate release: that it answers the question, that it answers it
    with the SAME NUMBERS as the records, and that it carries no record."""
    rows = aggregate.aggregate(corpus, profile)

    for name in aggregate.TABLES:
        check(len(rows[name]) > 0, f"{name} is not empty ({len(rows[name]):,} rows)")

    # The loader splits schema.sql on the statement separator, because
    # ClickHouse's HTTP interface runs exactly one statement per request. A
    # separator inside a comment therefore starts a statement that is an English
    # sentence, and the load fails with a syntax error quoting the prose. This
    # cost one load of the stand.
    prose = [
        line
        for line in aggregate.DDL.split("\n")
        if line.strip().startswith("--") and ";" in line
    ]
    check(not prose, f"no comment in the release schema carries a statement separator {prose[:2]}")
    check(
        len(rows["procedure_monthly"]) < len(tables["procedures"]),
        f"the release is smaller than the record it counts "
        f"({len(rows['procedure_monthly']):,} rows against {len(tables['procedures']):,})",
    )

    # `adr_from_release` filters whole months, so it can only agree with a
    # day-level filter over the records if the window IS whole months. Asserted
    # rather than assumed: a window ending mid-month would make the two figures
    # differ for a reason that has nothing to do with the release.
    start, end = ADR_WINDOW
    last = dt.date.fromisoformat(end)
    month_end = (last.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    check(
        start.endswith("-01") and last == month_end,
        f"the question's window {start}..{end} is whole months, so a monthly grain "
        f"can answer it exactly",
    )

    from_records = adr_from_records(tables)
    from_release = adr_from_release(rows)
    check(
        from_records == from_release,
        f"the release answers the adenoma-detection question exactly as the records do "
        f"{from_release} (records {from_records})",
    )
    if published:
        check(
            from_release == PUBLISHED_ADR[profile.key],
            f"it answers it with the figures reported on SUP-909 {PUBLISHED_ADR[profile.key]} "
            f"— got {from_release}",
        )

    at = {name: index for index, name in enumerate(aggregate.release_columns("network_profile"))}
    header = rows["network_profile"][0]
    check(
        header[at["patients"]] == len(tables["patients"])
        and header[at["procedures"]] == len(tables["procedures"]),
        "the release's own header row counts the corpus it was built from",
    )
    check(
        profile.coverage_note in header,
        "the release carries this network's coverage caveat, so a researcher holding "
        "no records still reads it",
    )

    # Rule 1 of aggregate.py, exercised rather than read. It raises SystemExit on
    # a leak, so a pass is silence.
    try:
        aggregate.assert_no_record(corpus, rows)
        leaked = None
    except SystemExit as error:
        leaked = str(error)
    check(leaked is None, f"the release contains no value that identifies a record{'' if leaked is None else f': {leaked}'}")

    # And the inverse, so that the check above is not vacuous: a release that
    # DID carry a patient identifier must be caught.
    sentinel = tables["patients"][0]["mrn"]
    poisoned = {name: [list(row) for row in table] for name, table in rows.items()}
    poisoned["sites"][0][3] = sentinel
    try:
        aggregate.assert_no_record(corpus, poisoned)
        caught = False
    except SystemExit:
        caught = True
    check(caught, "a release with a medical record number in it fails that check")

    # The release card is the researcher's ONLY knowledge of this database, and
    # the researcher holds no identifying card to fall back on — so the same
    # card-versus-schema comparison the identifying card gets, plus the one check
    # only an aggregate needs: that no column it declares is an identifier.
    card_path = os.path.join(os.path.dirname(HERE), profile.release_dataset, "data.yaml")
    try:
        import yaml
    except ImportError:
        print("  skip  PyYAML not installed — release card checks skipped")
        return
    card = yaml.safe_load(open(card_path, encoding="utf-8"))
    prefix = profile.release_prefix
    card_tables = {
        table["name"]: [column["name"] for column in table["columns"]]
        for table in card["spec"]["schema"]["tables"]
    }
    schema_tables = {prefix + name: aggregate.release_columns(name) for name in aggregate.TABLES}
    check(
        set(card_tables) == set(schema_tables),
        f"the release card declares exactly the tables the release creates "
        f"(card only: {sorted(set(card_tables) - set(schema_tables))}, "
        f"release only: {sorted(set(schema_tables) - set(card_tables))})",
    )
    mismatched = [
        name for name in sorted(set(card_tables) & set(schema_tables))
        if card_tables[name] != schema_tables[name]
    ]
    check(not mismatched, f"every release table's columns match its card in name and order {mismatched}")
    incomplete = [
        f"{table['name']}.{column['name']}"
        for table in card["spec"]["schema"]["tables"]
        for column in table["columns"]
        if column.get("role") not in ("measure", "dimension") or not column.get("description")
    ]
    check(not incomplete, f"every release column declares a role and a description {incomplete[:5]}")
    identifying = sorted(
        name
        for columns in card_tables.values()
        for name in columns
        if name in IDENTIFYING_COLUMNS
    )
    check(
        not identifying,
        f"the release card declares no column that identifies a patient, a specimen, "
        f"a single procedure or a clinician {identifying}",
    )
    flat = " ".join(open(card_path, encoding="utf-8").read().split())
    check(
        " ".join(profile.coverage_note.split()) in flat,
        "the release card publishes this network's coverage caveat in its own text",
    )
    check(
        card["spec"]["access"]["mode"] == "gateway",
        "the release is reached through the gate like every other card here, so a "
        "withdrawn grant stops it",
    )


def check(condition: bool, message: str) -> None:
    if condition:
        if not QUIET:
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


def run(out: str, network: str, patients: int, seed: int, quiet: bool = False) -> None:
    """Generate one corpus. `quiet` swallows the generator's own row-count table,
    which is worth reading once and not once per seed in a sweep."""
    sink = io.StringIO() if quiet else contextlib.nullcontext()
    with contextlib.redirect_stdout(sink) if quiet else contextlib.nullcontext():
        code = generate.main(
            ["--network", network, "--out", out, "--patients", str(patients), "--seed", str(seed)]
        )
    assert code == 0


def check_placement(directory: str, tables: dict[str, list[dict]], profile) -> None:
    """The assertions that depend on WHICH records the generator happened to pick.

    Separated out because these are the ones a single seed cannot vouch for. Every
    defect this generator has shipped was of this shape — a contradiction planted
    on a superseded recommendation, a coordinator note about somebody's mother
    attached to a man, the showcase document on a patient who had died — and each
    was invisible under the one seed that was run and present under half the
    others. Measured: without the `_eligible` guards, 7 of 16 seeds put a planted
    contradiction on a non-active patient. `--seeds` runs this over a range.
    """
    manifest = open(os.path.join(directory, "MANIFEST.md"), encoding="utf-8").read()

    for contradiction in notes.CONTRADICTIONS:
        check(
            f"`{contradiction['key']}`" in manifest or contradiction["consequence"] in manifest,
            f"the planted contradiction {contradiction['key']!r} reached the manifest",
        )

    # A contradiction on a superseded recommendation is moot: "the record holds
    # seven years" is not a finding when the next colonoscopy happened two years
    # ago and closed the recall. Two of the five landed there in the first draft.
    superseded = {
        r["procedure_id"]: r["superseded_by"]
        for r in tables["surveillance_recommendations"]
        if r["superseded_by"]
    }
    contradiction_procs = re.findall(r"procedure `(PROC-\d+)`", manifest)
    check(
        len(contradiction_procs) >= len(notes.CONTRADICTIONS),
        f"the manifest names a procedure for each contradiction ({len(contradiction_procs)})",
    )
    moot = [p for p in contradiction_procs if p in superseded]
    check(not moot, f"no planted contradiction sits on a superseded recommendation ({moot})")
    # Closed is not only "completed". A recall shut because the patient refused,
    # was deferred on clinical grounds, transferred out or died has no next action
    # left in it either, and a document proving the record wrong about a recall
    # with no next action proves nothing. The first version of this check looked
    # at `completed` alone and let the showcase incomplete-exam document land on a
    # patient who had died.
    status_for_rec = {r["recommendation_id"]: r["recall_status"] for r in tables["recalls"]}
    rec_for_proc = {
        r["procedure_id"]: r["recommendation_id"] for r in tables["surveillance_recommendations"]
    }
    closed = [
        f"{p}:{status_for_rec.get(rec_for_proc.get(p, ''), 'none')}"
        for p in contradiction_procs
        if status_for_rec.get(rec_for_proc.get(p, "")) not in constants.LIVE_RECALL_STATUSES
    ]
    check(not closed, f"every planted contradiction sits on a live recall ({closed})")

    # Every document the manifest hands the operator has to be about somebody the
    # practice can still act on, in all three cohorts. A chart-abstraction queue
    # containing dead patients is realistic; a demonstration that ends in "there
    # is nothing to do about this one" is not.
    status_for_mrn = {p["mrn"]: p["patient_status"] for p in tables["patients"]}
    named_mrns = sorted(set(re.findall(rf"{re.escape(profile.mrn_prefix)}\d{{7}}", manifest)))
    check(
        len(named_mrns) >= 5,
        f"the manifest names the patients its documents are about ({len(named_mrns)})",
    )
    inactive = [
        f"{mrn}:{status_for_mrn.get(mrn, 'unknown')}"
        for mrn in named_mrns
        if status_for_mrn.get(mrn) != "active"
    ]
    check(not inactive, f"the patient behind every planted document is active ({inactive})")



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", default="cypress-bay", choices=sorted(profiles.PROFILES))
    parser.add_argument(
        "--all-networks",
        action="store_true",
        help="check every network in profiles.py, which is what CI and a review want: "
        "the capture knobs are guarded on being non-zero, so the complete networks "
        "and the incomplete one take different paths through the generator",
    )
    parser.add_argument(
        "--patients",
        type=int,
        default=1500,
        help="corpus size (default 1500 — small and fast). Some properties only appear at the "
        "size the stand runs on, so CI also runs --published-size.",
    )
    parser.add_argument(
        "--published-size",
        action="store_true",
        help="use the network's own patient count instead of --patients, which is what the "
        "published card declares and what the stand loads",
    )
    parser.add_argument("--seed", type=int, default=None, help="default: the network's own seed")
    parser.add_argument("--keep", action="store_true", help="do not delete the generated corpora")
    parser.add_argument(
        "--seeds",
        type=int,
        default=1,
        help="also run the placement assertions over this many consecutive seeds "
        "(default 1 — just the one below). A generator is not vouched for by one seed.",
    )
    args = parser.parse_args()

    networks = sorted(profiles.PROFILES) if args.all_networks else [args.network]
    if args.published_size:
        args.patients = None  # resolved per network in check_network
    for index, network in enumerate(networks):
        if len(networks) > 1:
            print(f"\n{'=' * 72}\n== {profiles.by_key(network).network_name}  ({network})\n{'=' * 72}")
        check_network(network, args)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("everything checked out")
    return 0


def check_network(network: str, args) -> None:
    profile = profiles.by_key(network)
    seed = args.seed if args.seed is not None else profile.seed
    n_patients = args.patients if args.patients is not None else profile.patients

    workdir = tempfile.mkdtemp(prefix=f"{profile.network_short.lower()}-selftest-")
    first = os.path.join(workdir, "a")
    second = os.path.join(workdir, "b")

    print("generating twice with the same seed")
    run(first, network, n_patients, seed)
    run(second, network, n_patients, seed)

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
    notes_dir = os.path.join(first, "notes")
    registered = {r["document_filename"] for r in tables["clinical_documents"] if r["document_filename"]}
    on_disk = set(os.listdir(notes_dir))
    check(registered == on_disk, f"clinical_documents names exactly the files on disk ({len(on_disk)})")
    check(
        all(os.path.getsize(os.path.join(notes_dir, name)) > 500 for name in on_disk),
        "no generated document is a stub",
    )
    formats = {os.path.splitext(name)[1] for name in on_disk}
    check(formats == {".pdf", ".docx", ".txt"}, f"all three formats are produced ({sorted(formats)})")

    check_placement(first, tables, profile)

    # Every identifying document must actually identify somebody who exists: a
    # note naming an MRN that is not in `patients` would make the reconciliation
    # scenario unanswerable.
    mrns = {r["mrn"] for r in tables["patients"]}
    named = 0
    for name in sorted(on_disk):
        if not name.endswith(".txt"):
            continue
        text = open(os.path.join(notes_dir, name), encoding="utf-8").read()
        found = re.findall(rf"{re.escape(profile.mrn_prefix)}\d{{7}}", text)
        check(bool(found) and all(m in mrns for m in found), f"{name} names a real MRN")
        named += 1
    check(named > 0, "the text notes carry medical record numbers")

    print("\nanatomy: nothing is found where the scope never went")
    segment_reach = {name: index for index, name in enumerate(constants.REACH_ORDER)}
    findings_by_proc: dict[str, list[dict]] = {}
    for row in tables["findings"]:
        findings_by_proc.setdefault(row["procedure_id"], []).append(row)

    sigmoidoscopies = [
        p for p in tables["procedures"] if p["procedure_type"] == "Flexible sigmoidoscopy"
    ]
    structured_sig = [s for s in sigmoidoscopies if s["report_source"] == "Structured capture"]
    check(len(structured_sig) > 0, f"the corpus contains sigmoidoscopies ({len(structured_sig)})")
    check(
        all(s["cecal_intubation"] == "\\N" for s in structured_sig),
        "no sigmoidoscopy claims a cecal intubation result — it was never going there",
    )
    check(
        not any("Incomplete" in s["completion_status"] for s in structured_sig),
        "no sigmoidoscopy is recorded as an incomplete colonoscopy",
    )
    sig_ids = {s["procedure_id"] for s in structured_sig}
    rec_proc = {r["procedure_id"] for r in tables["surveillance_recommendations"]}
    check(
        not (sig_ids & rec_proc),
        f"no sigmoidoscopy earns a colonic surveillance interval "
        f"({len(sig_ids & rec_proc)} do)",
    )
    beyond_left = [
        f
        for s in structured_sig
        for f in findings_by_proc.get(s["procedure_id"], [])
        if segment_reach[f["segment"]] >= constants.SIGMOIDOSCOPY_REACH
    ]
    check(not beyond_left, f"no sigmoidoscopy finds a polyp past the descending colon ({len(beyond_left)})")

    incomplete = [
        p
        for p in tables["procedures"]
        if p["report_source"] == "Structured capture" and p["cecal_intubation"] == "0"
    ]
    check(len(incomplete) > 0, f"some colonoscopies did fail to reach the cecum ({len(incomplete)})")
    in_the_cecum = [
        f
        for p in incomplete
        for f in findings_by_proc.get(p["procedure_id"], [])
        if f["segment"] == "Cecum"
    ]
    check(
        not in_the_cecum,
        f"no incomplete colonoscopy reports a polyp in the cecum it never reached "
        f"({len(in_the_cecum)})",
    )

    print("\nindications match the patient")
    dob = {p["patient_id"]: p["date_of_birth"] for p in tables["patients"]}

    def age_at(patient_id: str, on: str) -> int:
        born = dt.date.fromisoformat(dob[patient_id])
        day = dt.date.fromisoformat(on)
        return (day - born).days // 365

    too_young = [
        p
        for p in tables["procedures"]
        if p["indication"].startswith("Average-risk")
        and age_at(p["patient_id"], p["procedure_date"]) < constants.SCREENING_START_AGE
    ]
    check(
        not too_young,
        f"nobody is given average-risk, age-appropriate screening under "
        f"{constants.SCREENING_START_AGE} ({len(too_young)})",
    )

    print("\noutreach is possible as recorded")
    possible = {
        channel: {outcome for outcome, _ in outcomes}
        for channel, outcomes in vocabulary.OUTCOMES_BY_CHANNEL.items()
    }
    impossible = [
        o
        for o in tables["recall_outreach"]
        if o["outcome"] not in possible[o["channel"]]
        and not (
            o["outcome"] in vocabulary.CLOSING_OUTCOMES
            and o["channel"] in vocabulary.TWO_WAY_CHANNELS
        )
    ]
    check(not impossible, f"no outreach outcome is impossible on its channel ({len(impossible)})")
    by_recall: dict[str, list[dict]] = {}
    for row in tables["recall_outreach"]:
        by_recall.setdefault(row["recall_id"], []).append(row)
    out_of_order = [
        recall
        for recall, rows in by_recall.items()
        if [r["attempt_date"] for r in sorted(rows, key=lambda r: int(r["attempt_no"]))]
        != sorted(r["attempt_date"] for r in rows)
    ]
    check(not out_of_order, f"attempts ascend in date order on every recall ({len(out_of_order)})")

    print("\nwhat the record kept, and what it did not")
    structured = [
        p
        for p in tables["procedures"]
        if p["report_source"] == "Structured capture" and p["procedure_type"].endswith("colonoscopy")
    ]
    check(len(structured) > 0, f"the corpus contains captured colonoscopies ({len(structured)})")
    scale = {"Excellent", "Good", "Fair", "Poor"}

    def share(predicate) -> float:
        return sum(1 for p in structured if predicate(p)) / len(structured)

    no_bbps = share(lambda p: p["bbps_total"] == "\\N")
    no_withdrawal = share(lambda p: p["withdrawal_time_min"] == "\\N")
    free_text = share(lambda p: p["bowel_prep_quality"] not in scale)

    # Rates within four points of what the profile declares. A tolerance rather
    # than an equality because these are draws, and a tolerance rather than
    # ">0" because a knob that quietly halved would still pass ">0" and would
    # take the cross-network comparison with it.
    for label, observed, declared in [
        ("no bowel-preparation score", no_bbps, profile.missing_bbps_rate),
        ("no withdrawal time", no_withdrawal, profile.missing_withdrawal_rate),
        ("preparation as free text", free_text, profile.prep_free_text),
    ]:
        check(
            abs(observed - declared) < 0.04,
            f"{label}: {observed:.1%} of captured colonoscopies, profile declares {declared:.0%}",
        )

    # The whole of the "missing from the form, not from the care" rule. A null
    # preparation score must not drag the clinical columns down with it: the
    # scope still either reached the cecum or did not, and the examination still
    # has a completion status somebody can read.
    leaked = [
        p["procedure_id"]
        for p in structured
        if p["bbps_total"] == "\\N" and (p["cecal_intubation"] == "\\N" or not p["completion_status"])
    ]
    check(
        not leaked,
        f"a missing preparation score never takes the clinical columns with it ({len(leaked)})",
    )
    # Adequacy is decided by the score and not by the adjective, so wherever the
    # adjective IS from the scale the two have to agree. Free text is exempt by
    # construction: "adequate to the cecum" is a clinician's sentence, not a grade.
    disagreeing = [
        p["procedure_id"]
        for p in structured
        if p["bbps_total"] != "\\N"
        and p["bowel_prep_quality"] in scale
        and (int(p["bbps_total"]) >= 6) != (p["bowel_prep_quality"] in ("Excellent", "Good"))
        and not (p["bowel_prep_quality"] == "Fair" and int(p["bbps_total"]) >= 6)
    ]
    check(not disagreeing, f"a recorded score and a recorded adjective agree ({len(disagreeing)})")

    # An interval that never reached the record publishes no recommendation and
    # therefore no recall — the patient is invisible to the practice's own overdue
    # query, which is the failure the surveillance story is about. Checked as a
    # pair of totals rather than per procedure, because the generator does not
    # emit a row saying "this one was skipped": that absence IS the fact.
    eligible = [
        p
        for p in tables["procedures"]
        if p["report_source"] == "Structured capture" and p["procedure_type"].endswith("colonoscopy")
    ]
    recommended = len(tables["surveillance_recommendations"])
    missing_share = 1 - recommended / len(eligible)
    check(
        abs(missing_share - profile.missing_interval_rate) < 0.04,
        f"colonoscopies with no surveillance recommendation: {missing_share:.1%}, "
        f"profile declares {profile.missing_interval_rate:.0%}",
    )
    check(
        len(tables["recalls"]) == recommended,
        f"every published recommendation has exactly one recall "
        f"({len(tables['recalls'])} recalls, {recommended} recommendations)",
    )

    print("\nthe feed lags by as much as it says it does")
    data_through = constants.AS_OF - dt.timedelta(days=profile.feed_lag_days)
    inside_window = [
        p["procedure_id"]
        for p in tables["procedures"]
        if p["report_source"] == "Structured capture" and p["procedure_date"] >= data_through.isoformat()
    ]
    check(
        not inside_window,
        f"no captured procedure is dated on or after {data_through.isoformat()} "
        f"({profile.feed_lag_days} days behind the as-of date) — {len(inside_window)} are",
    )
    latest = max(p["procedure_date"] for p in tables["procedures"])
    check(
        latest >= (data_through - dt.timedelta(days=30)).isoformat(),
        f"the capture reaches the edge of its window rather than stopping short ({latest})",
    )

    print("\nthe dataset card is the agent's only grounding")
    # The generator is shared and the cards are not, so the card this corpus is
    # checked against is the one belonging to the network that was generated.
    card_path = os.path.join(os.path.dirname(HERE), profile.dataset, "data.yaml")
    try:
        import yaml
    except ImportError:
        print("  skip  PyYAML not installed — card checks skipped")
    else:
        card = yaml.safe_load(open(card_path, encoding="utf-8"))
        prefix = profile.table_prefix
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

        # A value the generator writes and the card does not mention is the exact
        # failure this whole section exists for: the agent's only knowledge of
        # this column is the description, so an undocumented free-text
        # preparation note is a value it will either ignore or map onto a grade
        # it is not. Compared against the card as one whitespace-collapsed
        # string, because the card is wrapped at eighty columns and a value can
        # straddle a line break.
        if profile.prep_free_text:
            flat = " ".join(open(card_path, encoding="utf-8").read().split())
            undocumented = sorted(
                value
                for value in {
                    row["bowel_prep_quality"]
                    for row in tables["procedures"]
                    if row["bowel_prep_quality"]
                    not in ("", "Excellent", "Good", "Fair", "Poor")
                }
                if " ".join(value.split()) not in flat
            )
            check(
                not undocumented,
                f"the card names every free-text preparation value the corpus contains "
                f"({undocumented})",
            )

    print("\npdftotext round-trip" if shutil.which("pdftotext") else "\npdftotext not installed — skipped")
    if shutil.which("pdftotext"):
        sample = sorted(n for n in on_disk if n.endswith(".pdf"))[0]
        text = subprocess.run(
            ["pdftotext", "-layout", os.path.join(notes_dir, sample), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        check("MRN:" in text, f"{sample} extracts as text with its identifiers intact")
        check("?" not in text.replace("? ", ""), f"{sample} extracts without mojibake")

    print("\nthe aggregate release")
    check_release(
        first,
        tables,
        profile,
        published=args.patients is None and args.seed is None,
    )
    second_release = aggregate.aggregate(second, profile)
    check(
        aggregate.aggregate(first, profile) == second_release,
        "the release of two byte-identical corpora is identical",
    )

    if args.seeds > 1:
        print(f"\nthe same placement assertions over {args.seeds} seeds")
        global QUIET
        for sweep_seed in range(seed, seed + args.seeds):
            corpus = os.path.join(workdir, f"seed-{sweep_seed}")
            before = len(failures)
            try:
                run(corpus, network, n_patients, sweep_seed, quiet=True)
            except Exception as error:  # a generator that cannot place a document
                check(False, f"seed {sweep_seed}: the generator refused — {error}")
                continue
            seed_schema = open(
                os.path.join(corpus, "schema.sql"), encoding="utf-8"
            ).read()
            seed_tables = {
                name: load(corpus, seed_schema, name) for name in generate.TABLES
            }
            QUIET = True
            try:
                check_placement(corpus, seed_tables, profile)
            finally:
                QUIET = False
            if len(failures) == before:
                print(f"  ok    seed {sweep_seed}")
            shutil.rmtree(corpus)

    if not args.keep:
        shutil.rmtree(workdir)
    else:
        print(f"\ncorpora kept in {workdir}")


if __name__ == "__main__":
    raise SystemExit(main())
