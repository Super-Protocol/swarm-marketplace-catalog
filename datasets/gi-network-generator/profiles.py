"""The three clinic networks this generator produces, and everything that differs
between them.

One program, three corpora. The clinical model — what a guideline says, what a
colon is shaped like, what an outreach attempt can end in — is identical for all
of them, because it is the same specialty in the same country. What differs is
the practice: how many sites, how many patients, how far back the record goes,
how well the forms get filled in, and how good the endoscopists are.

That last pair is the point. A pooled question across three networks is only
interesting if the networks are not three copies of one shape: a researcher
comparing adenoma detection has to find a real difference, and has to be forced
to say which examinations were excluded from the denominator because nobody
recorded a bowel-preparation score. So **coding quality is a first-class axis
here**, modelled as missingness in the record rather than as a worse clinic —
the examination happened, the form was not completed, and the only honest answer
is one that says so.

Two rules the knobs obey, and both matter:

1. **Missingness is about the RECORD, never about the care.** A bowel-prep score
   that was not written down still decided whether the scope reached the cecum
   and still decided the surveillance interval. So the generator computes the
   clinical truth first and then forgets part of it on the way to the table. A
   knob that changed the clinical path instead would produce a network where
   patients really were treated differently, which is a different claim and not
   one this data is allowed to make.
2. **A zero knob draws no random number.** Every missingness draw is guarded by
   `profile.<rate> and rng.random() < profile.<rate>`, which short-circuits. That
   is what keeps the Cypress Bay corpus — already published, already carrying a
   semantic model somebody built by hand — byte for byte what it was before the
   generator learned about profiles.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Site records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """One clinical site, and its share of the network's patients.

    `weight` used to be a parallel list of six numbers in `build_patients`, which
    is a thing you can only get wrong once a network has a different number of
    sites. It lives on the site now.
    """

    site_id: str
    name: str
    city: str
    state: str
    suites: int
    opened: str
    weight: int


@dataclass(frozen=True)
class NetworkProfile:
    """Everything about a corpus that is a fact about the practice, not the specialty."""

    # -- identity ----------------------------------------------------------
    key: str
    """The `--network` argument and the name used in log lines."""

    dataset: str
    """The dataset directory and the published card's `metadata.name`."""

    network_name: str
    network_short: str

    table_prefix: str
    """Prefix on every table name. Part of the published column names."""

    release_dataset: str
    """The directory and published `metadata.name` of this network's AGGREGATE
    RELEASE card — the counts-only extract the practice is willing to hand to an
    outside researcher. A separate card with a separate owner's grant, because it
    is a separate decision from publishing the record."""

    release_prefix: str
    """Prefix on the aggregate release's tables. It extends `table_prefix` so that
    a name read in a query still says which network it belongs to, and the two
    cards are kept apart by their GRANTS rather than by their names: every reader
    this stand creates is granted SELECT table by table, enumerated from the
    corpus directory it loaded (`stands/latica/harness/load-corpus.js`), never by
    wildcard. So the release reader holds exactly the four tables below and the
    identifying reader holds exactly its own eleven."""

    coverage_note: str
    """What an outside reader has to know about this practice's record before
    comparing it with another's, in one sentence. It is published on the
    aggregate card, because the aggregate is the only thing the researcher holds
    — a caveat that lives only in the identifying card's documentation is a
    caveat the researcher never sees."""

    mrn_prefix: str
    """What the medical record number on this practice's documents starts with."""

    accession_prefix: str
    """The middle field of a pathology accession number, `<year>-<this>-<n>`."""

    state: str
    postal_prefix: str
    """First two digits of a patient postal code; the rest is generated."""

    letterhead: str
    """Address line under the network name on a procedure report."""

    surveillance_letterhead: str
    """Address line on a letter from the surveillance service, which runs out of
    one office rather than out of whichever site did the procedure."""

    sites: tuple[Site, ...]

    provider_roles: tuple[tuple[str, int], ...]
    """The establishment, as (role, head count). Sized to the network rather than
    derived from the site count, because the ratio is itself a fact about the
    practice: Ridgeline runs eleven sites on six surveillance coordinators, and
    that is why its recalls go unworked."""

    # -- local colour ------------------------------------------------------
    area_codes: tuple[str, ...]
    """Area codes for generated telephone numbers. The number itself is always in
    the 555-0100..555-0199 fiction range, so nothing here reaches a person."""

    streets: tuple[str, ...]
    payers: tuple[tuple[str, str, int], ...]
    labs: tuple[str, ...]
    """The laboratories this network sends its own specimens to; the first is the
    in-house one and takes most of the work."""

    outside_labs: tuple[str, ...]
    source_facilities: tuple[str, ...]

    referring_practices: tuple[str, ...]
    """Primary-care practices that refer into this network. They appear only in
    the referral letters, which is how a surveillance obligation created
    somewhere else actually arrives — in a paragraph, on somebody else's
    letterhead."""

    referral_letterhead: str
    """Address line on that letter. One address for the lot of them: these are
    scene-setting for a document, not a directory anybody joins to."""

    # -- volume and period -------------------------------------------------
    seed: int
    patients: int
    history_start: dt.date
    """The first procedure in the corpus."""

    procedures_per_patient: float
    feed_lag_days: int = 0
    """How far behind the as-of date this practice's structured capture runs. Zero
    means the record is current to the day. A lagging feed is a real property of a
    network that batches its uploads, and it shows up as a question the pooled
    comparison has to handle: the most recent quarter is not comparable across
    networks that do not all report at the same speed."""

    # -- how well the examinations go --------------------------------------
    prep_quality: tuple[tuple[str, int], ...] = (
        ("Excellent", 30), ("Good", 54), ("Fair", 13), ("Poor", 3),
    )
    withdrawal_mean: float = 11.4
    withdrawal_sd: float = 2.6
    lesion_counts: tuple[tuple[int, int], ...] = (
        (0, 46), (1, 23), (2, 14), (3, 8), (4, 4), (5, 2), (6, 1), (8, 1), (12, 1),
    )
    """Lesions found per completed colonoscopy. This is what moves the adenoma
    detection rate, so it is the headline difference between the three networks —
    and it is the one a researcher is actually asking about."""

    # -- how well the forms get filled in ----------------------------------
    missing_bbps_rate: float = 0.0
    """Share of structured examinations with no Boston Bowel Preparation Scale
    total recorded. The examination still had one — see rule 1 above."""

    missing_withdrawal_rate: float = 0.0
    """Share of colonoscopies with no withdrawal time recorded."""

    prep_free_text: float = 0.0
    """Share of examinations where `bowel_prep_quality` is the endoscopist's prose
    rather than one of the four scale adjectives. Free text in a column a card
    documents as an enumeration is the most ordinary data-quality defect there
    is, and an agent that reports it rather than silently dropping those rows is
    the behaviour worth demonstrating."""

    missing_interval_rate: float = 0.0
    """Share of completed colonoscopies whose surveillance interval never reached
    the structured record — it is in the procedure note and nowhere else. No
    interval means no due date, which means **no recall row**: the patient is not
    overdue in this practice's system because this practice's system has never
    heard of them. That is the failure mode the whole surveillance story is
    about, and it is why this knob is not cosmetic."""

    @property
    def site_weights(self) -> list[tuple[str, int]]:
        return [(site.site_id, site.weight) for site in self.sites]

    def site_name(self, site_id: str) -> str:
        for site in self.sites:
            if site.site_id == site_id:
                return site.name
        return self.sites[0].name

    def city_of(self, site_id: str) -> str:
        for site in self.sites:
            if site.site_id == site_id:
                return site.city
        return self.sites[0].city


