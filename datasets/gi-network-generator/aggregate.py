#!/usr/bin/env python3
"""The aggregate release: what a practice publishes to an outside researcher.

A clinic that agrees to a pooled comparison has agreed to a comparison, not to
handing over its chart. This script turns one generated corpus into a second,
much smaller corpus of COUNTS — network by site by month by procedure type —
and that second corpus is what the clinic's release card publishes.

    python3 aggregate.py --network ridgeline --corpus out/ridgeline --out out/ridgeline-release

Three rules, and they are the whole of this file:

1. **A count, never a record.** Every output column is either a dimension the
   practice already publishes in its own name (the site, the month, the
   procedure type, the recall status) or an integer count of rows. There is no
   patient identifier, no medical record number, no name, no date of birth, no
   encounter, no procedure identifier and no free-text note. `assert_no_record`
   below checks that against the corpus rather than trusting this docstring: it
   fails if any emitted cell is a value that identifies a patient, a specimen or
   a single procedure in the source.

2. **No clinician, either.** There is no `provider_id` anywhere in the release.
   A per-endoscopist detection rate is the single most sensitive figure this
   corpus could produce, it identifies a named clinician in a network with a
   handful of endoscopists, and no question this stand asks needs it. A release has to decide what it is NOT
   for, and this is that decision.

3. **The aggregate carries its own caveats.** `network_profile.coverage_note`
   and the `prep_missing` counts travel with the numbers. The researcher holds
   no identifying card, so a caveat documented only there is a caveat the
   researcher never reads — and a rate published without the size of what it
   excluded is the defect this whole scenario exists to show being caught.

What the release deliberately does NOT do is suppress small cells. A count of
one in one month at one site is a real disclosure risk in a real release and a
real release would suppress or round it. This one does not, because the whole
point of the self-test is that the researcher's pooled figures come out EXACTLY
equal to the figures computed from the records — and suppression would make them
only approximately equal, which is a demonstration of something else. Said out
loud here, in `stands/latica/README.md` and on the card itself.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import generate  # noqa: E402
import profiles as pr  # noqa: E402

#: Tables written, in load order. `load-corpus.js` loads whatever `*.tsv` it
#: finds, so the order is for a human reading the directory.
TABLES = ["network_profile", "sites", "procedure_monthly", "recall_monthly"]

#: Examinations that look at the colon. The other two procedure types in the
#: corpus — an upper endoscopy and a flexible sigmoidoscopy — are examinations in
#: their own right and are counted, but they set no surveillance interval and
#: they carry no bowel-preparation denominator.
COLONOSCOPY_TYPES = ("Screening colonoscopy", "Surveillance colonoscopy", "Diagnostic colonoscopy")

#: The four values `bowel_prep_quality` is documented as taking. Anything else
#: non-empty is the endoscopist's prose, which is the defect Ridgeline carries on
#: purpose, so it is counted rather than cleaned.
PREP_SCALE = ("Excellent", "Good", "Fair", "Poor")

STRUCTURED = "Structured capture"

#: BBPS total at or above which the preparation is adequate. The same threshold
#: the published card documents and the same one the analyst used unprompted.
ADEQUATE_BBPS = 6


def columns_of(schema: str, table: str) -> list[str]:
    """Column names of one corpus table, read out of its own rendered schema.sql.

    The corpus is TabSeparated with no header, so the schema IS the header. Taking
    it from there rather than restating it means this script cannot drift from the
    generator the way a hard-coded column list would."""
    match = re.search(r"CREATE TABLE \w+\.\w*?" + table + r"\s*\((.*?)\n\) ENGINE", schema, re.S)
    if not match:
        raise SystemExit(f"{table}: no DDL in the corpus schema.sql")
    return [line.strip().split()[0] for line in match.group(1).strip().split("\n") if line.strip()]


def load(directory: str, schema: str, table: str) -> list[dict]:
    names = columns_of(schema, table)
    rows = []
    with open(os.path.join(directory, f"{table}.tsv"), encoding="utf-8") as handle:
        for line in handle:
            values = line.rstrip("\n").split("\t")
            rows.append(dict(zip(names, values)))
    return rows


def null(value: str) -> bool:
    r"""TabSeparated writes NULL as `\N`; an empty string is an empty string."""
    return value == "\\N"


def month_of(date_text: str) -> str:
    return f"{date_text[:7]}-01"


class Counter:
    """A row of the output under construction: a dict of named integers that
    starts at zero and can be added to without being initialised."""

    def __init__(self, measures: tuple[str, ...]):
        self.measures = measures
        self.values: dict[str, float] = {name: 0 for name in measures}

    def bump(self, name: str, amount: float = 1) -> None:
        self.values[name] += amount


def aggregate(corpus: str, profile: pr.NetworkProfile) -> dict[str, list[list]]:
    schema = open(os.path.join(corpus, "schema.sql"), encoding="utf-8").read()
    source = {name: load(corpus, schema, name) for name in generate.TABLES}

    # -- which procedures found what ---------------------------------------
    #
    # One pass over pathology, which is the only table big enough for the shape
    # of the loop to matter. `adenoma` and `advanced_histology` are published
    # flags on the specimen; a procedure counts once however many specimens it
    # produced, which is what "detection rate" means.
    adenoma_procedures: set[str] = set()
    advanced_procedures: set[str] = set()
    for row in source["pathology"]:
        if row["adenoma"] == "1":
            adenoma_procedures.add(row["procedure_id"])
        if row["advanced_histology"] == "1":
            advanced_procedures.add(row["procedure_id"])

    # A surveillance interval reached the structured record if and only if there
    # is a recommendation row for the procedure. See `World.build_surveillance`:
    # when the interval was not recorded, no row is written and no recall exists
    # — the practice's overdue list has never heard of the patient.
    with_interval = {row["procedure_id"] for row in source["surveillance_recommendations"]}

    # -- procedures ---------------------------------------------------------
    procedure_measures = (
        "procedures",
        "structured_reports",
        "dictated_reports",
        "prep_adequate",
        "prep_inadequate",
        "prep_missing",
        "prep_free_text",
        "with_adenoma",
        "prep_adequate_with_adenoma",
        "with_advanced_histology",
        "cecal_intubation",
        "withdrawal_recorded",
        "withdrawal_minutes_total",
        "surveillance_eligible",
        "interval_recorded",
    )
    procedure_cells: dict[tuple[str, str, str], Counter] = {}
    site_of_procedure: dict[str, str] = {}
    first_date = ""
    last_date = ""

    for row in source["procedures"]:
        key = (row["site_id"], month_of(row["procedure_date"]), row["procedure_type"])
        cell = procedure_cells.setdefault(key, Counter(procedure_measures))
        site_of_procedure[row["procedure_id"]] = row["site_id"]
        first_date = row["procedure_date"] if not first_date else min(first_date, row["procedure_date"])
        last_date = max(last_date, row["procedure_date"])

        cell.bump("procedures")
        structured = row["report_source"] == STRUCTURED
        cell.bump("structured_reports" if structured else "dictated_reports")

        if structured:
            if null(row["bbps_total"]):
                cell.bump("prep_missing")
            elif int(row["bbps_total"]) >= ADEQUATE_BBPS:
                cell.bump("prep_adequate")
            else:
                cell.bump("prep_inadequate")
            quality = row["bowel_prep_quality"]
            if quality and quality not in PREP_SCALE:
                cell.bump("prep_free_text")
            if row["procedure_type"] not in ("EGD", "Flexible sigmoidoscopy"):
                cell.bump("surveillance_eligible")
                if row["procedure_id"] in with_interval:
                    cell.bump("interval_recorded")

        found_adenoma = row["procedure_id"] in adenoma_procedures
        if found_adenoma:
            cell.bump("with_adenoma")
        if row["procedure_id"] in advanced_procedures:
            cell.bump("with_advanced_histology")
        # The numerator of the rate this release exists to serve. It is published
        # as its own count rather than left to the reader to intersect, because
        # "adenomas among examinations with an adequate preparation" is not
        # recoverable from a with-adenoma count and an adequate-prep count side
        # by side — the two sets overlap in a way only the record knows.
        adequate = structured and not null(row["bbps_total"]) and int(row["bbps_total"]) >= ADEQUATE_BBPS
        if adequate and found_adenoma:
            cell.bump("prep_adequate_with_adenoma")

        if row["cecal_intubation"] == "1":
            cell.bump("cecal_intubation")
        if not null(row["withdrawal_time_min"]):
            cell.bump("withdrawal_recorded")
            cell.bump("withdrawal_minutes_total", float(row["withdrawal_time_min"]))

    # -- recalls ------------------------------------------------------------
    #
    # A recall carries no site of its own: it belongs to a recommendation, which
    # belongs to a procedure, which happened somewhere. Two hops, resolved here
    # so the release can be read by site without the researcher needing either
    # intermediate table.
    recommendation = {
        row["recommendation_id"]: row for row in source["surveillance_recommendations"]
    }
    outreach_by_recall: collections.Counter = collections.Counter()
    for row in source["recall_outreach"]:
        outreach_by_recall[row["recall_id"]] += 1

    recall_measures = ("recalls", "days_overdue_total", "outreach_attempts")
    recall_cells: dict[tuple[str, str, str, str], Counter] = {}
    for row in source["recalls"]:
        parent = recommendation[row["recommendation_id"]]
        site = site_of_procedure[parent["procedure_id"]]
        key = (site, month_of(row["due_date"]), parent["risk_tier"], row["recall_status"])
        cell = recall_cells.setdefault(key, Counter(recall_measures))
        cell.bump("recalls")
        cell.bump("days_overdue_total", int(row["days_overdue"]))
        cell.bump("outreach_attempts", outreach_by_recall[row["recall_id"]])

    # -- the network's own row ---------------------------------------------
    structured_colonoscopies = sum(
        cell.values["structured_reports"]
        for (_, _, kind), cell in procedure_cells.items()
        if kind in COLONOSCOPY_TYPES
    )

    def share(measure: str) -> float:
        if not structured_colonoscopies:
            return 0.0
        total = sum(
            cell.values[measure]
            for (_, _, kind), cell in procedure_cells.items()
            if kind in COLONOSCOPY_TYPES
        )
        return round(100 * total / structured_colonoscopies, 2)

    eligible = sum(cell.values["surveillance_eligible"] for cell in procedure_cells.values())
    recorded = sum(cell.values["interval_recorded"] for cell in procedure_cells.values())

    data_through = (generate.AS_OF - dt.timedelta(days=profile.feed_lag_days)).isoformat()
    network_profile = [[
        profile.key,
        profile.network_name,
        profile.network_short,
        profile.state,
        len(profile.sites),
        len(source["patients"]),
        len(source["procedures"]),
        int(sum(
            cell.values["procedures"]
            for (_, _, kind), cell in procedure_cells.items()
            if kind in COLONOSCOPY_TYPES
        )),
        first_date,
        last_date,
        data_through,
        profile.feed_lag_days,
        round(100 - share("prep_missing"), 2),
        share("prep_free_text"),
        share("withdrawal_recorded"),
        round(100 * recorded / eligible, 2) if eligible else 0.0,
        profile.coverage_note,
    ]]

    sites = [
        [profile.key, profile.network_name, site.site_id, site.name, site.city, site.state,
         site.suites, site.opened]
        for site in profile.sites
    ]

    procedure_rows = [
        [profile.key, profile.network_name, site, month, kind]
        + [
            round(cell.values[name], 2) if name == "withdrawal_minutes_total" else int(cell.values[name])
            for name in procedure_measures
        ]
        for (site, month, kind), cell in sorted(procedure_cells.items())
    ]
    recall_rows = [
        [profile.key, profile.network_name, site, month, tier, status]
        + [int(cell.values[name]) for name in recall_measures]
        for (site, month, tier, status), cell in sorted(recall_cells.items())
    ]

    return {
        "network_profile": network_profile,
        "sites": sites,
        "procedure_monthly": procedure_rows,
        "recall_monthly": recall_rows,
    }


def declared(table: str) -> list[tuple[str, str]]:
    """The (name, type) pairs of one release table, read out of this file's own
    DDL. The DDL is the single declaration of the release's shape: the rows this
    script builds are positional, the self-test reads them by name, and both get
    the order from here rather than from a second list that can drift."""
    found = re.search(
        r"CREATE TABLE \{db\}\.\{p\}" + table + r"\s*\((.*?)\n\) ENGINE", DDL, re.S
    )
    if not found:
        raise SystemExit(f"{table}: no DDL in aggregate.py")
    pairs = []
    for line in found.group(1).strip().split("\n"):
        if line.strip():
            name, kind = line.strip().split()[:2]
            pairs.append((name, kind.rstrip(",")))
    return pairs


def release_columns(table: str) -> list[str]:
    return [name for name, _ in declared(table)]


def date_columns(table: str) -> set[int]:
    """Positions of the `Date` columns in one release table. They are skipped by
    the containment check below: a month and a date of birth are both
    `YYYY-MM-DD`, and a month that happened to equal somebody's birthday would
    fail a check it should pass."""
    return {index for index, (_, kind) in enumerate(declared(table)) if kind == "Date"}


def assert_no_record(corpus: str, rows: dict[str, list[list]]) -> None:
    """Rule 1, checked against the source rather than asserted.

    Collects every value in the corpus that identifies a patient, a specimen, a
    single procedure or a clinician, and fails if any of them appears anywhere in
    the release. This is a containment check, not a schema check: it catches a
    column added later that carries an identifier the author did not think of as
    one, which is exactly how a release leaks."""
    schema = open(os.path.join(corpus, "schema.sql"), encoding="utf-8").read()
    forbidden: set[str] = set()
    for table, columns in [
        ("patients", ("patient_id", "mrn", "family_name", "given_name", "date_of_birth",
                      "phone", "email", "address_line", "postal_code")),
        ("providers", ("provider_id", "provider_name", "npi")),
        ("procedures", ("procedure_id",)),
        ("findings", ("finding_id",)),
        ("pathology", ("pathology_id", "accession_number")),
        ("surveillance_recommendations", ("recommendation_id",)),
        ("recalls", ("recall_id",)),
        ("encounters", ("encounter_id",)),
        ("clinical_documents", ("document_id", "document_filename")),
    ]:
        present = set(columns_of(schema, table))
        for row in load(corpus, schema, table):
            for column in columns:
                if column in present and row[column]:
                    forbidden.add(row[column])

    leaked = []
    for name, table in rows.items():
        dates = date_columns(name)
        for row in table:
            for index, value in enumerate(row):
                if index in dates:
                    continue
                if isinstance(value, str) and value in forbidden:
                    leaked.append(f"{name}.{index}: {value!r}")
    if leaked:
        raise SystemExit(
            "the release contains values that identify a record in the corpus:\n  "
            + "\n  ".join(sorted(set(leaked))[:20])
        )


DDL = """-- {network} — aggregate release.
--
-- Generated by datasets/gi-network-generator/aggregate.py --network {key} from
-- the corpus datasets/gi-network-generator/generate.py --network {key} writes.
-- Entirely synthetic, like the corpus it counts.
--
-- WHAT THIS IS. The practice publishes its records under one card and these
-- counts under another, and an outside researcher is granted the counts. Every
-- column below is a dimension the practice publishes in its own name or an
-- integer count of rows: there is no patient, no encounter, no medical record
-- number, no procedure identifier, no clinician and no free text from a chart.
-- An agent holding this database can compute a rate and cannot read a patient,
-- because there is no patient here to read.
--
-- Small cells are NOT suppressed. See aggregate.py's docstring: the figures here
-- are exactly equal to the figures computed from the records, which is the
-- property the self-test checks, and suppression would make them approximate.
-- A real release would suppress. Say that rather than implying otherwise.
--
-- No comment or string in this file may contain a SEMICOLON other than the ones
-- that end a statement. The loader splits schema.sql on that character, because
-- ClickHouse's HTTP interface runs exactly one statement per request — so one
-- inside a comment starts a statement that is an English sentence, and the server
-- rejects it with a syntax error quoting the prose. Found the hard way, loading
-- this very file. selftest.py checks it now.

