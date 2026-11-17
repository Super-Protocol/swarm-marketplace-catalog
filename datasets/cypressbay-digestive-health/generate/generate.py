#!/usr/bin/env python3
"""Generate the Cypress Bay Digestive Health synthetic dataset.

Two shapes come out of one run, because a clinic holds both halves:

  * a structured record for ClickHouse — eleven tables of patients, encounters,
    procedures, findings, pathology, surveillance recommendations and recall
    state, written as TSV with a `schema.sql` and a `load.sh` beside them;
  * identifying clinical notes as documents — PDF, DOCX and plain text, each one
    naming a patient, a date of birth and a medical record number, because the
    point of the demonstration is a model reading them safely rather than a
    pipeline removing the names first.

The two halves are deliberately *not* redundant. A procedure performed at another
facility has a row here — date, type, indication — and no findings, no pathology
and no surveillance recommendation, because at a real practice that detail arrives
as a document and somebody reads it. Those procedures are the chart-abstraction
cohort: the fields the model is asked to extract exist only in the prose.

Everything is synthetic. No row, no name and no narrative is drawn from a real
patient, a real practice or a real dataset, and nothing here is derived from one.
Run twice with the same seed and you get the same bytes.

    ./generate.py --out ./out
    ./generate.py --out ./out --patients 4000        # a smaller corpus
    ./generate.py --out ./out --no-documents         # tables only

Usage notes live in ../README.md; what the demo operator needs — which note
contradicts which row — is written to out/MANIFEST.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import shutil
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import guidelines as gl  # noqa: E402
import vocabulary as vocab  # noqa: E402
from constants import (  # noqa: E402
    ABANDON_POINTS,
    AS_OF,
    DEFAULT_DATABASE,
    DEFAULT_PATIENTS,
    DEFAULT_SEED,
    DEFAULT_TABLE_PREFIX,
    HISTORY_START,
    REACH_ORDER,
    SCREENING_START_AGE,
    SIGMOIDOSCOPY_REACH,
    reachable_segments,
)
from guidelines import Specimen  # noqa: E402

# Where the tables go, the as-of date and the colon's anatomy all live in
# constants.py, imported above.

#: Share of colonoscopies that were performed somewhere else and reached this
#: practice as a document rather than as structured capture. These are the
#: chart-abstraction cohort.
#:
#: The value is chosen against the number of notes the bundle actually ships
#: (notes.py), not for its own sake: a retrieval queue of seventy with
#: twenty-six documents in hand reads like a records office that is keeping up.
#: A queue of five hundred with the same twenty-six invites the question of where
#: the other four hundred and seventy are, and the answer — "not in this demo
#: bundle" — is not one you want to give in front of a customer.
OUTSIDE_REPORT_SHARE = 0.0045

TABLES = [
    "sites",
    "providers",
    "patients",
    "encounters",
    "procedures",
    "findings",
    "pathology",
    "surveillance_recommendations",
    "recalls",
    "recall_outreach",
    "clinical_documents",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def weighted(rng: random.Random, pairs):
    """Pick from (value, weight) pairs, or (a, b, weight) triples."""
    total = sum(p[-1] for p in pairs)
    roll = rng.uniform(0, total)
    upto = 0.0
    for pair in pairs:
        upto += pair[-1]
        if roll <= upto:
            return pair[0] if len(pair) == 2 else pair[:-1]
    return pairs[-1][0] if len(pairs[-1]) == 2 else pairs[-1][:-1]


def add_months(date: dt.date, months: int) -> dt.date:
    month_index = date.month - 1 + months
    year = date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                         31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.date(year, month, day)


def tsv_cell(value) -> str:
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value)
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "")


class Table:
    """An ordered column list plus rows, written out as TabSeparated."""

    def __init__(self, name: str, columns: list[str]):
        self.name = name
        self.columns = columns
        self.rows: list[list] = []

    def add(self, **values):
        missing = [c for c in self.columns if c not in values]
        extra = [k for k in values if k not in self.columns]
        if missing or extra:
            raise ValueError(f"{self.name}: missing={missing} extra={extra}")
        self.rows.append([values[c] for c in self.columns])

    def write(self, directory: str) -> str:
        path = os.path.join(directory, f"{self.name}.tsv")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            for row in self.rows:
                handle.write("\t".join(tsv_cell(v) for v in row) + "\n")
        return path


# ---------------------------------------------------------------------------
# The record types the generator passes around and notes.py reads
# ---------------------------------------------------------------------------


@dataclass
class Provider:
    provider_id: str
    name: str
    npi: str
    role: str
    site_id: str
    focus: str
    credential: str


@dataclass
class Patient:
    patient_id: str
    mrn: str
    family_name: str
    given_name: str
    dob: dt.date
    sex: str
    phone: str
    email: str
    address: str
    city: str
    state: str
    postal_code: str
    site_id: str
    provider_id: str
    payer_type: str
    plan: str
    language: str
    registered_on: dt.date
    status: str

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.family_name}"

    @property
    def listed_name(self) -> str:
        return f"{self.family_name}, {self.given_name}"

    def age_on(self, date: dt.date) -> int:
        return (date - self.dob).days // 365


@dataclass
class Lesion:
    finding_id: str
    segment: str
    size_mm: int
    morphology: str
    resection: str
    complete_resection: str
    histology: str
    dysplasia: str
    piecemeal: bool
    margin: str


@dataclass
class Procedure:
    procedure_id: str
    patient: Patient
    site_id: str
    provider: Provider
    date: dt.date
    kind: str
    indication: str
    report_source: str
    prep_quality: str
    bbps_total: int | None
    cecal_intubation: int | None
    withdrawal_min: float | None
    sedation: str
    asa: str
    complication: str
    completion_status: str = ""
    #: How many segments of REACH_ORDER the scope actually examined. A complete
    #: colonoscopy is 6; a sigmoidoscopy is 3; an abandoned colonoscopy is
    #: whatever it got to. Nothing may be found beyond it.
    reach: int = len(REACH_ORDER)
    #: Where the examination was abandoned, in words, or "" if it was not.
    abandoned_at: str = ""
    #: The later examination that superseded this one's recommendation, if any.
    #: Filled in by build_surveillance, read by notes.py — a contradiction
    #: planted on a recommendation a later scope already answered is moot.
    superseded_by: str = ""
    #: What became of the recall this procedure's recommendation generated. Also
    #: filled in by build_surveillance and also read by notes.py, for the same
    #: reason: a closed recall has no next action to be wrong about.
    recall_status: str = ""
    lesions: list[Lesion] = field(default_factory=list)
    recommendation: gl.Recommendation | None = None
    pathologist: Provider | None = None
    lab: str = ""
    accession: str = ""
    recommendation_id: str = ""


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


class World:
    def __init__(self, rng: random.Random, n_patients: int):
        self.rng = rng
        self.n_patients = n_patients
        self.tables = {
            "sites": Table(
                "sites",
                ["site_id", "site_name", "city", "state", "endoscopy_suites", "opened_on"],
            ),
            "providers": Table(
                "providers",
                ["provider_id", "provider_name", "npi", "role", "site_id", "specialty_focus"],
            ),
            "patients": Table(
                "patients",
                [
                    "patient_id", "mrn", "family_name", "given_name", "date_of_birth", "sex",
                    "phone", "email", "address_line", "city", "state", "postal_code",
                    "primary_site_id", "primary_provider_id", "payer_type", "insurance_plan",
                    "preferred_language", "registered_on", "patient_status",
                ],
            ),
            "encounters": Table(
                "encounters",
                [
                    "encounter_id", "patient_id", "site_id", "provider_id", "encounter_date",
                    "encounter_type", "chief_complaint", "disposition",
                ],
            ),
            "procedures": Table(
                "procedures",
                [
                    "procedure_id", "patient_id", "site_id", "provider_id", "procedure_date",
                    "procedure_type", "indication", "report_source", "bowel_prep_quality",
                    "bbps_total", "cecal_intubation", "withdrawal_time_min", "sedation",
                    "asa_class", "completion_status", "complication",
                ],
            ),
            "findings": Table(
                "findings",
                [
                    "finding_id", "procedure_id", "patient_id", "segment", "size_mm",
                    "morphology", "resection_method", "resection_complete", "piecemeal",
                ],
            ),
            "pathology": Table(
                "pathology",
                [
                    "pathology_id", "finding_id", "procedure_id", "patient_id", "accession_number",
                    "received_date", "reported_date", "lab_name", "pathologist_id", "histology",
                    "dysplasia_grade", "adenoma", "advanced_histology", "margin_status",
                ],
            ),
            "surveillance_recommendations": Table(
                "surveillance_recommendations",
                [
                    "recommendation_id", "procedure_id", "patient_id", "recommended_on",
                    "interval_months", "due_date", "risk_tier", "basis",
                    "recommending_provider_id", "recommendation_source", "superseded_by",
                ],
            ),
            "recalls": Table(
                "recalls",
                [
                    "recall_id", "recommendation_id", "patient_id", "due_date", "recall_status",
                    "days_overdue", "outreach_attempts", "last_outreach_date", "scheduled_date",
                    "completed_procedure_id", "assigned_coordinator_id", "deferral_reason",
                ],
            ),
            "recall_outreach": Table(
                "recall_outreach",
                [
                    "outreach_id", "recall_id", "patient_id", "attempt_no", "attempt_date",
                    "channel", "outcome", "coordinator_id",
                ],
            ),
            "clinical_documents": Table(
                "clinical_documents",
                [
                    "document_id", "procedure_id", "patient_id", "document_type",
                    "source_facility", "received_on", "document_status", "document_filename",
                    "document_format", "document_pages", "abstraction_status",
                    "assigned_abstractor_id",
                ],
            ),
        }
        self.providers: list[Provider] = []
        self.pathologists: list[Provider] = []
        self.coordinators: list[Provider] = []
        self.endoscopists: list[Provider] = []
        self.patients: list[Patient] = []
        self.procedures: list[Procedure] = []
        self.outside_procedures: list[Procedure] = []
        self.counters: dict[str, int] = {}

    # -- identifiers --------------------------------------------------------

    def next_id(self, prefix: str, width: int = 7) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}-{self.counters[prefix]:0{width}d}"

    # -- the network --------------------------------------------------------

    def build_sites(self):
        for site_id, name, city, state, suites, opened in vocab.SITES:
            self.tables["sites"].add(
                site_id=site_id,
                site_name=name,
                city=city,
                state=state,
                endoscopy_suites=suites,
                opened_on=dt.date.fromisoformat(opened),
            )

    def build_providers(self):
        rng = self.rng
        npi_seed = 9000000000
        for role, count in vocab.PROVIDER_ROLES:
            for _ in range(count):
                sex = rng.choice(["F", "M"])
                given = rng.choice(vocab.GIVEN_FEMALE if sex == "F" else vocab.GIVEN_MALE)
                family = rng.choice(vocab.SURNAMES)
                credential = {
                    "Gastroenterologist": "MD",
                    "Advanced endoscopist": "MD",
                    "Colorectal surgeon": "MD, FACS",
                    "Nurse practitioner": "APRN",
                    "Physician assistant": "PA-C",
                    "Surveillance coordinator": "RN",
                    "Pathologist": "MD",
                }[role]
                npi_seed += rng.randint(1013, 9973)
                provider = Provider(
                    provider_id=self.next_id("PR", 4),
                    name=f"{given} {family}, {credential}",
                    npi=str(npi_seed),
                    role=role,
                    site_id=rng.choice(vocab.SITES)[0],
                    focus=rng.choice(vocab.SPECIALTY_FOCUS),
                    credential=credential,
                )
                self.providers.append(provider)
                self.tables["providers"].add(
                    provider_id=provider.provider_id,
                    provider_name=provider.name,
                    npi=provider.npi,
                    role=provider.role,
                    site_id=provider.site_id,
                    specialty_focus=provider.focus,
                )
        self.pathologists = [p for p in self.providers if p.role == "Pathologist"]
        self.coordinators = [p for p in self.providers if p.role == "Surveillance coordinator"]
        self.endoscopists = [
            p
            for p in self.providers
            if p.role in ("Gastroenterologist", "Advanced endoscopist", "Colorectal surgeon")
        ]

    # -- patients -----------------------------------------------------------

    def build_patients(self):
        rng = self.rng
        for index in range(self.n_patients):
            sex = "F" if rng.random() < 0.52 else "M"
            given = rng.choice(vocab.GIVEN_FEMALE if sex == "F" else vocab.GIVEN_MALE)
            family = rng.choice(vocab.SURNAMES)
            # A screening and surveillance population: mostly 45 and over, with a
            # tail of younger patients who came in with symptoms or family history.
            if rng.random() < 0.08:
                age = rng.randint(27, 44)
            else:
                age = int(min(89, max(45, rng.gauss(63, 10))))
            dob = AS_OF - dt.timedelta(days=age * 365 + rng.randint(0, 364))
            site_id = weighted(rng, [(s[0], w) for s, w in zip(vocab.SITES, [24, 19, 13, 12, 18, 14])])
            provider = rng.choice(self.endoscopists)
            payer_type, plan = weighted(rng, vocab.PAYERS)
            registered = HISTORY_START - dt.timedelta(days=rng.randint(0, 2600))
            status = weighted(
                rng,
                [("active", 912), ("inactive", 54), ("transferred out", 24), ("deceased", 10)],
            )
            patient = Patient(
                patient_id=self.next_id("P"),
                mrn=f"CB-{1000000 + index * 7 + rng.randint(0, 6)}",
                family_name=family,
                given_name=given,
                dob=dob,
                sex=sex,
                phone=f"({rng.choice(vocab.FICTION_AREA_CODES)}) 555-0{rng.randint(100, 199)}",
                email=f"{given.lower()}.{family.lower()}{rng.randint(11, 99)}@example.org",
                address=f"{rng.randint(12, 8940)} {rng.choice(vocab.STREETS)}",
                city=[s for s in vocab.SITES if s[0] == site_id][0][2],
                state="FL",
                postal_code=f"34{rng.randint(100, 299)}",
                site_id=site_id,
                provider_id=provider.provider_id,
                payer_type=payer_type,
                plan=plan,
                language=weighted(rng, vocab.LANGUAGES),
                registered_on=registered,
                status=status,
            )
            self.patients.append(patient)
            self.tables["patients"].add(
                patient_id=patient.patient_id,
                mrn=patient.mrn,
                family_name=patient.family_name,
                given_name=patient.given_name,
                date_of_birth=patient.dob,
                sex=patient.sex,
                phone=patient.phone,
                email=patient.email,
                address_line=patient.address,
                city=patient.city,
                state=patient.state,
                postal_code=patient.postal_code,
                primary_site_id=patient.site_id,
                primary_provider_id=patient.provider_id,
                payer_type=patient.payer_type,
                insurance_plan=patient.plan,
                preferred_language=patient.language,
                registered_on=patient.registered_on,
                patient_status=patient.status,
            )

    # -- procedures, findings, pathology ------------------------------------

    def _indication(self, kind: str, age: int) -> str:
        """Why the procedure was done, given how old the patient was that day.

        Average-risk screening begins at 45 in the US, so "age-appropriate" is a
        claim about the patient and not a label to sprinkle. A younger patient is
        scoped for a reason — a first-degree relative, a syndrome, a symptom — and
        a product lead who screens for a living reads "average-risk screening,
        age-appropriate" on a 28-year-old as a sign the data was never looked at.
        """
        pool = vocab.INDICATIONS_BY_TYPE[kind]
        if kind == "Screening colonoscopy" and age < SCREENING_START_AGE:
            pool = vocab.EARLY_SCREENING_INDICATIONS
        return self.rng.choice(pool)

    def _lesion_count(self, kind: str) -> int:
        rng = self.rng
        if kind == "EGD":
            return 0
        if kind == "Flexible sigmoidoscopy":
            return weighted(rng, [(0, 62), (1, 26), (2, 9), (3, 3)])
        # An adenoma detection rate in the high thirties, which is where a
        # well-performing practice sits.
        return weighted(rng, [(0, 46), (1, 23), (2, 14), (3, 8), (4, 4), (5, 2), (6, 1), (8, 1), (12, 1)])

    def _histology_for(self, size_mm: int, segment: str) -> tuple[str, str]:
        rng = self.rng
        proximal = segment in ("Cecum", "Ascending colon", "Transverse colon")
        if size_mm <= 5:
            pool = [
                ("Tubular adenoma", 46), ("Hyperplastic polyp", 30),
                ("Sessile serrated lesion", 16), ("Normal colonic mucosa", 6),
                ("Inflammatory polyp", 2),
            ]
        elif size_mm <= 9:
            pool = [
                ("Tubular adenoma", 54), ("Tubulovillous adenoma", 8),
                ("Sessile serrated lesion", 20), ("Hyperplastic polyp", 14),
                ("Inflammatory polyp", 4),
            ]
        elif size_mm <= 19:
            pool = [
                ("Tubular adenoma", 38), ("Tubulovillous adenoma", 32),
                ("Villous adenoma", 6), ("Sessile serrated lesion", 16),
                ("Traditional serrated adenoma", 4), ("Adenocarcinoma", 4),
            ]
        else:
            pool = [
                ("Tubulovillous adenoma", 40), ("Villous adenoma", 16),
                ("Tubular adenoma", 14), ("Sessile serrated lesion", 14),
                ("Traditional serrated adenoma", 6), ("Adenocarcinoma", 10),
            ]
        if proximal:
            pool = [(h, w * (2.1 if h == "Sessile serrated lesion" else 1.0)) for h, w in pool]
        histology = weighted(rng, pool)

        if histology in gl.ADENOMA_HISTOLOGIES:
            if size_mm >= 20:
                dysplasia = weighted(rng, [("Low-grade", 76), ("High-grade", 24)])
            elif size_mm >= 10:
                dysplasia = weighted(rng, [("Low-grade", 89), ("High-grade", 11)])
            else:
                dysplasia = weighted(rng, [("Low-grade", 98), ("High-grade", 2)])
        elif histology == "Sessile serrated lesion":
            dysplasia = weighted(rng, [("None", 93), ("Low-grade", 6), ("High-grade", 1)])
        elif histology == "Adenocarcinoma":
            dysplasia = "High-grade"
        else:
            dysplasia = "None"
        return histology, dysplasia

    def build_procedures(self):
        rng = self.rng
        span_days = (AS_OF - HISTORY_START).days
        # Roughly 1.3 procedures per patient over the window, concentrated on the
        # screening-age population.
        candidates = [p for p in self.patients for _ in range(2 if p.age_on(AS_OF) >= 50 else 1)]
        rng.shuffle(candidates)
        n_procedures = int(self.n_patients * 1.3)

        for _ in range(n_procedures):
            patient = candidates.pop()
            date = HISTORY_START + dt.timedelta(days=rng.randint(0, span_days - 1))
            if date <= patient.registered_on:
                date = patient.registered_on + dt.timedelta(days=rng.randint(30, 400))
            if date >= AS_OF:
                date = AS_OF - dt.timedelta(days=rng.randint(1, 600))
            kind = weighted(rng, vocab.PROCEDURE_TYPES)
            indication = self._indication(kind, patient.age_on(date))
            outside = kind != "EGD" and rng.random() < OUTSIDE_REPORT_SHARE
            provider = rng.choice(self.endoscopists)

            if outside:
                procedure = Procedure(
                    procedure_id=self.next_id("PROC"),
                    patient=patient,
                    site_id="",
                    provider=provider,
                    date=date,
                    kind=kind,
                    indication=indication,
                    report_source="Outside report",
                    prep_quality="",
                    bbps_total=None,
                    cecal_intubation=None,
                    withdrawal_min=None,
                    sedation="",
                    asa="",
                    complication="",
                )
                self.outside_procedures.append(procedure)
                self.procedures.append(procedure)
                self.tables["procedures"].add(
                    procedure_id=procedure.procedure_id,
                    patient_id=patient.patient_id,
                    site_id="",
                    provider_id="",
                    procedure_date=date,
                    procedure_type=kind,
                    indication=indication,
                    report_source="Outside report",
                    bowel_prep_quality="",
                    bbps_total=None,
                    cecal_intubation=None,
                    withdrawal_time_min=None,
                    sedation="",
                    asa_class="",
                    completion_status="Unknown — outside report not abstracted",
                    complication="",
                )
                continue

            prep = weighted(rng, vocab.PREP_QUALITY)
            bbps = {"Excellent": rng.randint(8, 9), "Good": rng.randint(6, 7),
                    "Fair": rng.randint(5, 6), "Poor": rng.randint(2, 4)}[prep]
            prep_adequate = bbps >= 6
            withdrawal = round(max(6.0, rng.gauss(11.4, 2.6)), 1) if kind != "EGD" else None

            # How far the scope got, and whether that counts as a failure.
            #
            # A flexible sigmoidoscopy examines the left colon and stops, by
            # design. It is NOT an incomplete colonoscopy: calling it one gave
            # every one of them a "cecum not reached, repeat in 3 months" recall
            # and put hundreds of phantom patients into the overdue cohort the
            # demonstration is about. A colonoscopy that failed to reach the
            # cecum is a different thing and keeps its repeat.
            abandoned_at = ""
            if kind == "Flexible sigmoidoscopy":
                reach, cecal, completion = (
                    SIGMOIDOSCOPY_REACH,
                    None,
                    "Complete to the extent intended — left colon only",
                )
            elif kind == "EGD":
                reach, cecal, completion = len(REACH_ORDER), None, "Complete — upper tract"
            elif rng.random() < (0.996 if prep_adequate else 0.82):
                reach, cecal, completion = len(REACH_ORDER), 1, "Complete to cecum"
            else:
                abandoned_at, reach = rng.choice(ABANDON_POINTS)
                cecal, completion = 0, "Incomplete — cecum not reached"

            procedure = Procedure(
                procedure_id=self.next_id("PROC"),
                patient=patient,
                site_id=patient.site_id,
                provider=provider,
                date=date,
                kind=kind,
                indication=indication,
                report_source="Structured capture",
                prep_quality=prep,
                bbps_total=bbps,
                cecal_intubation=cecal,
                withdrawal_min=withdrawal,
                sedation=weighted(rng, vocab.SEDATION),
                asa=weighted(rng, vocab.ASA_CLASS),
                complication=weighted(rng, vocab.COMPLICATIONS),
                completion_status=completion,
                reach=reach,
                abandoned_at=abandoned_at,
            )

            within_reach = set(reachable_segments(procedure.reach))
            for _lesion in range(self._lesion_count(kind)):
                # Nothing is found past where the scope stopped.
                segment = weighted(
                    rng, [(name, w) for name, w in vocab.SEGMENTS if name in within_reach]
                )
                size_mm = weighted(
                    rng,
                    [(rng.randint(2, 5), 52), (rng.randint(6, 9), 26),
                     (rng.randint(10, 19), 16), (rng.randint(20, 42), 6)],
                )
                resection = weighted(rng, vocab.RESECTION_METHODS)
                if size_mm >= 20 and resection.startswith("Cold"):
                    resection = "Endoscopic mucosal resection, piecemeal"
                piecemeal = resection.endswith("piecemeal")
                histology, dysplasia = self._histology_for(size_mm, segment)
                if resection.startswith("Not resected"):
                    margin = "Not applicable — lesion not resected"
                    complete_resection = "No"
                elif piecemeal:
                    margin = "Cannot be assessed — piecemeal specimen"
                    complete_resection = "Endoscopically complete, piecemeal"
                else:
                    margin = weighted(rng, [("Clear", 92), ("Involved", 5), ("Cannot be assessed", 3)])
                    complete_resection = "Yes" if margin != "Involved" else "No"
                procedure.lesions.append(
                    Lesion(
                        finding_id=self.next_id("F"),
                        segment=segment,
                        size_mm=size_mm,
                        morphology=weighted(rng, vocab.MORPHOLOGY),
                        resection=resection,
                        complete_resection=complete_resection,
                        histology=histology,
                        dysplasia=dysplasia,
                        piecemeal=piecemeal,
                        margin=margin,
                    )
                )

            resected = [l for l in procedure.lesions if not l.resection.startswith("Not resected")]
            specimens = [
                Specimen(l.size_mm, l.histology, l.dysplasia, l.piecemeal) for l in resected
            ]
            # `complete` here means "the whole colon was examined", which is what
            # the guideline turns on. A sigmoidoscopy never satisfies it, and it
            # is excluded from surveillance entirely in build_surveillance — the
            # recommendation computed here is simply never published for one.
            procedure.recommendation = gl.recommend(
                specimens, prep_adequate, procedure.cecal_intubation == 1
            )
            procedure.pathologist = rng.choice(self.pathologists) if resected else None
            procedure.lab = weighted(rng, [(vocab.LABS[0], 68), (vocab.LABS[1], 24), (vocab.LABS[2], 8)])
            procedure.accession = f"{date.year}-CP-{rng.randint(10000, 99999)}"
            self.procedures.append(procedure)
            self._emit_structured_procedure(procedure)

        self.procedures.sort(key=lambda p: (p.date, p.procedure_id))

    def _emit_structured_procedure(self, procedure: Procedure):
        rng = self.rng
        self.tables["procedures"].add(
            procedure_id=procedure.procedure_id,
            patient_id=procedure.patient.patient_id,
            site_id=procedure.site_id,
            provider_id=procedure.provider.provider_id,
            procedure_date=procedure.date,
            procedure_type=procedure.kind,
            indication=procedure.indication,
            report_source="Structured capture",
            bowel_prep_quality=procedure.prep_quality,
            bbps_total=procedure.bbps_total,
            cecal_intubation=procedure.cecal_intubation,
            withdrawal_time_min=procedure.withdrawal_min,
            sedation=procedure.sedation,
            asa_class=procedure.asa,
            completion_status=procedure.completion_status,
            complication=procedure.complication,
        )
        for lesion in procedure.lesions:
            self.tables["findings"].add(
                finding_id=lesion.finding_id,
                procedure_id=procedure.procedure_id,
                patient_id=procedure.patient.patient_id,
                segment=lesion.segment,
                size_mm=lesion.size_mm,
                morphology=lesion.morphology,
                resection_method=lesion.resection,
                resection_complete=lesion.complete_resection,
                piecemeal=1 if lesion.piecemeal else 0,
            )
            if lesion.resection.startswith("Not resected"):
                continue
            received = min(
                AS_OF - dt.timedelta(days=1), procedure.date + dt.timedelta(days=rng.randint(0, 1))
            )
            # A specimen resected the day before the as-of date has not been
            # reported yet, and a report dated tomorrow is the kind of detail that
            # makes an aggregate over "the last 30 days" look broken.
            reported = min(
                AS_OF - dt.timedelta(days=1), received + dt.timedelta(days=rng.randint(1, 6))
            )
            self.tables["pathology"].add(
                pathology_id=self.next_id("PATH"),
                finding_id=lesion.finding_id,
                procedure_id=procedure.procedure_id,
                patient_id=procedure.patient.patient_id,
                accession_number=procedure.accession,
                received_date=received,
                reported_date=max(received, reported),
                lab_name=procedure.lab,
                pathologist_id=procedure.pathologist.provider_id if procedure.pathologist else "",
                histology=lesion.histology,
                dysplasia_grade=lesion.dysplasia,
                adenoma=1 if lesion.histology in gl.ADENOMA_HISTOLOGIES else 0,
                # The same predicate the guideline function uses, not a
                # restatement of it: a published flag that disagrees with the rule
                # it stands for is worse than no flag, because the card tells the
                # agent to trust it.
                advanced_histology=1
                if gl.is_advanced(
                    Specimen(lesion.size_mm, lesion.histology, lesion.dysplasia, lesion.piecemeal)
                )
                else 0,
                margin_status=lesion.margin,
            )

    # -- encounters ---------------------------------------------------------

    def build_encounters(self):
        rng = self.rng
        complaints = [
            "Routine screening discussion", "Rectal bleeding", "Abdominal pain",
            "Change in bowel habit", "Iron-deficiency anemia", "Family history review",
            "Post-procedure results review", "Surveillance interval discussion",
            "Reflux symptoms", "Constipation",
        ]
        dispositions = [
            "Colonoscopy scheduled", "Pathology discussed, surveillance interval agreed",
            "Reassured, no further action", "Referred to colorectal surgery",
            "Referred to genetics", "Medication started", "Repeat in one year",
        ]
        for procedure in self.procedures:
            if procedure.report_source == "Outside report":
                # Nobody was seen here before a procedure performed elsewhere. What
                # this practice has is the visit at which somebody noticed the gap
                # and asked the other facility for the chart.
                self.tables["encounters"].add(
                    encounter_id=self.next_id("ENC"),
                    patient_id=procedure.patient.patient_id,
                    site_id=procedure.patient.site_id,
                    provider_id=procedure.patient.provider_id,
                    encounter_date=min(
                        AS_OF - dt.timedelta(days=1),
                        procedure.date + dt.timedelta(days=rng.randint(30, 500)),
                    ),
                    encounter_type="New patient consultation",
                    chief_complaint="Surveillance interval discussion",
                    disposition="Outside records requested",
                )
                continue
            # A pre-procedure visit, and a results visit for most structured cases.
            pre_date = procedure.date - dt.timedelta(days=rng.randint(7, 70))
            self.tables["encounters"].add(
                encounter_id=self.next_id("ENC"),
                patient_id=procedure.patient.patient_id,
                site_id=procedure.patient.site_id,
                provider_id=procedure.provider.provider_id,
                encounter_date=pre_date,
                encounter_type="Pre-procedure consultation",
                chief_complaint=rng.choice(complaints),
                disposition="Colonoscopy scheduled",
            )
            if rng.random() < 0.72:
                self.tables["encounters"].add(
                    encounter_id=self.next_id("ENC"),
                    patient_id=procedure.patient.patient_id,
                    site_id=procedure.patient.site_id,
                    provider_id=procedure.provider.provider_id,
                    encounter_date=min(
                        AS_OF - dt.timedelta(days=1),
                        procedure.date + dt.timedelta(days=rng.randint(10, 45)),
                    ),
                    encounter_type="Results review",
                    chief_complaint="Post-procedure results review",
                    disposition=rng.choice(dispositions),
                )

    # -- surveillance, recalls, outreach ------------------------------------

    def build_surveillance(self):
        """A recommendation for every completed examination, and a recall for every
        recommendation.

        The recall is the part the colonoscopy scenario turns on, so it is modelled
        properly rather than sampled. A recommendation that a later examination
        answered produces a `completed` recall naming that examination — which is
        what makes `completed` mean something a query can check, instead of a
        status somebody typed. Only a patient's most recent recommendation can be
        live, and only a live recall can be overdue.
        """
        rng = self.rng
        by_patient: dict[str, list[Procedure]] = {}
        for procedure in self.procedures:
            if procedure.report_source != "Structured capture":
                continue
            # An upper endoscopy says nothing about the colon, and neither does a
            # flexible sigmoidoscopy: two thirds of the colon was never looked at,
            # so there is no surveillance interval to set. Both are examinations
            # in their own right, not failures.
            if procedure.kind in ("EGD", "Flexible sigmoidoscopy"):
                continue
            by_patient.setdefault(procedure.patient.patient_id, []).append(procedure)
        for series in by_patient.values():
            series.sort(key=lambda p: (p.date, p.procedure_id))

        for patient_id in sorted(by_patient):
            series = by_patient[patient_id]
            for index, procedure in enumerate(series):
                recommendation = procedure.recommendation
                assert recommendation is not None
                recommendation_id = self.next_id("REC")
                procedure.recommendation_id = recommendation_id
                due = add_months(procedure.date, recommendation.interval_months)
                successor = series[index + 1] if index + 1 < len(series) else None
                self.tables["surveillance_recommendations"].add(
                    recommendation_id=recommendation_id,
                    procedure_id=procedure.procedure_id,
                    patient_id=patient_id,
                    recommended_on=procedure.date,
                    interval_months=recommendation.interval_months,
                    due_date=due,
                    risk_tier=recommendation.risk_tier,
                    basis=recommendation.basis,
                    recommending_provider_id=procedure.provider.provider_id,
                    recommendation_source="Endoscopist at discharge"
                    if recommendation.risk_tier in ("Repeat required", "Malignant")
                    else "Pathology-triggered revision"
                    if rng.random() < 0.22
                    else "Endoscopist at discharge",
                    superseded_by=successor.procedure_id if successor else "",
                )
                procedure.superseded_by = successor.procedure_id if successor else ""
                self._emit_recall(procedure, recommendation_id, due, successor)

    def _emit_recall(
        self,
        procedure: Procedure,
        recommendation_id: str,
        due: dt.date,
        successor: Procedure | None,
    ):
        rng = self.rng
        patient = procedure.patient
        recall_id = self.next_id("RC")
        coordinator = rng.choice(self.coordinators)
        days_past = (AS_OF - due).days

        scheduled_date: dt.date | None = None
        completed_procedure_id = ""
        deferral = ""

        if successor is not None:
            # A later examination answered this recommendation, whether or not it
            # arrived on time. That is what closes a recall in a real practice.
            status = "completed"
            completed_procedure_id = successor.procedure_id
            scheduled_date = successor.date
        elif patient.status == "deceased":
            status = "deceased"
        elif patient.status == "transferred out":
            status = "moved_away"
        elif days_past < -90:
            status = "not_yet_due"
        elif days_past <= 0:
            # Due today is due, not overdue. The boundary matters because
            # `days_overdue` is published as a measure: an "overdue" recall
            # carrying zero days is a row that contradicts its own status, and a
            # cohort defined by that status then has a member nothing is wrong
            # with.
            status = weighted(
                rng, [("scheduled", 46), ("due_soon", 42), ("declined", 6), ("deferred_medical", 6)]
            )
        else:
            # The cohort the colonoscopy scenario is about. A patient past due is
            # usually past due because outreach failed, not because nobody tried.
            status = weighted(
                rng,
                [("overdue", 52), ("scheduled", 17), ("declined", 10),
                 ("deferred_medical", 7), ("lost_to_follow_up", 12), ("moved_away", 2)],
            )

        if status == "scheduled":
            scheduled_date = (
                AS_OF + dt.timedelta(days=rng.randint(3, 120))
                if days_past >= -120
                else due + dt.timedelta(days=rng.randint(-30, 30))
            )
        elif status == "deferred_medical":
            deferral = rng.choice(vocab.DEFERRAL_REASONS)

        attempts = 0
        last_outreach = None
        if status in ("overdue", "lost_to_follow_up", "declined", "due_soon", "scheduled", "completed"):
            attempts = weighted(
                rng,
                [(1, 22), (2, 28), (3, 22), (4, 14), (5, 8), (6, 4), (7, 2)]
                if status in ("overdue", "lost_to_follow_up")
                else [(0, 18), (1, 40), (2, 28), (3, 10), (4, 4)],
            )
        # Attempts march forward in order, and the outcome has to be possible on
        # the channel it came through: a letter is not answered by voicemail, and
        # a portal message does not come back undeliverable by post. These are
        # exactly the rows the coordinator-note scenario cross-checks, so a
        # mismatch there is a contradiction nobody planted.
        first_attempt = due - dt.timedelta(days=rng.randint(0, 60))
        attempt_date = first_attempt
        for attempt_no in range(1, attempts + 1):
            if attempt_no > 1:
                attempt_date = attempt_date + dt.timedelta(days=rng.randint(9, 70))
            attempt_date = min(AS_OF - dt.timedelta(days=1), attempt_date)
            channel = weighted(rng, vocab.OUTREACH_CHANNELS)
            reached = ""
            if status == "declined" and attempt_no == attempts:
                reached = "Reached — declined"
            elif status in ("due_soon", "scheduled", "completed") and attempt_no == attempts:
                reached = "Reached — appointment scheduled"
            if reached and channel not in vocab.TWO_WAY_CHANNELS:
                # Nobody is "reached" by a letter. The contact that closed the
                # recall was the call back, so that is the attempt recorded.
                channel = "Telephone"
            outcome = reached or weighted(rng, vocab.OUTCOMES_BY_CHANNEL[channel])
            last_outreach = attempt_date
            self.tables["recall_outreach"].add(
                outreach_id=self.next_id("OUT"),
                recall_id=recall_id,
                patient_id=patient.patient_id,
                attempt_no=attempt_no,
                attempt_date=attempt_date,
                channel=channel,
                outcome=outcome,
                coordinator_id=coordinator.provider_id,
            )

        procedure.recall_status = status
        self.tables["recalls"].add(
            recall_id=recall_id,
            recommendation_id=recommendation_id,
            patient_id=patient.patient_id,
            due_date=due,
            recall_status=status,
            days_overdue=max(0, days_past) if status in ("overdue", "lost_to_follow_up") else 0,
            outreach_attempts=attempts,
            last_outreach_date=last_outreach,
            scheduled_date=scheduled_date,
            completed_procedure_id=completed_procedure_id,
            assigned_coordinator_id=coordinator.provider_id,
            deferral_reason=deferral,
        )

    # -- the document register ---------------------------------------------

    def build_documents(self, documents: list[dict]):
        """One row per document the practice holds about a procedure.

        Two cohorts live in this table and they are not the same thing.

        An *outside report* is a colonoscopy performed somewhere else: the
        procedures row has a date and an indication and nothing else, because
        every clinical detail arrived as a document. Those rows carry
        `abstraction_status = 'pending'` when the document is in hand and
        `'blocked - awaiting records'` when it is still being chased, and they are
        what chart abstraction is for.

        A *quality review* document is the signed report for a procedure that was
        captured structurally. The structured columns were keyed in from that
        report by a person, the report is the source of truth, and the sample is
        read back against the record periodically. This is not a formality: a
        transcription that lost a grade of dysplasia changes the surveillance
        interval, and the only place the loss is visible is the document.
        """
        rng = self.rng
        abstractors = [
            p for p in self.providers if p.role in ("Nurse practitioner", "Physician assistant")
        ]
        documented = {d["procedure_id"] for d in documents}

        for document in documents:
            self.tables["clinical_documents"].add(
                document_id=self.next_id("DOC", 5),
                procedure_id=document["procedure_id"],
                patient_id=document["patient_id"],
                document_type=document["document_type"],
                source_facility=document.get("source_facility", ""),
                received_on=document["received_on"],
                document_status="received",
                document_filename=document["filename"],
                document_format=document["format"],
                document_pages=document["pages"],
                abstraction_status=document["abstraction_status"],
                assigned_abstractor_id=rng.choice(abstractors).provider_id,
            )

        for procedure in sorted(self.outside_procedures, key=lambda p: p.procedure_id):
            if procedure.procedure_id in documented:
                continue
            self.tables["clinical_documents"].add(
                document_id=self.next_id("DOC", 5),
                procedure_id=procedure.procedure_id,
                patient_id=procedure.patient.patient_id,
                document_type="Outside records packet",
                source_facility=rng.choice(vocab.SOURCE_FACILITIES),
                received_on=None,
                document_status=weighted(rng, [("requested", 62), ("not received", 38)]),
                document_filename="",
                document_format="",
                document_pages=None,
                abstraction_status="blocked - awaiting records",
                assigned_abstractor_id=rng.choice(abstractors).provider_id,
            )


# ---------------------------------------------------------------------------
# DDL and loader
# ---------------------------------------------------------------------------

DDL = """-- Cypress Bay Digestive Health — synthetic GI clinic network.
--
-- Generated by datasets/cypressbay-digestive-health/generate/generate.py.
-- Entirely synthetic: no real patient, practice or record is represented here,
-- and nothing in it is derived from a real dataset.
--
-- The column comments are not decoration. The dataset card publishes the table
-- and column descriptions, and that publication is the only knowledge the
-- consuming agent gets about this database — so the comments here and the
-- descriptions in ../data.yaml say the same thing deliberately.
--
-- Table names carry a prefix and share a database with whatever else is in it.
-- DEFAULT_TABLE_PREFIX in generate.py says why. The short version is that the
-- credential the marketplace stand holds cannot create a database of its own.