#: Free-text bowel-preparation descriptions, for the network that writes prose
#: into the column instead of scoring it. Deliberately not mappable to the scale
#: by string matching alone — "adequate to the cecum" is a clinician saying the
#: examination was good enough, and it is not the word "Good".
PREP_FREE_TEXT = [
    "adequate",
    "adequate to the cecum",
    "good views throughout",
    "suboptimal, washed",
    "poor prep, limited views in the right colon",
    "residual stool, repeat advised",
    "satisfactory after lavage",
]


# ---------------------------------------------------------------------------
# A — Cypress Bay Digestive Health (US, Florida)
# ---------------------------------------------------------------------------
#
# The original, and the one already published and already carrying a semantic
# model built by hand on the stand. Every default above is its value, and the
# four missingness knobs are zero, so its corpus is unchanged by the arrival of
# the other two. Do not retune it.

CYPRESS_BAY = NetworkProfile(
    key="cypress-bay",
    dataset="cypressbay-digestive-health",
    network_name="Cypress Bay Digestive Health",
    network_short="CBDH",
    table_prefix="cbdh_",
    release_dataset="cypressbay-release",
    release_prefix="cbdh_release_",
    coverage_note=(
        "Complete capture. Every structured examination carries a bowel-preparation "
        "score and a withdrawal time, every surveillance interval reached the "
        "structured record, and the feed is current to the as-of date. No "
        "examination has to be excluded from a preparation-quality denominator."
    ),
    mrn_prefix="CB-",
    accession_prefix="CP",
    state="FL",
    postal_prefix="34",
    letterhead="1400 Cypress Bay Parkway, Cypress Bay, FL 34219   (941) 555-0100",
    surveillance_letterhead="920 Palm Hollow Road, Palmetto Point, FL 34221   (941) 555-0187",
    sites=(
        Site("S01", "Cypress Bay Endoscopy Center", "Cypress Bay", "FL", 4, "2009-03-02", 24),
        Site("S02", "Palmetto Point Gastroenterology", "Palmetto Point", "FL", 3, "2012-07-16", 19),
        Site("S03", "Osprey Landing Digestive Care", "Osprey Landing", "FL", 2, "2015-01-12", 13),
        Site("S04", "Indian Shoal Endoscopy Suite", "Indian Shoal", "FL", 2, "2017-09-05", 12),
        Site("S05", "Coral Ridge GI Associates", "Coral Ridge", "FL", 3, "2019-04-01", 18),
        Site("S06", "Mangrove Park Ambulatory Endoscopy", "Mangrove Park", "FL", 2, "2021-10-18", 14),
    ),
    provider_roles=(
        ("Gastroenterologist", 24),
        ("Advanced endoscopist", 5),
        ("Colorectal surgeon", 3),
        ("Nurse practitioner", 6),
        ("Physician assistant", 4),
        ("Surveillance coordinator", 8),
        ("Pathologist", 6),
    ),
    area_codes=("941", "813", "727", "239"),
    streets=(
        "Mangrove Walk", "Sea Grape Lane", "Tarpon Row", "Live Oak Terrace",
        "Pelican Crossing", "Saw Palmetto Drive", "Coquina Court", "Banyan Reach",
        "Spanish Moss Way", "Heron Point Road", "Sabal Palm Circle", "Egret Bend",
        "Loggerhead Trail", "Buttonwood Place", "Cordgrass Avenue", "Snook Harbour Road",
    ),
    payers=(
        ("Medicare Part B", "Traditional Medicare", 34),
        ("Medicare Advantage", "Sunstate Advantage HMO", 16),
        ("Medicare Advantage", "Gulf Coast Senior Choice PPO", 8),
        ("Commercial", "Cypress Health Alliance PPO", 14),
        ("Commercial", "Everglade Mutual Select", 9),
        ("Commercial", "Sawgrass Employer Plan", 7),
        ("Medicaid", "State Medicaid Managed Care", 7),
        ("Self-pay", "Self-pay", 5),
    ),
    referring_practices=(
        "Palm Hollow Family Medicine",
        "Three Rivers Primary Care",
        "Seaward Internal Medicine Group",
        "Caloosa Family Health",
    ),
    referral_letterhead="920 Palm Hollow Road, Palmetto Point, FL 34221   (941) 555-0187",
    labs=(
        "Cypress Bay Pathology Associates",
        "Gulfshore Regional Laboratory",
        "Sabal Diagnostics (reference lab)",
    ),
    outside_labs=(
        "Sunrise Isles Hospital Laboratory",
        "Northbridge Pathology Group",
        "Lakeview Anatomic Pathology",
        "Statewide Reference Pathology",
    ),
    source_facilities=(
        "Sunrise Isles Community Hospital",
        "Northbridge Surgical Center",
        "Lakeview Endoscopy Partners",
        "Bay Terrace Regional Medical Center",
        "Out-of-state practice, records faxed",
        "Retired practice, chart transferred",
    ),
    seed=710,
    patients=12000,
    history_start=dt.date(2019, 1, 7),
    procedures_per_patient=1.3,
)