CREATE DATABASE IF NOT EXISTS {db};

DROP TABLE IF EXISTS {db}.{p}network_profile;
CREATE TABLE {db}.{p}network_profile
(
    network_key               String,
    network                   String,
    network_short             String,
    state                     String,
    sites                     UInt16,
    patients                  UInt32,
    procedures                UInt32,
    colonoscopies             UInt32,
    first_procedure_date      Date,
    last_procedure_date       Date,
    data_through              Date,
    feed_lag_days             UInt16,
    prep_score_recorded_pct   Float32,
    prep_free_text_pct        Float32,
    withdrawal_recorded_pct   Float32,
    interval_recorded_pct     Float32,
    coverage_note             String
) ENGINE = MergeTree ORDER BY network_key;

DROP TABLE IF EXISTS {db}.{p}sites;
CREATE TABLE {db}.{p}sites
(
    network_key      String,
    network          String,
    site_id          String,
    site_name        String,
    city             String,
    state            String,
    endoscopy_suites UInt8,
    opened_on        Date
) ENGINE = MergeTree ORDER BY site_id;

-- One row per site, month and procedure type. An EMPTY `site_id` is an
-- examination this network did not perform: it is known only from an outside
-- report the practice holds, it joins to no row in `{p}sites`, and it is counted
-- under `dictated_reports` with no preparation score of its own. Every
-- preparation and surveillance count below is therefore zero on those rows, and
-- a rate that forgets to say so is a rate over the wrong denominator.
DROP TABLE IF EXISTS {db}.{p}procedure_monthly;
CREATE TABLE {db}.{p}procedure_monthly
(
    network_key                String,
    network                    String,
    site_id                    String,
    month                      Date,
    procedure_type             String,
    procedures                 UInt32,
    structured_reports         UInt32,
    dictated_reports           UInt32,
    prep_adequate              UInt32,
    prep_inadequate            UInt32,
    prep_missing               UInt32,
    prep_free_text             UInt32,
    with_adenoma               UInt32,
    prep_adequate_with_adenoma UInt32,
    with_advanced_histology    UInt32,
    cecal_intubation           UInt32,
    withdrawal_recorded        UInt32,
    withdrawal_minutes_total   Float64,
    surveillance_eligible      UInt32,
    interval_recorded          UInt32
) ENGINE = MergeTree ORDER BY (month, site_id, procedure_type);