CREATE DATABASE IF NOT EXISTS {db};

DROP TABLE IF EXISTS {db}.{p}sites;
CREATE TABLE {db}.{p}sites
(
    site_id          String,
    site_name        String,
    city             String,
    state            String,
    endoscopy_suites UInt8,
    opened_on        Date
) ENGINE = MergeTree ORDER BY site_id;

DROP TABLE IF EXISTS {db}.{p}providers;
CREATE TABLE {db}.{p}providers
(
    provider_id     String,
    provider_name   String,
    npi             String,
    role            String,
    site_id         String,
    specialty_focus String
) ENGINE = MergeTree ORDER BY provider_id;

DROP TABLE IF EXISTS {db}.{p}patients;
CREATE TABLE {db}.{p}patients
(
    patient_id          String,
    mrn                 String,
    family_name         String,
    given_name          String,
    date_of_birth       Date,
    sex                 String,
    phone               String,
    email               String,
    address_line        String,
    city                String,
    state               String,
    postal_code         String,
    primary_site_id     String,
    primary_provider_id String,
    payer_type          String,
    insurance_plan      String,
    preferred_language  String,
    registered_on       Date,
    patient_status      String
) ENGINE = MergeTree ORDER BY patient_id;

DROP TABLE IF EXISTS {db}.{p}encounters;
CREATE TABLE {db}.{p}encounters
(
    encounter_id    String,
    patient_id      String,
    site_id         String,
    provider_id     String,
    encounter_date  Date,
    encounter_type  String,
    chief_complaint String,
    disposition     String
) ENGINE = MergeTree ORDER BY (patient_id, encounter_date);