# ---------------------------------------------------------------------------
# B — Harbor Point Gastroenterology (US, Massachusetts)
# ---------------------------------------------------------------------------
#
# Small, new and academic. Two sites, an eighth of Cypress Bay's patients, and a
# record that starts in 2022 because the practice does. It is the network whose
# forms are complete and whose endoscopists find the most — which is why the
# withdrawal beat takes THIS one out: remove it and the pooled ranking changes
# visibly, which is the point of showing a withdrawal at all.
#
# A high adenoma detection rate here is not a compliment paid to invented
# doctors. It is what lets the researcher's question have an answer with a
# spread in it, and the spread is what makes "pool three networks" worth doing.

HARBOR_POINT = NetworkProfile(
    key="harbor-point",
    dataset="harborpoint-gastroenterology",
    network_name="Harbor Point Gastroenterology",
    network_short="HPG",
    table_prefix="hpg_",
    release_dataset="harborpoint-release",
    release_prefix="hpg_release_",
    coverage_note=(
        "Complete capture over a short history. The record begins in June 2022 "
        "because the practice does, so any comparison reaching further back is "
        "comparing a full network with a partial one. Within that period nothing "
        "is missing: every examination carries its preparation score and its "
        "withdrawal time, and every surveillance interval reached the record."
    ),
    mrn_prefix="HP-",
    accession_prefix="HPG",
    state="MA",
    postal_prefix="02",
    letterhead="31 Drydock Avenue, Suite 400, Harbor Point, MA 02210   (617) 555-0142",
    surveillance_letterhead="31 Drydock Avenue, Suite 210, Harbor Point, MA 02210   (617) 555-0168",
    sites=(
        Site("H01", "Harbor Point Endoscopy Institute", "Harbor Point", "MA", 5, "2022-06-06", 68),
        Site("H02", "Mill Brook Digestive Health", "Mill Brook", "MA", 2, "2024-02-05", 32),
    ),
    provider_roles=(
        ("Gastroenterologist", 9),
        ("Advanced endoscopist", 4),
        ("Colorectal surgeon", 2),
        ("Nurse practitioner", 3),
        ("Physician assistant", 2),
        ("Surveillance coordinator", 3),
        ("Pathologist", 4),
    ),
    area_codes=("617", "857", "781"),
    streets=(
        "Drydock Avenue", "Quincy Shore Way", "Beacon Hollow", "Fort Point Row",
        "Charter Street", "Wharfside Lane", "Granite Mill Road", "Brookline Rise",
        "Copley Terrace", "Shawmut Crossing", "Pier Four Walk", "Mill Brook Close",
    ),
    payers=(
        ("Commercial", "Bay State Preferred PPO", 29),
        ("Commercial", "Charter Health Group Plan", 17),
        ("Commercial", "Longwood Academic Select", 11),
        ("Medicare Part B", "Traditional Medicare", 21),
        ("Medicare Advantage", "Harborview Advantage HMO", 10),
        ("Medicaid", "Commonwealth Managed Care", 8),
        ("Self-pay", "Self-pay", 4),
    ),
    referring_practices=(
        "Fort Point Internal Medicine",
        "Mill Brook Family Practice",
        "Charter Street Primary Care",
        "Quincy Shore Medical Associates",
    ),
    referral_letterhead="18 Fort Point Channel Road, Harbor Point, MA 02210   (617) 555-0124",
    labs=(
        "Harbor Point Academic Pathology",
        "Charter River Histopathology",
        "Commonwealth Reference Laboratory (reference lab)",
    ),
    outside_labs=(
        "Mill Brook Hospital Laboratory",
        "Granite State Pathology Associates",
        "Seaport Anatomic Pathology",
        "Statewide Reference Pathology",
    ),
    source_facilities=(
        "Mill Brook General Hospital",
        "Seaport Ambulatory Surgery Center",
        "Granite State Endoscopy",
        "Academic medical centre, records released on request",
        "Out-of-state practice, records faxed",
    ),
    seed=909_002,
    patients=3400,
    history_start=dt.date(2022, 6, 13),
    procedures_per_patient=1.6,
    # Complete forms: no missingness knob is set at all. The contrast with
    # Ridgeline is the whole of the data-quality story.
    prep_quality=(("Excellent", 44), ("Good", 48), ("Fair", 7), ("Poor", 1)),
    withdrawal_mean=13.1,
    withdrawal_sd=2.4,
    # An adenoma detection rate in the middle forties. Deliberately not higher:
    # the top decile of endoscopists sits near 45%, and a synthetic practice
    # detecting adenomas in half of everybody it scopes is the kind of number a
    # gastroenterologist stops reading at.
    lesion_counts=(
        (0, 38), (1, 24), (2, 17), (3, 10), (4, 5), (5, 3), (6, 2), (8, 1), (12, 0),
    ),
)


