"""The identifying clinical notes: what they say, and which ones lie.

Four kinds of document come out of here, and each exists for a reason the
structured tables cannot serve.

**Outside records packets** (PDF). A colonoscopy performed at another facility.
The `procedures` row has a date, a type and an indication; the prep quality, the
cecal intubation, every polyp, every histology and the recommended interval exist
only in this prose. This is the chart-abstraction cohort: the fields the model is
asked to extract are genuinely not columns anywhere, for these procedures.

**Quality-review reports** (PDF). The signed endoscopy and pathology report for a
procedure that *was* captured structurally. The columns were keyed in from this
document by a person. Twelve are shipped as a review sample and five of them
disagree with the record — a lost grade of dysplasia, a piecemeal resection
recorded as a snare, a serrated lesion filed as hyperplastic, an incomplete
examination recorded as complete, a carcinoma filed as an adenoma. Each
disagreement changes the surveillance interval, and each is in the document only.

**Referral letters** (DOCX). A letter from a referring practice carrying prior
colonoscopy detail in prose, the way a letter does.

**Recall coordinator notes** (TXT). What the coordinator learned on the phone
about a patient whose surveillance is overdue — a caregiver's number, a move, a
reason for refusing. The `recalls` table records that outreach failed; only the
note records why, and why matters to what you do next.

Every note names the patient, their date of birth and their medical record
number. That is deliberate and it is the point of the demonstration: the model
reads an identifying record inside the perimeter. All of it is synthetic.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
import random

import docwriters as dw
import guidelines as gl
import vocabulary as vocab
from constants import (
    ABANDON_POINTS,
    AS_OF,
    LIVE_RECALL_STATUSES,
    REACH_ORDER,
    SCREENING_START_AGE,
    reachable_segments,
)
from docwriters import blank, body, h1, h2, mono, rule

N_OUTSIDE_PACKETS = 20
N_QUALITY_REVIEW = 12
N_REFERRAL_LETTERS = 6
N_RECALL_NOTES = 6  # one per entry in RECALL_NARRATIVES

#: The five planted disagreements, in the order they are placed on the
#: quality-review sample.
#:
#: `describe` is a function, not a sentence, and that is the whole point. These
#: used to be static strings, and the strings drifted from the rows they claimed
#: to describe the moment the seed moved a lesion: the manifest announced "the
#: record calls it a 12 mm hot snare" over a record that said 4 mm cold snare.
#: The manifest is the operator's script — somebody reads it aloud next to the
#: live data — so every word of it is now derived from the record it is about.
#:
#: Each `describe` takes the lesion or procedure as the RECORD has it, before the
#: document's correction, and returns what the document says the record does not.


def _grade(dysplasia: str) -> str:
    """A dysplasia grade in running prose. "none dysplasia" is not English."""
    return "no dysplasia" if dysplasia == "None" else f"{dysplasia.lower()} dysplasia"


CONTRADICTIONS = [
    {
        "key": "high-grade-dysplasia",
        "describe": lambda before, procedure: (
            f"Pathology reports high-grade dysplasia in the "
            f"{before.segment.lower()} specimen; the record carries "
            f"{_grade(before.dysplasia)} on a {before.size_mm} mm "
            f"{before.histology.lower()}"
        ),
        "consequence": "a 3-year interval, on the grade alone",
    },
    {
        "key": "piecemeal-emr",
        "describe": lambda before, procedure: (
            f"A 26 mm lesion removed piecemeal by endoscopic mucosal resection; the record "
            f"calls it {_article(before.size_mm)} {before.size_mm} mm "
            f"{before.resection.lower()}"
        ),
        "consequence": "6 months to confirm clearance of the resection site",
    },
    {
        "key": "serrated-not-hyperplastic",
        "describe": lambda before, procedure: (
            f"Sessile serrated lesion with dysplasia in the {before.segment.lower()}; the record "
            f"says {before.histology.lower()}, {_grade(before.dysplasia)}"
        ),
        "consequence": "a 3-year interval, not a screening interval",
    },
    {
        "key": "incomplete-exam",
        "describe": lambda before, procedure: (
            f"The cecum was not reached and the preparation was inadequate; the record says "
            f"{procedure.completion_status.lower()}, preparation {procedure.prep_quality.lower()} "
            f"(Boston {procedure.bbps_total} of 9)"
        ),
        "consequence": "a repeat within 3 months, not a surveillance decision",
    },
    {
        "key": "carcinoma",
        "describe": lambda before, procedure: (
            f"Invasive adenocarcinoma on deeper levels through the {before.segment.lower()} "
            f"polypectomy specimen; the record says {before.histology.lower()}, "
            f"{_grade(before.dysplasia)}"
        ),
        "consequence": "multidisciplinary review, not routine surveillance",
    },
]


# ---------------------------------------------------------------------------
# Shared pieces of prose
# ---------------------------------------------------------------------------


def _patient_block(patient, extra: list[tuple[str, str]] | None = None) -> list:
    rows = [
        ("Patient", patient.listed_name),
        ("Date of birth", patient.dob.strftime("%m/%d/%Y")),
        ("Sex", "Female" if patient.sex == "F" else "Male"),
        ("MRN", patient.mrn),
        ("Address", f"{patient.address}, {patient.city}, {patient.state} {patient.postal_code}"),
        ("Telephone", patient.phone),
    ]
    rows.extend(extra or [])
    return [mono(f"{label + ':':<18}{value}") for label, value in rows]


def _header(title: str, subtitle: str) -> list:
    return [
        h1(vocab.NETWORK_NAME),
        mono("1400 Cypress Bay Parkway, Cypress Bay, FL 34219   (941) 555-0100"),
        rule(),
        h2(title),
        mono(subtitle),
        blank(),
    ]


def _segment_phrase(segment: str) -> str:
    return {
        "Cecum": "the cecum",
        "Ascending colon": "the ascending colon",
        "Transverse colon": "the transverse colon",
        "Descending colon": "the descending colon",
        "Sigmoid colon": "the sigmoid colon",
        "Rectum": "the rectum",
    }[segment]


def _article(word_or_number) -> str:
    text = str(word_or_number)
    return "an" if text[0] in "aeiouAEIOU8" or text.startswith("11") or text.startswith("18") else "a"


def _prose_lesion(index: int, lesion) -> list:
    morphology = lesion.morphology.split(" — ")[-1]
    if lesion.resection.startswith("Not resected"):
        # "Removed by not resected" is what a sentence built from a column looks
        # like when the column says the opposite of what the sentence assumes.
        disposition = (
            "Not resected: " + lesion.resection.split(" — ")[-1].rstrip(".") + "."
        )
    else:
        disposition = f"Removed by {lesion.resection.lower()}. " + (
            "Retrieved in fragments and submitted in a single container."
            if lesion.piecemeal
            else "Retrieved and submitted in a separate container."
        )
    return [
        mono(f"  Polyp {index}"),
        body(
            f"    {_article(lesion.size_mm).capitalize()} {lesion.size_mm} mm {morphology} lesion in "
            f"{_segment_phrase(lesion.segment)}. {disposition}"
        ),
    ]


def _prose_pathology(index: int, lesion) -> list:
    dysplasia = (
        "No dysplasia identified."
        if lesion.dysplasia == "None"
        else f"{lesion.dysplasia} dysplasia is present."
    )
    return [
        mono(f"  Specimen {index} — {lesion.segment.lower()}, {lesion.size_mm} mm"),
        body(f"    Diagnosis: {lesion.histology}. {dysplasia} Margin: {lesion.margin.lower()}."),
    ]


# ---------------------------------------------------------------------------
# Outside records packet — the chart-abstraction cohort
# ---------------------------------------------------------------------------


def _outside_packet(procedure, facility: str, received_on: dt.date, rng: random.Random) -> list:
    """A colonoscopy and pathology report from another facility, in prose only.

    Everything a surveillance decision needs is in here and nowhere else: the
    preparation, whether the cecum was reached, each polyp with its size and
    histology, and the interval the outside endoscopist recommended.
    """
    patient = procedure.patient
    outside_endoscopist = (
        f"{rng.choice(vocab.GIVEN_MALE + vocab.GIVEN_FEMALE)} "
        f"{rng.choice(vocab.SURNAMES)}, MD"
    )
    prep = rng.choice(["Excellent", "Good", "Good", "Fair", "Fair", "Poor"])
    bbps = {"Excellent": 9, "Good": 7, "Fair": 5, "Poor": 3}[prep]
    prep_adequate = bbps >= 6
    complete = prep_adequate and rng.random() < 0.94

    # Decide how far the scope got BEFORE deciding what it found. The other order
    # produces a report that abandons the examination in the transverse colon and
    # then lists two polyps in the cecum, which is the first thing a
    # gastroenterologist notices and the point at which they stop reading.
    if procedure.kind == "Flexible sigmoidoscopy":
        abandoned_at, reach = "", 3
    elif complete:
        abandoned_at, reach = "", len(REACH_ORDER)
    else:
        abandoned_at, reach = rng.choice(ABANDON_POINTS)
    within_reach = set(reachable_segments(reach))
    withdrawal = round(max(6.0, rng.gauss(10.8, 2.4)), 1)

    lesions = []
    count = rng.choice([0, 1, 1, 2, 2, 3, 3, 4, 5, 7])
    for _ in range(count):
        segment = _weighted_choice(
            rng, [(name, w) for name, w in vocab.SEGMENTS if name in within_reach]
        )
        size_mm = rng.choice(
            [rng.randint(2, 5)] * 5 + [rng.randint(6, 9)] * 3 + [rng.randint(10, 19)] * 2
            + [rng.randint(20, 35)]
        )
        resection = _weighted_choice(rng, vocab.RESECTION_METHODS)
        if size_mm >= 20 and resection.startswith("Cold"):
            resection = "Endoscopic mucosal resection, piecemeal"
        piecemeal = resection.endswith("piecemeal")
        histology, dysplasia = _outside_histology(rng, size_mm)
        margin = (
            "Cannot be assessed — piecemeal specimen"
            if piecemeal
            else rng.choice(["Clear", "Clear", "Clear", "Involved"])
        )
        lesions.append(
            type(
                "L",
                (),
                {
                    "segment": segment,
                    "size_mm": size_mm,
                    "morphology": _weighted_choice(rng, vocab.MORPHOLOGY),
                    "resection": resection,
                    "histology": histology,
                    "dysplasia": dysplasia,
                    "piecemeal": piecemeal,
                    "margin": margin,
                },
            )()
        )

    specimens = [
        gl.Specimen(l.size_mm, l.histology, l.dysplasia, l.piecemeal)
        for l in lesions
        if not l.resection.startswith("Not resected")
    ]
    recommendation = gl.recommend(specimens, prep_adequate, complete)

    lines = _header(
        "OUTSIDE RECORDS — COLONOSCOPY AND PATHOLOGY",
        f"Received {received_on.strftime('%m/%d/%Y')} from {facility}. Scanned to chart, not abstracted.",
    )
    lines += _patient_block(
        patient,
        [
            ("Procedure ID", procedure.procedure_id),
            ("Procedure date", procedure.date.strftime("%m/%d/%Y")),
            ("Performed at", facility),
            ("Endoscopist", outside_endoscopist),
        ],
    )
    lines += [
        blank(),
        h2("INDICATION"),
        body(procedure.indication + "."),
        blank(),
        h2("PROCEDURE"),
        body(
            f"{procedure.kind} performed on {procedure.date.strftime('%m/%d/%Y')} under "
            f"{rng.choice(['monitored anesthesia care', 'moderate sedation'])}. "
            f"Bowel preparation was {prep.lower()} (Boston Bowel Preparation Scale {bbps} of 9). "
            + (
                f"The cecum was intubated and identified by the appendiceal orifice and "
                f"ileocecal valve. Withdrawal time {withdrawal} minutes."
                if complete
                else "The examination was abandoned in the "
                + rng.choice(["descending colon", "transverse colon", "splenic flexure"])
                + " because of "
                + (
                    "residual solid stool obscuring the mucosa."
                    if not prep_adequate
                    else "a fixed, tortuous sigmoid that could not be passed."
                )
                + " The cecum was not reached."
            )
        ),
        blank(),
        h2("FINDINGS"),
    ]
    if lesions:
        for index, lesion in enumerate(lesions, start=1):
            lines += _prose_lesion(index, lesion)
    else:
        lines.append(
            body(
                "  No polyps, masses or mucosal abnormality were seen in the segments examined. "
                "Diverticulosis of the sigmoid colon was noted and is of no surveillance "
                "significance."
            )
        )
    lines += [
        blank(),
        h2("HISTOPATHOLOGY"),
        mono(f"  Reporting laboratory: {rng.choice(vocab.OUTSIDE_LABS)}"),
        mono(
            f"  Accession: {procedure.date.year}-OS-{rng.randint(10000, 99999)}    "
            f"Reported {(procedure.date + dt.timedelta(days=rng.randint(3, 11))).strftime('%m/%d/%Y')}"
        ),
        blank(),
    ]
    if specimens:
        for index, lesion in enumerate(
            [l for l in lesions if not l.resection.startswith("Not resected")], start=1
        ):
            lines += _prose_pathology(index, lesion)
    else:
        lines.append(mono("  No specimens were submitted."))
    lines += [
        blank(),
        h2("RECOMMENDATION OF THE PERFORMING ENDOSCOPIST"),
        body(
            f"{recommendation.basis}. Repeat examination in "
            f"{_interval_phrase(recommendation.interval_months)}, that is on or about "
            f"{_due(procedure.date, recommendation.interval_months).strftime('%m/%d/%Y')}."
        ),
        blank(),
        body(
            f"Signed {outside_endoscopist}, {facility}, "
            f"{(procedure.date + dt.timedelta(days=1)).strftime('%m/%d/%Y')}."
        ),
        blank(),
        rule(),
        mono("CBDH records office stamp: received, scanned, pending abstraction."),
        mono(f"No structured findings have been entered for {procedure.procedure_id}."),
    ]
    return lines, recommendation, len(lesions)


def _outside_histology(rng: random.Random, size_mm: int) -> tuple[str, str]:
    if size_mm <= 5:
        histology = rng.choice(
            ["Tubular adenoma", "Tubular adenoma", "Hyperplastic polyp", "Sessile serrated lesion"]
        )
    elif size_mm <= 9:
        histology = rng.choice(
            ["Tubular adenoma", "Tubular adenoma", "Sessile serrated lesion", "Hyperplastic polyp"]
        )
    elif size_mm <= 19:
        histology = rng.choice(
            ["Tubular adenoma", "Tubulovillous adenoma", "Sessile serrated lesion", "Villous adenoma"]
        )
    else:
        histology = rng.choice(
            ["Tubulovillous adenoma", "Villous adenoma", "Sessile serrated lesion", "Adenocarcinoma"]
        )
    if histology in gl.ADENOMA_HISTOLOGIES:
        dysplasia = "High-grade" if (size_mm >= 10 and rng.random() < 0.2) else "Low-grade"
    elif histology == "Sessile serrated lesion":
        dysplasia = "Low-grade" if rng.random() < 0.08 else "None"
    elif histology == "Adenocarcinoma":
        dysplasia = "High-grade"
    else:
        dysplasia = "None"
    return histology, dysplasia


def _weighted_choice(rng: random.Random, pairs):
    total = sum(p[-1] for p in pairs)
    roll = rng.uniform(0, total)
    upto = 0.0
    for pair in pairs:
        upto += pair[-1]
        if roll <= upto:
            return pair[0]
    return pairs[-1][0]


def _interval_phrase(months: int) -> str:
    if months < 12:
        return f"{months} months"
    years = months // 12
    return "1 year" if years == 1 else f"{years} years"


def _due(date: dt.date, months: int) -> dt.date:
    month_index = date.month - 1 + months
    year = date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date.day, 28)
    return dt.date(year, month, day)


# ---------------------------------------------------------------------------
# Quality-review report — the contradiction cohort
# ---------------------------------------------------------------------------


def _quality_review(procedure, rng: random.Random, contradiction: dict | None) -> list:
    """The signed report the structured columns were keyed in from.

    With `contradiction` set, the prose says one specific thing the record does
    not, and the disagreement is clinically consequential. Without it, the prose
    and the record agree completely — which is what makes the sample a sample
    rather than a list of answers.
    """
    patient = procedure.patient
    # A deep copy, because a planted contradiction edits a lesion and the table
    # rows for this procedure were written from the originals. The disagreement
    # has to live in the document and nowhere else.
    lesions = [dataclasses.replace(l) for l in procedure.lesions]
    recommendation = procedure.recommendation
    prep = procedure.prep_quality
    bbps = procedure.bbps_total
    complete = bool(procedure.cecal_intubation)
    addendum: list = []

    key = contradiction["key"] if contradiction else None
    #: The lesion as the RECORD has it, kept before the document corrects it, so
    #: the manifest can describe the disagreement from both sides.
    before = None
    abandoned_at = ""

    if key == "high-grade-dysplasia":
        target = _first(
            lesions,
            lambda l: l.histology in gl.ADENOMA_HISTOLOGIES
            and l.dysplasia == "Low-grade"
            and l.size_mm >= 6,
        )
        before = dataclasses.replace(target)
        target.dysplasia = "High-grade"
        addendum = [
            h2("PATHOLOGY ADDENDUM"),
            body(
                f"On review of the {target.segment.lower()} specimen with a second pathologist, "
                "focal high-grade dysplasia is present at the surface of the lesion. The original "
                "report of low-grade dysplasia is amended. This changes the surveillance "
                "recommendation to three years."
            ),
        ]
    elif key == "piecemeal-emr":
        # An adenoma, not a hyperplastic polyp: a 26 mm hyperplastic lesion taken
        # piecemeal earns no six-month scar check, so planting it there would
        # produce a contradiction with no consequence.
        target = _first(lesions, lambda l: l.histology in gl.ADENOMA_HISTOLOGIES)
        before = dataclasses.replace(target)
        target.size_mm = 26
        target.resection = "Endoscopic mucosal resection, piecemeal"
        target.piecemeal = True
        target.margin = "Cannot be assessed — piecemeal specimen"
        target.complete_resection = "Endoscopically complete, piecemeal"
        addendum = [
            h2("ENDOSCOPIST'S NOTE"),
            body(
                "The lesion measured 26 mm against the open snare and was removed in four "
                "fragments by piecemeal endoscopic mucosal resection. The base was inspected and "
                "two clips were placed. A six-month examination of the resection site is required "
                "to confirm clearance; this is not a routine surveillance interval."
            ),
        ]
    elif key == "serrated-not-hyperplastic":
        target = _first(lesions, lambda l: l.histology == "Hyperplastic polyp")
        before = dataclasses.replace(target)
        # The segment is left exactly as the record has it. Moving it to the
        # ascending colon made the lesion commoner but made the document disagree
        # with the record about a second fact, and a reader cannot then tell which
        # disagreement is the planted one.
        target.histology = "Sessile serrated lesion"
        target.dysplasia = "Low-grade"
        addendum = [
            h2("PATHOLOGY ADDENDUM"),
            body(
                f"The {target.segment.lower()} polyp shows dilated, boot-shaped crypt bases with "
                "lateral extension along the muscularis mucosae, with a focus of cytologic "
                "dysplasia. This is a sessile serrated lesion with dysplasia, not a hyperplastic "
                "polyp. Surveillance at three years is indicated."
            ),
        ]
    elif key == "incomplete-exam":
        complete = False
        prep, bbps = "Poor", 3
        # Where the scope stopped is derived from what it found, not chosen: the
        # abandonment point has to lie beyond the most proximal polyp in the
        # report, or the document describes polyps in a colon it never reached.
        # That is the first thing a gastroenterologist notices.
        deepest = max(
            (REACH_ORDER.index(l.segment) for l in lesions),
            default=0,
        )
        candidates = [point for point in ABANDON_POINTS if point[1] > deepest]
        abandoned_at, reach = candidates[0] if candidates else ABANDON_POINTS[-1]
        addendum = [
            h2("ENDOSCOPIST'S NOTE"),
            body(
                "Preparation was inadequate throughout, with semi-solid residue that could not be "
                "cleared by irrigation (Boston Bowel Preparation Scale 3 of 9). The scope was "
                f"withdrawn at {abandoned_at}. The cecum was NOT reached and the examination "
                "must be regarded as incomplete. Repeat with a split-dose preparation within "
                "three months."
            ),
        ]
    elif key == "carcinoma":
        target = _first(
            lesions, lambda l: l.histology in ("Tubulovillous adenoma", "Tubular adenoma")
        )
        before = dataclasses.replace(target)
        target.histology = "Adenocarcinoma"
        target.dysplasia = "High-grade"
        original_margin = target.margin.lower()
        target.margin = "Involved — tumor within 0.4 mm of the cautery margin"
        addendum = [
            h2("PATHOLOGY ADDENDUM"),
            body(
                f"Deeper levels through the {target.segment.lower()} polypectomy specimen show "
                "invasive moderately differentiated adenocarcinoma extending into the submucosa, "
                "with lymphovascular invasion identified. The margin reported as "
                f"{original_margin} on the original levels is involved on the deeper ones, with "
                "tumor within 0.4 mm of the cautery edge. This is no longer an adenoma. The case "
                "requires colorectal multidisciplinary review; a routine surveillance interval is "
                "not appropriate."
            ),
        ]

    lines = _header(
        "ENDOSCOPY AND PATHOLOGY REPORT — SIGNED COPY",
        f"Quality review sample. Document is the source of record for {procedure.procedure_id}.",
    )
    site = _site_name(procedure.site_id)
    lines += _patient_block(
        patient,
        [
            ("Procedure ID", procedure.procedure_id),
            ("Procedure date", procedure.date.strftime("%m/%d/%Y")),
            ("Facility", site),
            ("Endoscopist", procedure.provider.name),
            ("NPI", procedure.provider.npi),
        ],
    )
    lines += [
        blank(),
        h2("INDICATION"),
        body(procedure.indication + "."),
        blank(),
        h2("PROCEDURE"),
        body(
            f"{procedure.kind} under {procedure.sedation.lower()}; ASA class {procedure.asa}. "
            f"Bowel preparation {prep.lower()} (Boston Bowel Preparation Scale {bbps} of 9). "
            + (
                f"The cecum was intubated and photo-documented. Withdrawal time "
                f"{procedure.withdrawal_min} minutes."
                if complete
                else f"The examination was abandoned at {abandoned_at or 'the splenic flexure'}; "
                "the cecum was not reached."
            )
            + f" Complication: {procedure.complication.lower() if procedure.complication != 'None' else 'none'}."
        ),
        blank(),
        h2("FINDINGS"),
    ]
    if lesions:
        for index, lesion in enumerate(lesions, start=1):
            lines += _prose_lesion(index, lesion)
    else:
        lines.append(body("  No polyps or mucosal abnormality were identified."))
    lines += [blank(), h2("HISTOPATHOLOGY")]
    resected = [l for l in lesions if not l.resection.startswith("Not resected")]
    if resected:
        lines += [
            mono(f"  Laboratory: {procedure.lab}    Accession: {procedure.accession}"),
            mono(
                f"  Pathologist: {procedure.pathologist.name if procedure.pathologist else 'n/a'}"
            ),
            blank(),
        ]
        for index, lesion in enumerate(resected, start=1):
            lines += _prose_pathology(index, lesion)
    else:
        lines.append(mono("  No specimens submitted."))

    if addendum:
        lines += [blank(), rule()] + addendum

    specimens = [gl.Specimen(l.size_mm, l.histology, l.dysplasia, l.piecemeal) for l in resected]
    corrected = gl.recommend(specimens, bbps >= 6, complete)
    lines += [
        blank(),
        rule(),
        h2("SURVEILLANCE RECOMMENDATION ON THIS DOCUMENT"),
        body(
            f"{corrected.basis}. Repeat examination in {_interval_phrase(corrected.interval_months)} "
            f"— on or about {_due(procedure.date, corrected.interval_months).strftime('%m/%d/%Y')}."
        ),
        blank(),
        body(
            f"Electronically signed {procedure.provider.name}, "
            f"{(procedure.date + dt.timedelta(days=2)).strftime('%m/%d/%Y')} at {site}."
        ),
    ]
    return lines, corrected, recommendation, before


def _first(items, predicate):
    for item in items:
        if predicate(item):
            return item
    return None


def _site_name(site_id: str) -> str:
    for candidate in vocab.SITES:
        if candidate[0] == site_id:
            return candidate[1]
    return vocab.SITES[0][1]


# ---------------------------------------------------------------------------
# Referral letter
# ---------------------------------------------------------------------------


def _referral_letter(patient, procedure, rng: random.Random) -> list:
    """A letter from a referring practice, carrying prior colonoscopy detail in prose.

    It exists because this is how a surveillance obligation actually arrives at a
    practice that did not create it: in a paragraph, in a letter, with the
    interval stated in words and the evidence for it attached. None of what the
    letter says reaches a column until somebody reads it.
    """
    referrer = (
        f"{rng.choice(vocab.GIVEN_FEMALE + vocab.GIVEN_MALE)} {rng.choice(vocab.SURNAMES)}, MD"
    )
    practice = rng.choice(
        [
            "Palm Hollow Family Medicine",
            "Three Rivers Primary Care",
            "Seaward Internal Medicine Group",
            "Caloosa Family Health",
        ]
    )
    # Everything the letter asserts about time is computed from the procedure it
    # is about. Picking the interval and the letter date independently produced
    # letters that cited a colonoscopy two months in their own future, and letters
    # calling a five-year interval "due within the next few months" three weeks
    # after the scope.
    size = rng.choice([8, 11, 13, 14, 18, 22])
    histology = rng.choice(
        ["Tubular adenoma", "Tubulovillous adenoma", "Sessile serrated lesion"]
    )
    piecemeal = size >= 20 and rng.random() < 0.5
    # The interval the letter quotes is the one this lesion actually earns, from
    # the same guideline function the rest of the corpus uses. Sampling it
    # independently gave letters that described a 22 mm adenoma and then quoted a
    # five-year interval — a disagreement nobody planted, in a document whose
    # whole job is to carry a surveillance obligation correctly.
    recommendation = gl.recommend(
        [gl.Specimen(size, histology, "Low-grade", piecemeal)], True, True
    )
    interval_months = recommendation.interval_months
    due = _due(procedure.date, interval_months)
    earliest = procedure.date + dt.timedelta(days=30)
    letter_date = max(earliest, AS_OF - dt.timedelta(days=rng.randint(20, 400)))
    if letter_date >= AS_OF:
        letter_date = AS_OF - dt.timedelta(days=rng.randint(7, 30))
    days_to_due = (due - letter_date).days
    if days_to_due < -365:
        standing = "overdue by over a year"
    elif days_to_due < 0:
        standing = "overdue"
    elif days_to_due <= 180:
        standing = "due within the next few months"
    else:
        standing = f"not due until {due.strftime('%B %Y')}"

    pronoun = "she" if patient.sex == "F" else "he"
    possessive = "her" if patient.sex == "F" else "his"
    relative = "daughter" if patient.sex == "F" else "son"
    resection_detail = (
        "The report notes the lesion was taken in more than one piece and that the base could "
        "not be fully inspected, which is why the interval below is as short as it is."
        if piecemeal
        else rng.choice(
            [
                "The margin was reported clear and the resection complete in one piece.",
                "A clip was placed at the resection site.",
            ]
        )
    )
    arrangement = rng.choice(
        [
            f"{patient.given_name} remembers getting a letter but never had an appointment.",
            "the prior practice has closed and the records came to us incomplete.",
            "there is no correspondence in the transferred record after the procedure.",
        ]
    )
    contact = rng.choice(
        [
            f" {possessive.capitalize()} {relative} can be reached at "
            f"({rng.choice(vocab.FICTION_AREA_CODES)}) 555-0{rng.randint(100, 199)} if "
            f"{pronoun} cannot be reached directly.",
            f" {pronoun.capitalize()} is deaf and prefers written contact or the patient portal.",
            f" Please send correspondence in {patient.language}."
            if patient.language != "English"
            else f" {pronoun.capitalize()} works nights and asks not to be called before noon.",
        ]
    )
    return [
        h1(practice),
        mono("920 Palm Hollow Road, Palmetto Point, FL 34221   (941) 555-0187"),
        rule(),
        mono(f"{letter_date.strftime('%B')} {letter_date.day}, {letter_date.year}"),
        blank(),
        body(f"{vocab.NETWORK_NAME} — Surveillance Service"),
        blank(),
        h2("RE: " + patient.listed_name),
        mono(f"Date of birth: {patient.dob.strftime('%m/%d/%Y')}    Your MRN: {patient.mrn}"),
        mono(f"Our chart: {practice.split()[0].upper()}-{rng.randint(10000, 99999)}"),
        blank(),
        body("Dear Doctor,"),
        blank(),
        body(
            f"I am referring {patient.given_name} {patient.family_name} for colonoscopic "
            f"surveillance. {patient.given_name} moved into the area this year and is now "
            f"established with this practice. {pronoun.capitalize()} is "
            f"{patient.age_on(AS_OF)} and otherwise in good health."
        ),
        blank(),
        body(
            f"The relevant history is a colonoscopy on {procedure.date.strftime('%m/%d/%Y')}, "
            f"performed elsewhere, at which {_article(size)} {size} mm {histology.lower()} was "
            f"removed "
            f"from the "
            f"{rng.choice(['sigmoid colon', 'ascending colon', 'transverse colon', 'rectum'])}. "
            f"{resection_detail} I am enclosing the procedure report and the pathology for your "
            f"records."
        ),
        blank(),
        body(
            f"The recommendation at the time was a repeat examination in "
            f"{_interval_phrase(interval_months)}, which puts it due around "
            f"{due.strftime('%B %Y')} — on my reading of the report that is now {standing}. "
            f"I have not been able to confirm whether it was ever arranged — {arrangement}"
        ),
        blank(),
        body(f"The best number for {patient.given_name} is {patient.phone}.{contact}"),
        blank(),
        body("Thank you for seeing this patient."),
        blank(),
        body("Sincerely,"),
        blank(),
        body(referrer),
        mono(practice),
    ]


# ---------------------------------------------------------------------------
# Recall coordinator note
# ---------------------------------------------------------------------------


#: What a coordinator learned on the telephone, and what the record should do
#: about it. Two of these say something the `recalls` row is wrong about — a death
#: the practice was never told and a clinical reason to postpone — so a recall
#: that is still sending letters should not be. The rest explain why outreach
#: failed without contradicting the status: the structured row records that the
#: call did not land, never why, and why is what decides the next action.
RECALL_NARRATIVES = [
    {
        "key": "deceased",
        "changes_record": True,
        "sex": "M",
        "text": (
            "Reached the patient's wife. The patient died in the hospital in August; this practice "
            "was never notified and the chart is still active. Death certificate requested from the "
            "family. Please close the recall and suppress further letters — the family has had "
            "three since the funeral, and the last one was addressed to him."
        ),
        "implication": "the recall is live and still generating outreach for a patient who has died",
    },
    {
        "key": "medical-deferral",
        "changes_record": True,
        "text": (
            "Patient is under active cardiology care with a drug-eluting stent placed eight weeks "
            "ago and dual antiplatelet therapy that cannot be interrupted for six months. The "
            "cardiology letter is in the chart. This is a medical deferral, not a failure of "
            "outreach: the recall should be re-dated to after the antiplatelet course, not "
            "escalated, and the patient should not be called again before then."
        ),
        "implication": (
            "the record counts this as failed outreach when it is a clinical postponement with a "
            "date attached"
        ),
    },
    {
        "key": "caregiver",
        "changes_record": False,
        # This note names the patient's relationship to the caller, so it only
        # fits a woman, and it puts her in assisted living with early dementia,
        # which only fits an older one — it landed on a 41-year-old once. `sex`
        # and `min_age` are what stop both happening again on the next seed
        # rather than being corrected once each.
        "sex": "F",
        "min_age": 70,
        "text": (
            "Spoke to the patient's son. The patient moved into an assisted living facility in "
            "March and the number on file is the old landline. New number taken below. He asks "
            "that appointments be made through him and that letters be addressed to him, not to "
            "his mother, who has early dementia and throws out mail she does not recognize."
        ),
        "implication": "a reachable contact exists; the number on file is the reason outreach failed",
    },
    {
        "key": "transport",
        "changes_record": False,
        "sex": "F",
        "text": (
            "Patient reached on the fourth attempt. She is not refusing the examination: she "
            "cannot arrange a driver for the sedation and will not come without one. Offered the "
            "early list with the facility shuttle; she accepted in principle and asked to be "
            "called back after the 14th."
        ),
        "implication": "a transport barrier, not a refusal — the fix is a shuttle slot",
    },
    {
        "key": "address",
        "changes_record": False,
        "text": (
            "Number disconnected. Letter returned marked 'moved, no forwarding address'. The portal "
            "message has been unread for nine months. Pharmacy records show a prescription "
            "collected locally last month, so the patient has not left the area. Asked the front "
            "desk to flag the chart for an address check at any contact."
        ),
        "implication": "the patient is local and contactable; only the address on file is stale",
    },
    {
        "key": "prep-intolerance",
        "changes_record": False,
        "sex": "F",
        "text": (
            "Patient declined, and asked not to be contacted about this again. The reason she gave "
            "is that the last preparation made her unwell for two days. She was not told that a "
            "different preparation is available. Flagged for the physician to raise at the next "
            "visit rather than closing the recall as a refusal."
        ),
        "implication": "the refusal is about the preparation, and a different preparation is available",
    },
]


def _recall_note(patient, recall_row: dict, narrative: dict, rng: random.Random) -> list:
    coordinator = rng.choice(
        [
            "Hilda Breckenridge, RN",
            "Marcia Thibodeaux, RN",
            "Vernon Easterling, RN",
            "Josefina Calloway, RN",
        ]
    )
    due = recall_row["due_date"]
    last = recall_row["last_outreach_date"]
    return [
        h1("Surveillance recall — coordinator note"),
        mono(f"{vocab.NETWORK_NAME} — Surveillance Service"),
        rule(),
        mono(f"Patient:        {patient.listed_name}"),
        mono(f"Date of birth:  {patient.dob.strftime('%m/%d/%Y')}"),
        mono(f"MRN:            {patient.mrn}"),
        mono(f"Telephone:      {patient.phone}"),
        mono(f"Address:        {patient.address}, {patient.city}, {patient.state} "
             f"{patient.postal_code}"),
        mono(f"Recall:         {recall_row['recall_id']} "
             f"(recommendation {recall_row['recommendation_id']})"),
        mono(f"Due:            {due.strftime('%m/%d/%Y')} — status "
             f"{recall_row['recall_status']}, {recall_row['days_overdue']} days overdue"),
        mono(f"Outreach:       {recall_row['outreach_attempts']} attempts, last "
             f"{last.strftime('%m/%d/%Y') if last else 'none recorded'}"),
        mono(f"Coordinator:    {coordinator}"),
        rule(),
        blank(),
        body(narrative["text"]),
        blank(),
        body(
            "Entered "
            + (AS_OF - dt.timedelta(days=rng.randint(1, 30))).strftime("%m/%d/%Y")
            + f" by {coordinator}. This note is the only record of the conversation: the recall "
            "row carries the attempt and its outcome code and nothing of what was said."
        ),
    ]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _slug(patient, suffix: str) -> str:
    return f"{patient.family_name.lower()}-{patient.given_name.lower()}-{suffix}"


def build(world, notes_dir: str, rng: random.Random):
    """Write every note file. Returns (document records, scripted-case records)."""
    documents: list[dict] = []
    scripted: list[dict] = []

    # -- outside records packets -------------------------------------------
    # Active patients only, in every cohort. A records packet awaiting
    # abstraction on a patient who has died is realistic and is exactly what a
    # real queue contains; it is also a demonstration that ends in "there is
    # nothing to do about this one".
    outside = sorted(
        (p for p in world.outside_procedures if p.patient.status == "active"),
        key=lambda p: p.procedure_id,
    )
    rng.shuffle(outside)
    for procedure in outside[:N_OUTSIDE_PACKETS]:
        facility = rng.choice(vocab.SOURCE_FACILITIES)
        received = min(AS_OF - dt.timedelta(days=5), procedure.date + dt.timedelta(days=rng.randint(21, 400)))
        lines, recommendation, lesion_count = _outside_packet(procedure, facility, received, rng)
        filename = _slug(procedure.patient, f"outside-colonoscopy-{procedure.date.isoformat()}") + ".pdf"
        dw.write_pdf(
            os.path.join(notes_dir, filename),
            f"Outside colonoscopy records — {procedure.patient.listed_name}",
            lines,
        )
        documents.append(
            {
                "procedure_id": procedure.procedure_id,
                "patient_id": procedure.patient.patient_id,
                "document_type": "Outside records packet",
                "source_facility": facility,
                "received_on": received,
                "filename": filename,
                "format": "pdf",
                "pages": _page_count(lines),
                "abstraction_status": "pending",
            }
        )
        scripted.append(
            {
                "cohort": "abstraction",
                "filename": filename,
                "patient": procedure.patient.listed_name,
                "mrn": procedure.patient.mrn,
                "procedure_id": procedure.procedure_id,
                "note": (
                    f"{lesion_count} polyp(s) in the prose, none in `findings`; interval the document "
                    f"gives is {_interval_phrase(recommendation.interval_months)}"
                ),
            }
        )

    # -- quality-review reports, five of which disagree with the record ----
    reviewable = [
        p
        for p in world.procedures
        if p.report_source == "Structured capture"
        and p.kind in ("Screening colonoscopy", "Surveillance colonoscopy", "Diagnostic colonoscopy")
        and p.recommendation_id
        and len(p.lesions) >= 1
    ]
    reviewable.sort(key=lambda p: p.procedure_id)
    rng.shuffle(reviewable)

    # Place every planted contradiction first, on a procedure whose record it can
    # actually contradict, then pad the sample with ordinary reports. Filling the
    # sample first and hoping the shapes turn up does not work: a high-grade
    # amendment needs a low-grade adenoma to amend.
    picks: list[tuple[object, dict | None]] = []
    taken: set[str] = set()
    for contradiction in CONTRADICTIONS:
        match = _first(
            reviewable,
            lambda p: p.procedure_id not in taken and _eligible(p, contradiction["key"]),
        )
        if match is None:
            raise RuntimeError(
                f"no procedure in this corpus can carry the planted contradiction "
                f"{contradiction['key']!r} — raise --patients or adjust it"
            )
        taken.add(match.procedure_id)
        picks.append((match, contradiction))

    for procedure in reviewable:
        if len(picks) >= N_QUALITY_REVIEW:
            break
        if procedure.procedure_id in taken:
            continue
        taken.add(procedure.procedure_id)
        picks.append((procedure, None))
    picks.sort(key=lambda pair: pair[0].procedure_id)

    for procedure, contradiction in picks:
        lines, corrected, recorded, before = _quality_review(procedure, rng, contradiction)
        suffix = "report-" + procedure.date.isoformat()
        filename = _slug(procedure.patient, suffix) + ".pdf"
        dw.write_pdf(
            os.path.join(notes_dir, filename),
            f"Signed endoscopy report — {procedure.patient.listed_name}",
            lines,
        )
        documents.append(
            {
                "procedure_id": procedure.procedure_id,
                "patient_id": procedure.patient.patient_id,
                "document_type": "Endoscopy and pathology report",
                "source_facility": _site_name(procedure.site_id),
                "received_on": procedure.date + dt.timedelta(days=2),
                "filename": filename,
                "format": "pdf",
                "pages": _page_count(lines),
                "abstraction_status": "quality review",
            }
        )
        if contradiction:
            scripted.append(
                {
                    "cohort": "contradiction",
                    "filename": filename,
                    "patient": procedure.patient.listed_name,
                    "mrn": procedure.patient.mrn,
                    "procedure_id": procedure.procedure_id,
                    "note": (
                        f"{contradiction['describe'](before, procedure)}. Record holds "
                        f"{_interval_phrase(recorded.interval_months)} "
                        f"({recorded.risk_tier}); the document supports "
                        f"{_interval_phrase(corrected.interval_months)} "
                        f"({corrected.risk_tier}) — {contradiction['consequence']}."
                    ),
                }
            )

    # -- referral letters ---------------------------------------------------
    # A referral for post-polypectomy surveillance on a patient who was 29 at the
    # procedure is not wrong, but the letter does not say why — and a letter that
    # does not say why is the thing a product lead stops on. Keep these to the
    # screening-age population, where the referral explains itself.
    letter_candidates = [
        p
        for p in outside[N_OUTSIDE_PACKETS:]
        if p.patient.age_on(p.date) >= SCREENING_START_AGE
    ]
    if len(letter_candidates) < N_REFERRAL_LETTERS:
        letter_candidates += [
            p
            for p in outside[:N_OUTSIDE_PACKETS]
            if p.patient.age_on(p.date) >= SCREENING_START_AGE
            and p not in letter_candidates
        ]
    for procedure in letter_candidates[:N_REFERRAL_LETTERS]:
        lines = _referral_letter(procedure.patient, procedure, rng)
        filename = _slug(procedure.patient, "referral-letter") + ".docx"
        dw.write_docx(
            os.path.join(notes_dir, filename),
            f"Referral letter — {procedure.patient.listed_name}",
            lines,
        )
        documents.append(
            {
                "procedure_id": procedure.procedure_id,
                "patient_id": procedure.patient.patient_id,
                "document_type": "Referral letter",
                "source_facility": "Referring primary care practice",
                "received_on": min(
                    AS_OF - dt.timedelta(days=1),
                    procedure.date + dt.timedelta(days=rng.randint(40, 400)),
                ),
                "filename": filename,
                "format": "docx",
                "pages": 1,
                "abstraction_status": "pending",
            }
        )

    # -- recall coordinator notes ------------------------------------------
    recalls = world.tables["recalls"]
    columns = recalls.columns
    overdue = [
        dict(zip(columns, row))
        for row in recalls.rows
        if row[columns.index("recall_status")] in ("overdue", "lost_to_follow_up")
        and row[columns.index("outreach_attempts")] >= 3
    ]
    overdue.sort(key=lambda r: r["recall_id"])
    rng.shuffle(overdue)
    by_id = {p.patient_id: p for p in world.patients}
    # Each narrative goes to a patient it can be true about: one that names the
    # patient's wife needs a man, one that says "his mother" needs a woman.
    # Pairing them by position is how the shipped seed ended up with a note about
    # somebody's mother attached to a male patient.
    taken_recalls: set[str] = set()
    pairs: list[tuple[dict, dict]] = []

    def fits(narrative: dict, row: dict) -> bool:
        patient = by_id[row["patient_id"]]
        # Active only. The deceased note is the one exception that proves the
        # rule and it still needs an active patient: the finding IS that the
        # chart is open and still sending letters to a family who have buried
        # him, which is only true if the record does not know.
        if patient.status != "active":
            return False
        if "sex" in narrative and patient.sex != narrative["sex"]:
            return False
        if "min_age" in narrative and patient.age_on(AS_OF) < narrative["min_age"]:
            return False
        return True

    for narrative in RECALL_NARRATIVES:
        match = next(
            (
                row
                for row in overdue
                if row["recall_id"] not in taken_recalls and fits(narrative, row)
            ),
            None,
        )
        if match is None:
            raise RuntimeError(
                f"no overdue recall fits the coordinator note {narrative['key']!r} "
                f"(wanted sex {narrative.get('sex', 'any')}, age "
                f"{narrative.get('min_age', 'any')}+) — raise --patients"
            )
        taken_recalls.add(match["recall_id"])
        pairs.append((narrative, match))

    for narrative, recall_row in pairs:
        patient = by_id[recall_row["patient_id"]]
        lines = _recall_note(patient, recall_row, narrative, rng)
        filename = _slug(patient, f"recall-{recall_row['recall_id'].lower()}") + ".txt"
        dw.write_txt(
            os.path.join(notes_dir, filename),
            f"Recall coordinator note — {patient.listed_name}",
            lines,
        )
        documents.append(
            {
                "procedure_id": "",
                "patient_id": patient.patient_id,
                "document_type": "Recall coordinator note",
                "source_facility": "",
                "received_on": AS_OF - dt.timedelta(days=rng.randint(1, 30)),
                "filename": filename,
                "format": "txt",
                "pages": 1,
                "abstraction_status": "complete",
            }
        )
        scripted.append(
            {
                "cohort": "recall",
                "filename": filename,
                "patient": patient.listed_name,
                "mrn": patient.mrn,
                "procedure_id": recall_row["recall_id"],
                "changes_record": narrative["changes_record"],
                "note": (
                    f"{recall_row['days_overdue']} days overdue after "
                    f"{recall_row['outreach_attempts']} attempts — {narrative['implication']}"
                ),
            }
        )

    return documents, scripted


def _eligible(procedure, key: str) -> bool:
    """Whether this procedure's record can carry this disagreement.

    The first three conditions are the same for all five and none of them is about
    the lesion. The record has to have a next action left in it, because that is
    what the document is going to prove wrong:

      * the recommendation must still be the patient's current one — "the record
        holds seven years" is not a finding when the next scope happened two years
        ago;
      * the recall it generated must still be live — not completed, declined,
        deferred, transferred out or closed on a death;
      * the patient must be active.

    The last two exist because the first was not enough. With only the supersession
    check, the showcase incomplete-exam document landed on a patient who had died:
    the manifest told the operator the right answer was "a repeat within three
    months", and the assistant reading the record would have answered, correctly,
    that the patient was dead. A demonstration cannot recover from that.
    """
    if procedure.superseded_by:
        return False
    if procedure.recall_status not in LIVE_RECALL_STATUSES:
        return False
    if procedure.patient.status != "active":
        return False
    if key == "high-grade-dysplasia":
        # Six millimetres and up. High-grade dysplasia in a 2 mm polyp happens,
        # but a reader who sees it reads the corpus as random rather than the
        # finding as real, and the amendment is what has to be believed here.
        return any(
            l.histology in gl.ADENOMA_HISTOLOGIES
            and l.dysplasia == "Low-grade"
            and l.size_mm >= 6
            for l in procedure.lesions
        ) and procedure.recommendation.interval_months > 36
    if key == "piecemeal-emr":
        return (
            any(
                l.histology in gl.ADENOMA_HISTOLOGIES and not l.piecemeal and l.size_mm < 20
                for l in procedure.lesions
            )
            and procedure.recommendation.interval_months >= 36
        )
    if key == "serrated-not-hyperplastic":
        # Proximal colon only. Hyperplastic polyps and sessile serrated lesions
        # are genuinely confusable there and that is where the misclassification
        # costs something; a rectal serrated lesion with dysplasia reads as an
        # oddity instead of a miss, and the segment is not moved to suit the
        # story.
        return (
            len(procedure.lesions) == 1
            and procedure.lesions[0].histology == "Hyperplastic polyp"
            and procedure.lesions[0].segment
            in ("Cecum", "Ascending colon", "Transverse colon")
            and procedure.recommendation.interval_months >= 120
        )
    if key == "carcinoma":
        return (
            any(
                l.histology in ("Tubulovillous adenoma", "Tubular adenoma")
                for l in procedure.lesions
            )
            and procedure.recommendation.interval_months >= 36
        )
    if key == "incomplete-exam":
        return (
            procedure.cecal_intubation == 1
            and procedure.prep_quality in ("Good", "Excellent")
            and procedure.recommendation.interval_months >= 36
        )
    return False


def _page_count(lines) -> int:
    from docwriters import _paginate, flow

    return len(_paginate(flow(lines)))