DROP TABLE IF EXISTS {db}.{p}procedures;
CREATE TABLE {db}.{p}procedures
(
    procedure_id        String,
    patient_id          String,
    site_id             String,
    provider_id         String,
    procedure_date      Date,
    procedure_type      String,
    indication          String,
    report_source       String,
    bowel_prep_quality  String,
    bbps_total          Nullable(UInt8),
    cecal_intubation    Nullable(UInt8),
    withdrawal_time_min Nullable(Float32),
    sedation            String,
    asa_class           String,
    completion_status   String,
    complication        String
) ENGINE = MergeTree ORDER BY (patient_id, procedure_date);

DROP TABLE IF EXISTS {db}.{p}findings;
CREATE TABLE {db}.{p}findings
(
    finding_id        String,
    procedure_id      String,
    patient_id        String,
    segment           String,
    size_mm            UInt16,
    morphology        String,
    resection_method  String,
    resection_complete String,
    piecemeal         UInt8
) ENGINE = MergeTree ORDER BY (procedure_id, finding_id);

DROP TABLE IF EXISTS {db}.{p}pathology;
CREATE TABLE {db}.{p}pathology
(
    pathology_id       String,
    finding_id         String,
    procedure_id       String,
    patient_id         String,
    accession_number   String,
    received_date      Date,
    reported_date      Date,
    lab_name           String,
    pathologist_id     String,
    histology          String,
    dysplasia_grade    String,
    adenoma            UInt8,
    advanced_histology UInt8,
    margin_status      String
) ENGINE = MergeTree ORDER BY (procedure_id, pathology_id);