# ---------------------------------------------------------------------------
# C — Ridgeline Endoscopy Partners (US, Colorado)
# ---------------------------------------------------------------------------
#
# Eleven sites across a rural service area, a record going back to 2016, a feed
# that runs two months behind, and forms that are filled in when there is time.
# Its numbers are worse on every axis AND partly unmeasurable, which is the more
# interesting half: a third of its examinations carry no preparation score, so
# they cannot enter an adequate-prep denominator at all, and a fifth of its
# surveillance intervals never reached the structured record, so those patients
# have no recall row and are invisible to every overdue query the practice runs.
#
# Nothing here says rural medicine is worse. It says this network's REGISTRY is
# thinner, which is a documented and extremely common fact about community
# practices — and it is the fact that makes a pooled analysis need the gate's
# verdict log as much as it needs the rows.

RIDGELINE = NetworkProfile(
    key="ridgeline",
    dataset="ridgeline-endoscopy",
    network_name="Ridgeline Endoscopy Partners",
    network_short="REP",
    table_prefix="rep_",
    release_dataset="ridgeline-release",
    release_prefix="rep_release_",
    coverage_note=(
        "Incomplete capture, declared. About a third of structured examinations "
        "carry no bowel-preparation score, a fifth describe the preparation in "
        "prose instead of scoring it, nearly half carry no withdrawal time, and "
        "about a fifth of surveillance intervals never reached the structured "
        "record — so those patients generate no recall at all. The feed also runs "
        "about two months behind, so the most recent months are thinner than they "
        "will be. Any rate computed here must say what it excluded; the "
        "prep_missing counts in this release are what it excluded."
    ),
    mrn_prefix="RG-",
    accession_prefix="RDG",
    state="CO",
    postal_prefix="81",
    letterhead="2240 Divide Road, Ridgeline, CO 81432   (970) 555-0115",
    surveillance_letterhead="2240 Divide Road, Annex B, Ridgeline, CO 81432   (970) 555-0193",
    sites=(
        Site("R01", "Ridgeline Endoscopy Center", "Ridgeline", "CO", 3, "2016-03-14", 17),
        Site("R02", "San Miguel Valley GI", "San Miguel Valley", "CO", 2, "2016-09-19", 13),
        Site("R03", "Dolores Mesa Digestive Care", "Dolores Mesa", "CO", 2, "2017-05-08", 11),
        Site("R04", "Gunnison Flats Endoscopy", "Gunnison Flats", "CO", 1, "2018-01-22", 9),
        Site("R05", "Cimarron Pass Clinic", "Cimarron Pass", "CO", 1, "2018-11-05", 8),
        Site("R06", "High Lonesome Gastroenterology", "High Lonesome", "CO", 2, "2019-06-17", 10),
        Site("R07", "Paradox Basin Health Partners", "Paradox Basin", "CO", 1, "2020-02-10", 7),
        Site("R08", "Wolf Creek Digestive Associates", "Wolf Creek", "CO", 2, "2021-04-12", 8),
        Site("R09", "Uncompahgre Valley Endoscopy", "Uncompahgre Valley", "CO", 1, "2022-08-15", 6),
        Site("R10", "Red Table Mesa Clinic", "Red Table Mesa", "CO", 1, "2023-03-06", 6),
        Site("R11", "Slumgullion Ridge Health Center", "Slumgullion Ridge", "CO", 1, "2024-09-09", 5),
    ),
    provider_roles=(
        ("Gastroenterologist", 21),
        ("Advanced endoscopist", 3),
        ("Colorectal surgeon", 4),
        ("Nurse practitioner", 9),
        ("Physician assistant", 7),
        # Six coordinators for eleven sites and eighteen thousand patients. This
        # is the arithmetic behind `missing_interval_rate` and the overdue tail:
        # the work is not done because there is nobody to do it.
        ("Surveillance coordinator", 6),
        ("Pathologist", 5),
    ),
    area_codes=("970", "719"),
    streets=(
        "Divide Road", "Gambel Oak Lane", "Chokecherry Bend", "Sagebrush Spur",
        "Ponderosa Reach", "Columbine Hollow", "Switchback Trail", "Dry Creek Road",
        "Rabbitbrush Row", "Elk Camp Way", "Aspen Draw", "Mesa Verde Circle",
        "Tailings Pond Road", "Hanging Flume Court",
    ),
    payers=(
        ("Medicare Part B", "Traditional Medicare", 31),
        ("Medicare Advantage", "Rocky Mountain Advantage HMO", 13),
        ("Medicaid", "Colorado Health First Managed Care", 18),
        ("Commercial", "Western Slope Employer Plan", 12),
        ("Commercial", "San Juan Cooperative Health", 8),
        ("Commercial", "Mesa Ridge Select", 5),
        ("Self-pay", "Self-pay", 13),
    ),
    referring_practices=(
        "Divide Road Family Medicine",
        "San Miguel Valley Health Clinic",
        "Dry Creek Primary Care",
        "Four Corners Community Health",
    ),
    referral_letterhead="1105 Dry Creek Road, San Miguel Valley, CO 81426   (970) 555-0151",
    labs=(
        "Ridgeline Regional Pathology",
        "Front Range Histopathology",
        "Western Reference Laboratory (reference lab)",
    ),
    outside_labs=(
        "San Miguel County Hospital Laboratory",
        "Front Range Pathology Group",
        "Four Corners Anatomic Pathology",
        "Statewide Reference Pathology",
    ),
    source_facilities=(
        "San Miguel County Hospital",
        "Gunnison Flats Critical Access Hospital",
        "Front Range Surgical Center",
        "Indian Health Service clinic, records requested",
        "Out-of-state practice, records faxed",
        "Retired practice, chart transferred",
        "Facility closed, records with state custodian",
    ),
    seed=909_003,
    patients=18000,
    history_start=dt.date(2016, 3, 21),
    procedures_per_patient=1.1,
    feed_lag_days=61,
    prep_quality=(("Excellent", 17), ("Good", 47), ("Fair", 25), ("Poor", 11)),
    withdrawal_mean=8.9,
    withdrawal_sd=2.9,
    lesion_counts=(
        (0, 62), (1, 19), (2, 9), (3, 5), (4, 2), (5, 1), (6, 1), (8, 1), (12, 0),
    ),
    missing_bbps_rate=0.34,
    missing_withdrawal_rate=0.46,
    prep_free_text=0.21,
    missing_interval_rate=0.19,
)


PROFILES = {profile.key: profile for profile in (CYPRESS_BAY, HARBOR_POINT, RIDGELINE)}


def by_key(key: str) -> NetworkProfile:
    try:
        return PROFILES[key]
    except KeyError:
        known = ", ".join(sorted(PROFILES))
        raise SystemExit(f"unknown network {key!r}; known networks are {known}") from None
