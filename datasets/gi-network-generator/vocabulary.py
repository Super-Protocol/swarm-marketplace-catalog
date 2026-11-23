"""The clinical and demographic vocabularies the generator draws from.

Kept apart from the generation logic because this is the part a clinician reads to
judge whether the data is recognisable. Nothing here is taken from a real record,
a real registry or a real person: the name pools are ordinary US given names and
surnames combined at random, the practice and payer names are invented, and the
NPIs are deliberately invalid (a real NPI begins with 1 or 2 — every one below
begins with 9, so none of them can collide with an issued number).

What is in this file is the part that is the same for every network the generator
produces: the specialty, not the practice. Anything that differs between the
three clinic networks — the network's name and sites, its payers, its
laboratories, its streets and area codes, how well its examinations go and how
completely its forms are filled in — is a field on a `NetworkProfile` in
`profiles.py`. The division is not cosmetic: a pool of given names shared across
three practices is correct, and a pool of payer plans shared across Florida,
Massachusetts and Colorado is not.
"""

# ---------------------------------------------------------------------------
# What a GI network is staffed with
# ---------------------------------------------------------------------------
#
# How MANY of each is a `NetworkProfile` field — an eleven-site rural network and
# a two-site academic one are not the same establishment, and the surveillance
# coordinator count in particular is load-bearing: a network with too few of them
# for its volume is a network whose recalls go unworked, which is the story one
# of the three corpora is deliberately telling. What each of them DOES is the
# same everywhere, so the roles and their credentials live here.

CREDENTIALS = {
    "Gastroenterologist": "MD",
    "Advanced endoscopist": "MD",
    "Colorectal surgeon": "MD, FACS",
    "Nurse practitioner": "APRN",
    "Physician assistant": "PA-C",
    "Surveillance coordinator": "RN",
    "Pathologist": "MD",
}

#: Who performs procedures. Everybody else in the establishment appears in the
#: record through a different column.
ENDOSCOPIST_ROLES = ("Gastroenterologist", "Advanced endoscopist", "Colorectal surgeon")

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

LANGUAGES = [
    ("English", 76), ("Spanish", 17), ("Haitian Creole", 4), ("Portuguese", 2),
    ("Vietnamese", 1),
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

#: Why a patient under 45 is having a *screening* colonoscopy. Average-risk
#: screening begins at 45 in the US, so "age-appropriate" is false below it and a
#: younger patient is scoped for a reason that has to be stated.
EARLY_SCREENING_INDICATIONS = [
    "First-degree relative with colorectal cancer, screening begun 10 years early",
    "Two first-degree relatives with colorectal cancer",
    "Lynch syndrome, known pathogenic variant — surveillance program",
    "Familial adenomatous polyposis, known family mutation",
    "Serrated polyposis syndrome in a first-degree relative",
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

# The bowel-preparation distribution, the withdrawal-time distribution and the
# lesion count per examination are `NetworkProfile` fields: they are how well a
# particular unit does its work, which is exactly what a cross-network comparison
# is asking about. A well-run unit reports an inadequate preparation — Boston
# Bowel Preparation Scale under 6 — in well under a tenth of examinations.
# "Fair" straddles the threshold, which is why the score and not the adjective
# decides adequacy, in every profile.

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

#: What an outreach attempt can end in, by the channel it went out on. Split by
#: channel because the flat list produced rows that cannot happen: a telephone
#: call that came back "mail returned undeliverable", a letter answered by
#: voicemail, a portal message returned by the post office. The coordinator-note
#: scenario asks the model to cross-check outreach against the narrative, so an
#: impossible row there is a contradiction nobody planted and nobody can resolve.
OUTCOMES_BY_CHANNEL = {
    "Telephone": [
        ("No answer", 34),
        ("Voicemail left", 30),
        ("Wrong or disconnected number", 14),
        ("Reached — will call back", 13),
        ("Reached — declined", 5),
        ("Reached — appointment scheduled", 4),
    ],
    "Letter": [
        ("No response to letter", 62),
        ("Mail returned undeliverable", 38),
    ],
    "Patient portal": [
        ("Portal message unread", 58),
        ("Portal message read, no reply", 30),
        ("Reached — will call back", 12),
    ],
    "SMS": [
        ("No reply to text message", 64),
        ("Text message undeliverable", 24),
        ("Reached — will call back", 12),
    ],
}

#: Channels a patient can answer on. A letter is the only one-way channel here:
#: an outreach attempt that ends "reached" cannot have been a letter, so a recall
#: closed on a letter attempt is recorded as the call back that actually closed it.
TWO_WAY_CHANNELS = {"Telephone", "Patient portal", "SMS"}

#: The two outcomes that close a recall. They are assigned deterministically — the
#: last attempt against a recall that ended scheduled or declined — rather than
#: sampled, so they are listed apart from the per-channel pools.
CLOSING_OUTCOMES = {"Reached — declined", "Reached — appointment scheduled"}

#: The outcomes that mean the contact details on file are wrong, rather than that
#: the patient will not come. Different problem, different fix — and the
#: coordinator notes are about exactly that distinction.
UNREACHABLE_OUTCOMES = [
    "Wrong or disconnected number",
    "Mail returned undeliverable",
    "Text message undeliverable",
]

DEFERRAL_REASONS = [
    "Active cardiac workup",
    "Anticoagulation cannot be interrupted",
    "On palliative care pathway",
    "Pregnancy",
    "Recent major surgery",
]