DROP TABLE IF EXISTS {db}.{p}surveillance_recommendations;
CREATE TABLE {db}.{p}surveillance_recommendations
(
    recommendation_id        String,
    procedure_id             String,
    patient_id               String,
    recommended_on           Date,
    interval_months          UInt16,
    due_date                 Date,
    risk_tier                String,
    basis                    String,
    recommending_provider_id String,
    recommendation_source    String,
    superseded_by            String
) ENGINE = MergeTree ORDER BY (patient_id, recommended_on);

DROP TABLE IF EXISTS {db}.{p}recalls;
CREATE TABLE {db}.{p}recalls
(
    recall_id               String,
    recommendation_id       String,
    patient_id              String,
    due_date                Date,
    recall_status           String,
    days_overdue            UInt16,
    outreach_attempts       UInt8,
    last_outreach_date      Nullable(Date),
    scheduled_date          Nullable(Date),
    completed_procedure_id  String,
    assigned_coordinator_id String,
    deferral_reason         String
) ENGINE = MergeTree ORDER BY (due_date, recall_id);

DROP TABLE IF EXISTS {db}.{p}recall_outreach;
CREATE TABLE {db}.{p}recall_outreach
(
    outreach_id    String,
    recall_id      String,
    patient_id     String,
    attempt_no     UInt8,
    attempt_date   Date,
    channel        String,
    outcome        String,
    coordinator_id String
) ENGINE = MergeTree ORDER BY (recall_id, attempt_no);

