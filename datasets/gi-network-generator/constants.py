"""The facts every other module in this generator has to agree about, for every
network it generates.

What is here is true of the specialty and of the snapshot: the anatomy of a
colon, the age US screening starts at, the day the corpus is a picture of. What
is true of one practice and not another is a `NetworkProfile` field in
`profiles.py` — volumes, periods, sites, and how completely the forms get filled
in.

`AS_OF` used to be spelled out in four files. Nothing went wrong, but nothing was
stopping it either: a corpus whose tables stop one day short of the date its
documents are written on is not obviously broken from inside any one module, and
"no row is in the future" is checked against whichever copy the checker imported.
One definition, imported everywhere, removes the question.
"""

from __future__ import annotations

import datetime as dt

#: The day the corpus is a snapshot of. Everything — the overdue day counts, the
#: fixed timestamps in the rendered documents — is relative to this and never to
#: the wall clock, which is what makes a run reproducible on a different day.
#:
#: It is deliberately NOT per network. Three corpora pooled into one question are
#: three practices read on the same day, and an "overdue by 240 days" that means a
#: different today in each of them is a number nobody can add up. How far behind
#: this date a particular practice's structured capture runs is a separate and
#: real thing, and that one IS per network: `NetworkProfile.feed_lag_days`.
AS_OF = dt.date(2026, 11, 17)

#: Tables are prefixed and live in an existing database rather than one of their
#: own, because that is what the marketplace stand's ClickHouse can actually
#: serve: the account the marketplace holds there carries
#: `CREATE DATABASE, CREATE TABLE ON marketplace.*` and nothing wider, so a
#: `cypressbay_gi` database cannot be created without host access to that server.
#: The prefix is therefore part of the published names — the dataset card declares
#: `cbdh_patients`, not `patients` — and it matches the clinic datasets already on
#: that stand, which live in `marketplace` as `roczen_*` and `autonomyx_*`.
#:
#: Which prefix a network uses is a `NetworkProfile` field; three corpora sharing
#: one database is the reason it has to be, and the reason the reader accounts are
#: scoped per prefix rather than per database.
#:
#: A ClickHouse you do own takes `--database <yours> --table-prefix ""`; change the
#: card's `database` to match if you publish from there.
DEFAULT_DATABASE = "marketplace"

#: The colon, from the anus inward — the order a colonoscope travels in.
#:
#: This list is why an incomplete examination can be described honestly. A scope
#: that stopped at the splenic flexure saw the rectum, the sigmoid and the
#: descending colon and nothing beyond, so a polyp reported in the cecum on that
#: examination is not a detail a reader forgives: it is the first thing a
#: gastroenterologist notices, and it makes them stop trusting the rest.
REACH_ORDER = [
    "Rectum",
    "Sigmoid colon",
    "Descending colon",
    "Transverse colon",
    "Ascending colon",
    "Cecum",
]

#: Where an examination can be abandoned, and how far the scope got when it was.
#: The value is the number of leading entries of `REACH_ORDER` that were seen.
ABANDON_POINTS = [
    ("the sigmoid colon", 2),
    ("the descending colon", 3),
    ("the splenic flexure", 3),
    ("the transverse colon", 4),
    ("the hepatic flexure", 4),
]

#: A flexible sigmoidoscopy examines the left colon only. It is not a failed
#: colonoscopy and it earns no colonic surveillance interval — there is no
#: statement to make about a colon two thirds of which was never looked at.
SIGMOIDOSCOPY_REACH = 3  # rectum, sigmoid, descending

#: The age at which average-risk colorectal cancer screening begins in the US.
SCREENING_START_AGE = 45

#: Recall statuses that still have a next action in them. Everything else is
#: closed: the examination happened, the patient refused, moved, or died.
#:
#: This set is what a planted document has to sit on. A document that proves the
#: record is wrong proves nothing when the record is closed — "the right answer is
#: a repeat within three months" is not an answer about a patient who died, and an
#: assistant that reads the row will say so, correctly, in front of the customer.
LIVE_RECALL_STATUSES = frozenset(
    {"not_yet_due", "due_soon", "scheduled", "overdue", "lost_to_follow_up"}
)


def reachable_segments(reach: int) -> list[str]:
    """The segments a scope that got `reach` segments in could have examined."""
    return REACH_ORDER[: max(1, reach)]
