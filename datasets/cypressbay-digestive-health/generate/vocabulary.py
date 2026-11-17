"""The clinical and demographic vocabularies the generator draws from.

Kept apart from the generation logic because this is the part a clinician reads to
judge whether the data is recognisable. Nothing here is taken from a real record,
a real registry or a real person: the name pools are ordinary US given names and
surnames combined at random, the practice and payer names are invented, and the
NPIs are deliberately invalid (a real NPI begins with 1 or 2 — every one below
begins with 9, so none of them can collide with an issued number).
"""

# ---------------------------------------------------------------------------
# The clinic network
# ---------------------------------------------------------------------------

NETWORK_NAME = "Cypress Bay Digestive Health"
NETWORK_SHORT = "CBDH"

#: site_id, name, city, state, endoscopy suites, opened
SITES = [
    ("S01", "Cypress Bay Endoscopy Center", "Cypress Bay", "FL", 4, "2009-03-02"),
    ("S02", "Palmetto Point Gastroenterology", "Palmetto Point", "FL", 3, "2012-07-16"),
    ("S03", "Osprey Landing Digestive Care", "Osprey Landing", "FL", 2, "2015-01-12"),
    ("S04", "Indian Shoal Endoscopy Suite", "Indian Shoal", "FL", 2, "2017-09-05"),
    ("S05", "Coral Ridge GI Associates", "Coral Ridge", "FL", 3, "2019-04-01"),
    ("S06", "Mangrove Park Ambulatory Endoscopy", "Mangrove Park", "FL", 2, "2021-10-18"),
]

PROVIDER_ROLES = [
    ("Gastroenterologist", 24),
    ("Advanced endoscopist", 5),
    ("Colorectal surgeon", 3),
    ("Nurse practitioner", 6),
    ("Physician assistant", 4),
    ("Surveillance coordinator", 8),
    ("Pathologist", 6),
]

SPECIALTY_FOCUS = [
    "General gastroenterology",
    "Colorectal cancer screening",
    "Advanced polypectomy and EMR",
    "Inflammatory bowel disease",
    "Hereditary GI cancer risk",
    "Pancreaticobiliary",
    "Motility",
]

# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

GIVEN_FEMALE = [
    "Alicia", "Barbara", "Carmen", "Deborah", "Elaine", "Felicia", "Gloria",
    "Harriet", "Ingrid", "Janice", "Katherine", "Lorraine", "Marcia", "Nadine",
    "Olivia", "Patricia", "Quiana", "Rosalind", "Sylvia", "Theresa", "Ursula",
    "Vivian", "Wanda", "Yolanda", "Zelda", "Annette", "Bernice", "Clarisse",
    "Dolores", "Estella", "Frances", "Georgina", "Hilda", "Imelda", "Josefina",
    "Kendra", "Leticia", "Mabel", "Noelle", "Odette", "Priscilla", "Ramona",
    "Shirley", "Tabitha", "Valentina", "Winifred", "Xiomara", "Yvette",
]

GIVEN_MALE = [
    "Alonzo", "Bernard", "Clifford", "Desmond", "Edwin", "Franklin", "Gerald",
    "Horace", "Irving", "Jerome", "Kenneth", "Lionel", "Marshall", "Norman",
    "Orlando", "Percival", "Quentin", "Rodney", "Stanley", "Terrence", "Ulysses",
    "Vernon", "Wallace", "Xavier", "Yusuf", "Zachariah", "Abner", "Barrett",
    "Cornelius", "Dwight", "Elmer", "Floyd", "Gustavo", "Hubert", "Ignacio",
    "Julius", "Kermit", "Leland", "Mortimer", "Nelson", "Octavio", "Porter",
    "Reginald", "Sheldon", "Thaddeus", "Vance", "Willard", "Zebulon",
]