DROP TABLE IF EXISTS {db}.{p}clinical_documents;
CREATE TABLE {db}.{p}clinical_documents
(
    document_id            String,
    procedure_id           String,
    patient_id             String,
    document_type          String,
    source_facility        String,
    received_on            Nullable(Date),
    document_status        String,
    document_filename      String,
    document_format        String,
    document_pages         Nullable(UInt8),
    abstraction_status     String,
    assigned_abstractor_id String
) ENGINE = MergeTree ORDER BY document_id;
"""

LOADER = """#!/usr/bin/env bash
#
# Load the generated tables into a ClickHouse server over its HTTP interface.
#
#   CH_URL=http://10.10.1.11:8123 CH_USER=owner CH_PASSWORD=... ./load.sh
#
# The script is idempotent: schema.sql drops and recreates every table, so a
# reload replaces the dataset rather than appending to it. That is the whole
# reason the DDL carries DROP TABLE — a demonstration stand gets reloaded, and a
# dataset that doubles in size each time is worse than one that is missing.
#
# It refuses to start without credentials rather than quietly trying the
# passwordless `default` user, because a ClickHouse that answers 516 to every
# statement looks exactly like a network fault.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${CH_URL:?set CH_URL, e.g. http://10.10.1.11:8123}"
: "${CH_USER:?set CH_USER}"
: "${CH_PASSWORD:?set CH_PASSWORD}"
db="${CH_DATABASE:-@DB@}"
prefix="${CH_TABLE_PREFIX-@PREFIX@}"

