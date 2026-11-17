"""Write out/MANIFEST.md — the operator's crib sheet for a run.

The dataset card tells a consumer what the data means. This file tells the person
running the demonstration what is true about the particular corpus they just
generated: how big it is, how many recalls are genuinely overdue, which note
files are the abstraction cohort, and — the part nobody can reconstruct by
reading — which five documents disagree with the record and what the right answer
is. It is generated beside the data rather than committed, because every number
in it is a property of the seed.
"""

from __future__ import annotations

import os

from constants import AS_OF


def _rows(world, table: str) -> list[dict]:
    handle = world.tables[table]
    return [dict(zip(handle.columns, row)) for row in handle.rows]


def _tally(values) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def write(world, scripted: list[dict], args, path: str) -> None:
    recalls = _rows(world, "recalls")
    procedures = _rows(world, "procedures")
    documents = _rows(world, "clinical_documents")
    pathology = _rows(world, "pathology")
    recommendations = _rows(world, "surveillance_recommendations")

    overdue = [r for r in recalls if r["recall_status"] in ("overdue", "lost_to_follow_up")]
    overdue_long = [r for r in overdue if r["days_overdue"] >= 365]
    outside = [p for p in procedures if p["report_source"] == "Outside report"]
    pending = [d for d in documents if d["abstraction_status"] == "pending"]
    blocked = [d for d in documents if d["abstraction_status"].startswith("blocked")]
    colonoscopies = [
        p
        for p in procedures
        if p["procedure_type"].endswith("colonoscopy") and p["report_source"] == "Structured capture"
    ]
    with_adenoma = {p["procedure_id"] for p in pathology if p["adenoma"] == 1}
    adr = 100.0 * len(with_adenoma & {p["procedure_id"] for p in colonoscopies}) / max(1, len(colonoscopies))

    lines: list[str] = []
    out = lines.append

    out("# Cypress Bay Digestive Health — generated corpus")
    out("")
    out(
        f"Seed `{args.seed}`, as-of date `{AS_OF.isoformat()}`, "
        f"{args.patients:,} patients, tables `{args.database}.{args.table_prefix}*`."
    )
    out("")
    out("Entirely synthetic. Regenerating with the same seed reproduces this corpus exactly.")
    out("")

    out("## Volume")
    out("")
    out("| Table | Rows |")
    out("|---|---:|")
    total = 0
    for name, table in world.tables.items():
        total += len(table.rows)
        out(f"| `{args.table_prefix}{name}` | {len(table.rows):,} |")
    out(f"| **total** | **{total:,}** |")
    out("")

    out("## What is true about this corpus")
    out("")
    out(
        f"- **Adenoma detection rate** on structurally captured colonoscopies: "
        f"**{adr:.1f}%** — the figure a practice is judged on, and it is computable here."
    )
    out(
        f"- **{len(overdue):,} recalls are genuinely overdue** "
        f"({100.0 * len(overdue) / max(1, len(recalls)):.1f}% of {len(recalls):,}), "
        f"of which **{len(overdue_long):,} are more than a year past due**. "
        "Past due means the due date has passed with no completed examination after it, "
        "not a status someone typed."
    )
    out(
        f"- **{len(outside):,} procedures were performed at another facility** and have no "
        f"findings, no pathology and no surveillance recommendation in the structured record. "
        f"{len(pending):,} of them have a document in hand; {len(blocked):,} are still being "
        "chased. That gap is the chart-abstraction scenario, and it is visible in SQL."
    )
    out("")
    out("Recall status distribution:")
    out("")
    out("| Status | Recalls |")
    out("|---|---:|")
    for status, count in _tally(r["recall_status"] for r in recalls):
        out(f"| `{status}` | {count:,} |")
    out("")
    out("Surveillance risk tiers:")
    out("")
    out("| Risk tier | Recommendations |")
    out("|---|---:|")
    for tier, count in _tally(r["risk_tier"] for r in recommendations):
        out(f"| {tier} | {count:,} |")
    out("")

    contradictions = [s for s in scripted if s["cohort"] == "contradiction"]
    abstraction = [s for s in scripted if s["cohort"] == "abstraction"]
    recall_notes = [s for s in scripted if s["cohort"] == "recall"]

    out("## The five documents that disagree with the record")
    out("")
    out(
        "Each of these is the signed report a structured row was keyed in from, and in each the "
        "document is right and the row is wrong. The disagreement is in the prose only — no "
        "column anywhere says the record is suspect. Upload the file, ask the assistant to "
        "reconcile it against the patient's record, and this is what it should find."
    )
    out("")
    for item in contradictions:
        out(f"### `{item['filename']}`")
        out("")
        out(f"- Patient **{item['patient']}**, MRN `{item['mrn']}`, procedure `{item['procedure_id']}`")
        out(f"- {item['note']}")
        out("")

    out("## Chart-abstraction cohort")
    out("")
    out(
        "A procedure performed elsewhere. The `procedures` row carries a date, a type and an "
        "indication; the preparation, the cecal intubation, every polyp, every histology and the "
        "recommended interval are in the document and in no column. `clinical_documents` names "
        "the file."
    )
    out("")
    out("| File | Patient | MRN | Procedure | In the prose |")
    out("|---|---|---|---|---|")
    for item in abstraction:
        out(
            f"| `{item['filename']}` | {item['patient']} | `{item['mrn']}` | "
            f"`{item['procedure_id']}` | {item['note']} |"
        )
    out("")

    out("## Recall coordinator notes")
    out("")
    out(
        "The `recalls` table records that outreach failed and which outcome code it failed with. "
        "Why it failed is in these notes only, and why is what decides whether the next action is "
        "another letter, a different preparation, a shuttle slot, or closing the recall."
    )
    out("")
    changes = [r for r in recall_notes if r.get("changes_record")]
    explains = [r for r in recall_notes if not r.get("changes_record")]
    out("These two say something the recall row is wrong about, and the row is still sending letters:")
    out("")
    out("| File | Patient | MRN | Recall | What the note says the record does not |")
    out("|---|---|---|---|---|")
    for item in changes:
        out(
            f"| `{item['filename']}` | {item['patient']} | `{item['mrn']}` | "
            f"`{item['procedure_id']}` | {item['note']} |"
        )
    out("")
    out("These explain a failure the row only records as a code:")
    out("")
    out("| File | Patient | MRN | Recall | What the note adds |")
    out("|---|---|---|---|---|")
    for item in explains:
        out(
            f"| `{item['filename']}` | {item['patient']} | `{item['mrn']}` | "
            f"`{item['procedure_id']}` | {item['note']} |"
        )
    out("")

    out("## Loading")
    out("")
    out("```bash")
    out("CH_URL=http://10.10.1.11:8123 CH_USER=<owner> CH_PASSWORD=<secret> ./load.sh")
    out("```")
    out("")
    out(
        "The schema drops and recreates every table, so a reload replaces the corpus. The notes "
        "in `notes/` are uploaded to the assistant by hand — they are documents a clinician reads, "
        "not a table, which is the whole reason the abstraction scenario proves anything."
    )
    out("")

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    os.chmod(path, 0o644)