SURNAMES = [
    "Abernathy", "Balfour", "Castellanos", "Dunleavy", "Ellsworth", "Fairbanks",
    "Granados", "Hollingsworth", "Ivey", "Jaramillo", "Kirkpatrick", "Lindqvist",
    "Mendenhall", "Nakashima", "Ocampo", "Pemberton", "Quintanilla", "Rutherford",
    "Stoddard", "Thibodeaux", "Underhill", "Valderrama", "Whitfield", "Yearwood",
    "Zamarripa", "Ashcombe", "Breckenridge", "Calloway", "Dombrowski", "Easterling",
    "Fontenot", "Galbraith", "Hargrove", "Isbell", "Jennings", "Kowalczyk",
    "Lefebvre", "Marchetti", "Nightingale", "Ostrowski", "Pettigrew", "Quarles",
    "Ridgeway", "Sandoval", "Tillerson", "Uribe", "Vanterpool", "Westbrook",
    "Yarborough", "Zeppelin", "Arrington", "Blackwood", "Crenshaw", "Delacroix",
]

STREETS = [
    "Mangrove Walk", "Sea Grape Lane", "Tarpon Row", "Live Oak Terrace",
    "Pelican Crossing", "Saw Palmetto Drive", "Coquina Court", "Banyan Reach",
    "Spanish Moss Way", "Heron Point Road", "Sabal Palm Circle", "Egret Bend",
    "Loggerhead Trail", "Buttonwood Place", "Cordgrass Avenue", "Snook Harbour Road",
]

LANGUAGES = [
    ("English", 76), ("Spanish", 17), ("Haitian Creole", 4), ("Portuguese", 2),
    ("Vietnamese", 1),
]

PAYERS = [
    ("Medicare Part B", "Traditional Medicare", 34),
    ("Medicare Advantage", "Sunstate Advantage HMO", 16),
    ("Medicare Advantage", "Gulf Coast Senior Choice PPO", 8),
    ("Commercial", "Cypress Health Alliance PPO", 14),
    ("Commercial", "Everglade Mutual Select", 9),
    ("Commercial", "Sawgrass Employer Plan", 7),
    ("Medicaid", "State Medicaid Managed Care", 7),
    ("Self-pay", "Self-pay", 5),
]

# ---------------------------------------------------------------------------
# Procedures
# ---------------------------------------------------------------------------

PROCEDURE_TYPES = [
    ("Screening colonoscopy", 44),
    ("Surveillance colonoscopy", 31),
    ("Diagnostic colonoscopy", 17),
    ("Flexible sigmoidoscopy", 4),
    ("EGD", 4),
]

INDICATIONS_BY_TYPE = {
    "Screening colonoscopy": [
        "Average-risk colorectal cancer screening, age-appropriate",
        "First-degree relative with colorectal cancer",
        "Positive fecal immunochemical test",
        "Positive multitarget stool DNA test",
    ],
    "Surveillance colonoscopy": [
        "Post-polypectomy surveillance, prior adenoma",
        "Post-polypectomy surveillance, prior sessile serrated lesion",
        "Surveillance after piecemeal resection",
        "Surveillance, prior advanced adenoma",
    ],
    "Diagnostic colonoscopy": [
        "Iron-deficiency anemia",
        "Hematochezia",
        "Change in bowel habit",
        "Chronic diarrhea",
        "Abdominal pain, lower quadrant",
        "Positive FIT with symptoms",
    ],
    "Flexible sigmoidoscopy": [
        "Rectal bleeding, limited examination requested",
        "Known distal lesion follow-up",
    ],
    "EGD": [
        "Dyspepsia refractory to therapy",
        "Dysphagia",
        "Barrett's esophagus surveillance",
    ],
}