# Every statement goes by POST. ClickHouse treats a GET as read-only and answers
# a DDL statement over GET with READONLY, which reads like a privilege problem
# and is not one.
post() {
  curl -sS --fail-with-body -X POST -u "$CH_USER:$CH_PASSWORD" "$@"
}

# ClickHouse's HTTP interface runs one statement per request — a whole file of
# them comes back 400, which looks like a malformed statement and is not one. So
# the schema is split on its semicolons and posted statement by statement. No
# string literal in schema.sql contains a semicolon, which is what makes that
# split safe.
echo "== schema"
stmts="$(mktemp -d)"
trap 'rm -rf "$stmts"' EXIT
awk -v dir="$stmts" 'BEGIN { RS = ";" }
  { gsub(/^[[:space:]]+|[[:space:]]+$/, "");
    if (length($0)) { n++; printf "%s;%s", $0, ORS > sprintf("%s/%03d.sql", dir, n) } }' \\
  "$here/schema.sql"
for statement in "$stmts"/*.sql; do
  # A semicolon inside a comment splits off a chunk with no statement in it, and
  # ClickHouse answers an empty query with an error. Skip those rather than
  # forbidding semicolons in comments, which nobody will remember.
  grep -qvE '^[[:space:]]*(--.*)?$' "$statement" || continue
  post "$CH_URL/" --data-binary @"$statement" > /dev/null
done
echo "   schema applied"

for table in @TABLES@; do
  rows=$(wc -l < "$here/$table.tsv")
  printf '%-34s %8d rows ' "$prefix$table" "$rows"
  post "$CH_URL/?query=INSERT+INTO+$db.$prefix$table+FORMAT+TabSeparated" \
    --data-binary @"$here/$table.tsv" > /dev/null
  printf 'ok\n'
done

echo "== verify"
# Counted from the tables themselves, not from system.parts: a loader account
# scoped to one database has no SELECT on system.parts, and the privilege error
# reads as a failed load when the load in fact succeeded.
for table in @TABLES@; do
  printf '%-34s ' "$prefix$table"
  post "$CH_URL/?database=$db" --data-binary "SELECT count() FROM $prefix$table"
done
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="out", help="output directory (created if absent)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"default {DEFAULT_SEED}")
    parser.add_argument("--patients", type=int, default=DEFAULT_PATIENTS, help=f"default {DEFAULT_PATIENTS}")
    parser.add_argument(
        "--database", default=DEFAULT_DATABASE, help=f"ClickHouse database (default {DEFAULT_DATABASE})"
    )
    parser.add_argument(
        "--table-prefix",
        default=DEFAULT_TABLE_PREFIX,
        help=f"prefix on every table name (default {DEFAULT_TABLE_PREFIX!r})",
    )
    parser.add_argument("--no-documents", action="store_true", help="skip the clinical notes")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    notes_dir = os.path.join(args.out, "notes")

    rng = random.Random(args.seed)
    world = World(rng, args.patients)
    world.build_sites()
    world.build_providers()
    world.build_patients()
    world.build_procedures()
    world.build_encounters()
    world.build_surveillance()

    documents: list[dict] = []
    scripted: list[dict] = []
    if not args.no_documents:
        import notes as notes_module

        # A note that was generated by an earlier run with different parameters is
        # not in `clinical_documents` and nobody will notice it is there. Clear
        # the directory rather than writing over part of it.
        if os.path.isdir(notes_dir):
            shutil.rmtree(notes_dir)
        os.makedirs(notes_dir)
        documents, scripted = notes_module.build(world, notes_dir, rng)

    world.build_documents(documents)

    for name in TABLES:
        world.tables[name].write(args.out)

    with open(os.path.join(args.out, "schema.sql"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(DDL.format(db=args.database, p=args.table_prefix))

    loader_path = os.path.join(args.out, "load.sh")
    with open(loader_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            LOADER.replace("@DB@", args.database)
            .replace("@PREFIX@", args.table_prefix)
            .replace("@TABLES@", " ".join(TABLES))
        )
    os.chmod(loader_path, 0o755)

    import manifest

    manifest.write(world, scripted, args, os.path.join(args.out, "MANIFEST.md"))

    print(
        f"as-of {AS_OF.isoformat()}  seed {args.seed}  "
        f"tables {args.database}.{args.table_prefix}*"
    )
    total = 0
    for name in TABLES:
        count = len(world.tables[name].rows)
        total += count
        print(f"  {name:<32} {count:>8,}")
    print(f"  {'total':<32} {total:>8,}")
    if not args.no_documents:
        files = sorted(os.listdir(notes_dir))
        print(f"  {'clinical notes':<32} {len(files):>8,}  -> {notes_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