DROP TABLE IF EXISTS {db}.{p}recall_monthly;
CREATE TABLE {db}.{p}recall_monthly
(
    network_key        String,
    network            String,
    site_id            String,
    due_month          Date,
    risk_tier          String,
    recall_status      String,
    recalls            UInt32,
    days_overdue_total UInt64,
    outreach_attempts  UInt32
) ENGINE = MergeTree ORDER BY (due_month, site_id, recall_status);
"""


def write(out: str, rows: dict[str, list[list]], profile: pr.NetworkProfile, database: str,
          prefix: str) -> None:
    os.makedirs(out, exist_ok=True)
    for name in TABLES:
        # The rows above are built positionally and the DDL declares the order.
        # A column added to one and not the other would load silently and shift
        # every value after it, so the two are reconciled here rather than
        # trusted to stay in step.
        width = len(release_columns(name))
        for row in rows[name]:
            if len(row) != width:
                raise SystemExit(
                    f"{name}: a row carries {len(row)} values, the DDL declares {width}"
                )
    for name in TABLES:
        path = os.path.join(out, f"{name}.tsv")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows[name]:
                handle.write("\t".join(generate.tsv_cell(value) for value in row) + "\n")
    with open(os.path.join(out, "schema.sql"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(DDL.format(db=database, p=prefix, network=profile.network_name, key=profile.key))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--network", required=True, choices=sorted(pr.PROFILES))
    parser.add_argument("--corpus", required=True, help="a directory generate.py wrote")
    parser.add_argument("--out", required=True, help="where to write the release (created if absent)")
    parser.add_argument("--database", default=generate.DEFAULT_DATABASE)
    parser.add_argument("--table-prefix", default=None, help="default: the network's own release prefix")
    args = parser.parse_args(argv)

    profile = pr.by_key(args.network)
    prefix = args.table_prefix if args.table_prefix is not None else profile.release_prefix
    rows = aggregate(args.corpus, profile)
    assert_no_record(args.corpus, rows)
    write(args.out, rows, profile, args.database, prefix)

    print(f"{profile.network_name} — aggregate release  tables {args.database}.{prefix}*")
    for name in TABLES:
        print(f"  {name:<24} {len(rows[name]):>8,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