# A well-run unit reports an inadequate preparation — Boston Bowel Preparation
# Scale under 6 — in well under a tenth of examinations. "Fair" straddles the
# threshold, which is why the score and not the adjective decides adequacy.
PREP_QUALITY = [("Excellent", 30), ("Good", 54), ("Fair", 13), ("Poor", 3)]
SEDATION = [("Monitored anesthesia care", 71), ("Moderate sedation", 27), ("General anesthesia", 2)]
ASA_CLASS = [("I", 11), ("II", 54), ("III", 32), ("IV", 3)]
COMPLICATIONS = [
    ("None", 985),
    ("Post-polypectomy bleeding, managed endoscopically", 6),
    ("Post-polypectomy bleeding, admitted", 2),
    ("Cardiopulmonary event during sedation", 4),
    ("Abdominal pain requiring observation", 3),
]

SEGMENTS = [
    ("Cecum", 11), ("Ascending colon", 14), ("Transverse colon", 15),
    ("Descending colon", 13), ("Sigmoid colon", 27), ("Rectum", 20),
]

MORPHOLOGY = [
    ("Paris Is — sessile", 46), ("Paris IIa — flat elevated", 29),
    ("Paris Ip — pedunculated", 17), ("Paris IIb — completely flat", 5),
    ("Paris IIc — depressed", 3),
]

RESECTION_METHODS = [
    ("Cold snare polypectomy", 52),
    ("Cold forceps biopsy", 14),
    ("Hot snare polypectomy", 20),
    ("Endoscopic mucosal resection, en bloc", 7),
    ("Endoscopic mucosal resection, piecemeal", 5),
    ("Not resected — referred for surgical management", 1),
    ("Not resected — tattooed for later resection", 1),
]

#: The laboratories this network sends its own specimens to.
LABS = [
    "Cypress Bay Pathology Associates",
    "Gulfshore Regional Laboratory",
    "Sabal Diagnostics (reference lab)",
]

#: Laboratories that appear on a report from another facility. Kept separate
#: because our own lab's name on an outside report is the kind of detail that
#: makes a reader stop trusting the whole corpus.
OUTSIDE_LABS = [
    "Sunrise Isles Hospital Laboratory",
    "Northbridge Pathology Group",
    "Lakeview Anatomic Pathology",
    "Statewide Reference Pathology",
]

# ---------------------------------------------------------------------------
# Surveillance and recall
# ---------------------------------------------------------------------------

#: Every value `recalls.recall_status` can take. A recall is created for every
#: surveillance recommendation; the ones a later examination answered are
#: `completed` and name that examination, and only the patient's most recent
#: recommendation can be live.
RECALL_STATUSES = [
    "not_yet_due",          # due date more than 90 days away, nothing to do yet
    "due_soon",             # due within 90 days, no appointment booked
    "scheduled",            # appointment booked
    "completed",            # a later examination answered this recommendation
    "overdue",              # past due, no appointment, outreach continuing
    "lost_to_follow_up",    # past due, outreach exhausted or undeliverable
    "declined",             # patient declined
    "deferred_medical",     # postponed for a clinical reason
    "moved_away",           # transferred out of the network
    "deceased",
]

OUTREACH_CHANNELS = [("Telephone", 44), ("Patient portal", 24), ("Letter", 19), ("SMS", 13)]

OUTREACH_OUTCOMES = [
    ("No answer", 26),
    ("Voicemail left", 21),
    ("Reached — appointment scheduled", 17),
    ("Reached — will call back", 11),
    ("Reached — declined", 5),
    ("Wrong or disconnected number", 8),
    ("Mail returned undeliverable", 5),
    ("Portal message unread", 7),
]

DEFERRAL_REASONS = [
    "Active cardiac workup",
    "Anticoagulation cannot be interrupted",
    "On palliative care pathway",
    "Pregnancy",
    "Recent major surgery",
]

SOURCE_FACILITIES = [
    "Sunrise Isles Community Hospital",
    "Northbridge Surgical Center",
    "Lakeview Endoscopy Partners",
    "Bay Terrace Regional Medical Center",
    "Out-of-state practice, records faxed",
    "Retired practice, chart transferred",
]
