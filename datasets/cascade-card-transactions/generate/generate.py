#!/usr/bin/env python3
"""Generate the Cascade card transactions: one row per transaction, as ClickHouse rows.

    datasets/cascade-card-transactions/generate/generate.py --self-test
    datasets/cascade-card-transactions/generate/generate.py --out transactions.tsv

Why this exists: `Confidential Transaction Model` declares a `clickhouse-http/v1` data slot and
nothing else, because that is the only interface the marketplace gatekeeper can serve — and
nothing in this catalogue was that shape. `retail-transactions` looks close on paper and is not
it: its connection is a placeholder pointing at `ch.acme-data.internal`, it is
`clickhouse-native/v1`, it is `access.mode: direct`, and there are no rows behind it.

The columns are TabFormer's own spelling — `User`, `Card`, `Year`, `Month`, `Day`, `Time`,
`Amount`, `Use Chip`, `Merchant Name`, `Merchant City`, `Merchant State`, `Zip`, `MCC`,
`Is Fraud?` — so a deployment reading this dataset answers NOTHING in the listing's "Your
transactions" section. That is deliberate: the column parameters exist for an institution whose
extract is spelled its own way, and a demonstration that also has to explain a column mapping
demonstrates two things badly.

Everything here is synthetic. No cardholder, merchant or transaction is real or derived from a
real one. The GEOGRAPHY is real (`places.py` says why: the checkpoint's vocabulary enumerates US
states and 3-digit ZIP prefixes, so an invented one tokenises to a bucket nobody trained on).

Deterministic: one seed, one table, byte for byte.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from places import CATEGORIES, FOREIGN, PLACES, UNKNOWN_CATEGORIES  # noqa: E402

SEED = 20261124
USERS = 1200
ROWS_PER_USER = 250

# The vocabulary has CUST_0 to CUST_2999 and CARD_0 to CARD_9, so a portfolio
# this size is inside it and the coverage report says so. A bigger one would
# clip customers onto a shared identity — which is a real limitation of the
# shipped checkpoint and is stated on the listing, not something to demonstrate
# accidentally here.
CARDS_PER_USER = (1, 4)

START = dt.date(2023, 1, 1)
END = dt.date(2025, 10, 31)

ENTRY_MODES = [
    ('Swipe Transaction', 0.46),
    ('Chip Transaction', 0.39),
    ('Online Transaction', 0.15),
]

# How often a card is used somewhere its holder does not live, and how often
# abroad. Both matter to the model: the state and the ZIP prefix are tokens.
AWAY_FROM_HOME = 0.17
ABROAD = 0.008
UNKNOWN_CATEGORY = 0.015
REFUND = 0.009

# TabFormer's own fraud rate is about one transaction in a thousand, and a
# demonstration corpus with a comfortable 5% would make the baseline in
# `01_*.ipynb` look far better than a fraud model ever does.
FRAUD_BASE = 0.0011


def gaussian(rng: random.Random) -> float:
    """A standard normal from `rng.random()` alone — see the single-cell generator."""
    first = rng.random()
    while first <= 0.0:
        first = rng.random()
    return math.sqrt(-2.0 * math.log(first)) * math.cos(2.0 * math.pi * rng.random())


def pick(rng: random.Random, weighted: list[tuple[object, float]]) -> object:
    cut = rng.random() * sum(weight for _, weight in weighted)
    running = 0.0
    for value, weight in weighted:
        running += weight
        if cut <= running:
            return value
    return weighted[-1][0]


def choice(rng: random.Random, items: list) -> object:
    return items[min(int(rng.random() * len(items)), len(items) - 1)]


def amount(rng: random.Random, category: str) -> float:
    """A plausible ticket for the category, log-normal around its own median."""
    median, spread = {
        'grocery': (54.0, 0.6), 'fuel': (41.0, 0.45), 'restaurant': (38.0, 0.7),
        'fast food': (11.0, 0.5), 'pharmacy': (22.0, 0.8), 'department store': (68.0, 0.9),
        'wholesale club': (121.0, 0.7), 'clothing': (57.0, 0.8), 'shoes': (72.0, 0.6),
        'electronics': (189.0, 1.0), 'computers': (620.0, 0.9), 'furniture': (410.0, 1.0),
        'appliances': (330.0, 0.9), 'home goods': (48.0, 0.8), 'sporting goods': (74.0, 0.9),
        'books': (24.0, 0.7), 'gifts': (31.0, 0.8), 'cosmetics': (39.0, 0.7),
        'jewellery': (480.0, 1.1), 'off-licence': (34.0, 0.7), 'bar': (29.0, 0.7),
        'convenience': (14.0, 0.6), 'auto parts': (64.0, 0.9), 'auto body': (710.0, 0.9),
        'auto service': (210.0, 0.9), 'car wash': (16.0, 0.4), 'towing': (145.0, 0.6),
        'transit': (6.5, 0.5), 'taxi': (19.0, 0.6), 'coach': (44.0, 0.6),
        'tolls': (4.8, 0.5), 'airline': (318.0, 0.8), 'hotel': (214.0, 0.7),
        'car hire': (178.0, 0.7), 'travel agency': (690.0, 0.9), 'cruise': (1450.0, 0.7),
        'telecom': (72.0, 0.4), 'media': (28.0, 0.4), 'utilities': (118.0, 0.5),
        'insurance': (204.0, 0.6), 'doctor': (132.0, 0.8), 'dentist': (186.0, 0.8),
        'optician': (164.0, 0.7), 'hospital': (940.0, 1.1), 'medical': (276.0, 0.9),
        'legal': (520.0, 0.9), 'accounting': (295.0, 0.7), 'tax preparation': (188.0, 0.6),
        'salon': (46.0, 0.6), 'laundry': (21.0, 0.5), 'cleaning': (118.0, 0.6),
        'cinema': (17.0, 0.5), 'theatre': (58.0, 0.6), 'betting': (26.0, 1.0),
        'digital media': (13.0, 0.4), 'digital games': (26.0, 0.7),
        'periodicals': (9.5, 0.4), 'florist': (52.0, 0.6),
        'builders merchant': (142.0, 0.9), 'hardware': (38.0, 0.8),
        'garden centre': (61.0, 0.8), 'postal': (12.0, 0.6),
        'heating and plumbing': (380.0, 0.9), 'money transfer': (240.0, 0.8),
        'software': (89.0, 0.8), 'jeweller': (390.0, 1.0), 'charity': (45.0, 0.9),
        'subscription': (32.0, 0.4),
    }.get(category, (40.0, 0.8))
    return round(median * math.exp(spread * gaussian(rng)), 2)


def build() -> list[tuple]:
    rng = random.Random(SEED)
    span = (END - START).days
    categories = [(entry, 1.0) for entry in CATEGORIES]

    rows: list[tuple] = []
    for user in range(USERS):
        cards = 1 + int(rng.random() * (CARDS_PER_USER[1] - CARDS_PER_USER[0] + 1))
        cards = min(cards, 10)
        home = choice(rng, PLACES)
        # A holder's own spending rate, so the table is not 1,200 identical
        # customers with different indices.
        rate = math.exp(0.35 * gaussian(rng))
        count = max(40, int(ROWS_PER_USER * rate))

        for _ in range(count):
            mcc, category, merchants = (
                choice(rng, UNKNOWN_CATEGORIES) if rng.random() < UNKNOWN_CATEGORY
                else pick(rng, categories)
            )
            merchant = choice(rng, merchants)
            mode = pick(rng, [(name, weight) for name, weight in ENTRY_MODES])

            if mode == 'Online Transaction':
                # TabFormer's shape for a card-not-present row: the city and the
                # state say ONLINE and the postcode is absent. The absence is
                # the honest part — it is why `zip3` coverage is not 100% and
                # the coverage report says which field fell short.
                city, state, postcode = 'ONLINE', 'ONLINE', None
            elif rng.random() < ABROAD:
                state, city, postcode = choice(rng, FOREIGN)
            elif rng.random() < AWAY_FROM_HOME:
                state, city, postcode = choice(rng, PLACES)
            else:
                state, city, postcode = home

            when = START + dt.timedelta(days=min(int(rng.random() * (span + 1)), span))
            # Card spending is diurnal. The tokenizer carries the hour, so a
            # flat clock would train the model on a day nobody has.
            hour = int(pick(rng, [
                (8, 0.05), (9, 0.06), (10, 0.07), (11, 0.08), (12, 0.10), (13, 0.09),
                (14, 0.08), (15, 0.08), (16, 0.08), (17, 0.09), (18, 0.09), (19, 0.07),
                (20, 0.04), (21, 0.02),
            ]))
            minute = int(rng.random() * 60)

            value = amount(rng, category)
            if rng.random() < REFUND:
                value = -value

            risk = FRAUD_BASE
            if mode == 'Online Transaction':
                risk *= 4.0
            if value > 500:
                risk *= 3.0
            if state in {'ON', 'BC', 'QC'}:
                risk *= 6.0
            fraud = 'Yes' if rng.random() < risk else 'No'

            rows.append((
                user,
                min(int(rng.random() * cards), 9),
                when.year, when.month, when.day, f'{hour:02d}:{minute:02d}',
                f'${value:.2f}',
                mode,
                merchant, city, state, postcode, mcc, fraud,
            ))

    # Ordered by cardholder and then by time, which is the order the table's
    # sorting key keeps it in and the order the tokenizer's sequences read.
    rows.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[4], row[5]))
    return rows


COLUMNS = [
    'User', 'Card', 'Year', 'Month', 'Day', 'Time', 'Amount', 'Use Chip',
    'Merchant Name', 'Merchant City', 'Merchant State', 'Zip', 'MCC', 'Is Fraud?',
]

# What README.md publishes. Changing the generator means changing these,
# deliberately: "the same seed produces the same bytes" is the only claim a
# consumer can check without the stand, and a digest nobody asserts is a comment.
PUBLISHED_ROWS = 320_176
PUBLISHED_DIGEST = '150c38bd13253a1a8ac179ffb94dd1f4fe7ef50b5ab41f8bec1768fad7b431a5'

# The coverage table in README.md, to four places. These are the numbers a
# deployment's own `check_coverage()` will print, and the card explains each
# shortfall by name — so a generator change that moves one silently makes the
# card's explanation wrong.
PUBLISHED_COVERAGE = {
    'mcc': 0.9847,
    'state': 0.9933,
    'zip3': 0.9933,
}
PUBLISHED_OVERALL = 0.9952
PUBLISHED_FRAUD_RATE = 0.00210

README = pathlib.Path(__file__).resolve().parents[1] / 'README.md'


def selftest() -> int:
    """Assert what the card claims. Returns a process exit code."""
    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    rows = build()
    text = tabseparated(rows)

    # 1. Byte-level reproducibility, from two builds in one process — the stand
    #    regenerates by calling this twice, not from a fresh interpreter each time.
    require(tabseparated(build()) == text,
            'a second build in the same process differs from the first')

    actual = hashlib.sha256(text.encode('utf-8')).hexdigest()
    require(len(rows) == PUBLISHED_ROWS,
            f'{len(rows):,} rows, the card publishes {PUBLISHED_ROWS:,}')
    require(actual == PUBLISHED_DIGEST,
            f'sha256 {actual} , the card publishes {PUBLISHED_DIGEST}')
    print(f'  transactions {len(rows):,} rows  sha256 {actual}')
    print(f'  columns      {", ".join(COLUMNS)}')

    require(all(len(row) == len(COLUMNS) for row in rows),
            'a row is not as wide as the declared column list')

    # 2. The columns. `data.yaml` cannot declare them — the specification's
    #    column-name pattern forbids a space and a question mark — so README.md's
    #    CREATE TABLE is the card, and this is the check that it still describes
    #    the table this writes.
    readme = README.read_text()
    for column in COLUMNS:
        require(f'`{column}`' in readme,
                f'README.md does not name the column `{column}`')

    # 3. The value domains the shipped checkpoint's vocabulary depends on. Every
    #    one of these is a token: a value outside the domain does not fail, it
    #    tokenises to a catch-all and the model reads something it never trained
    #    on. Measured here rather than asserted from the generator's parameters.
    users = {row[0] for row in rows}
    require(max(users) <= 2999, f'a User index is {max(users)}; the vocabulary ends at CUST_2999')
    require(max(row[1] for row in rows) <= 9, 'a Card index is above CARD_9')
    require(all(row[7] in {name for name, _ in ENTRY_MODES} for row in rows),
            'an entry mode is outside the three the tokenizer maps')
    require(all(row[13] in {'Yes', 'No'} for row in rows),
            'a fraud label is neither Yes nor No')
    require(all(len(row[5]) == 5 and row[5][2] == ':' and row[5].replace(':', '').isdigit()
                for row in rows), 'a Time is not HH:MM')
    require(all(row[6].startswith('$') or row[6].startswith('-$') or row[6].startswith('$-')
                for row in rows), 'an Amount is not written as a dollar figure')
    require(all(row[11] is None or (len(row[11]) == 5 and row[11].isdigit())
                or row[10] in {state for state, _, _ in FOREIGN} for row in rows),
            'a domestic Zip is neither absent nor five digits')

    # 4. The coverage table. These are the numbers the deployment will print.
    unknown = {mcc for mcc, _, _ in UNKNOWN_CATEGORIES}
    foreign = {state for state, _, _ in FOREIGN}
    online = sum(1 for row in rows if row[7] == 'Online Transaction')
    # `zip3` is measured the way the deployment measures it, which is NOT "has a
    # postcode". `tfm_workspace.coverage()` does `Zip.fillna('00000')[:3]` and asks
    # whether that is digits, so a card-not-present row with no postcode is filled
    # to `000` and COUNTS AS COVERED — it tokenises, into one shared bucket. What
    # misses is a postcode that is not numeric at all, which here is only the
    # foreign ones. Published as 0.8507 when this card shipped, which was the
    # fraction of rows carrying a postcode: a true number about the data and the
    # wrong answer to what the deployment prints. Corrected against a real read
    # (SUP-900): the deployment reports 99.33%.
    measured = {
        'mcc': 1 - sum(1 for row in rows if row[12] in unknown) / len(rows),
        'state': 1 - sum(1 for row in rows if row[10] in foreign) / len(rows),
        'zip3': sum(1 for row in rows
                    if (row[11] or '00000')[:3].isdigit()) / len(rows),
    }
    print('  vocabulary coverage this table should report:')
    for field, expected in PUBLISHED_COVERAGE.items():
        print(f'    {field:10} {measured[field]:.4f}')
        require(round(measured[field], 4) == expected,
                f'{field} coverage is {measured[field]:.4f}, README.md publishes {expected}')
    print('    entrymode  1.0000   user 1.0000   card 1.0000')

    overall = (measured['mcc'] + measured['state'] + measured['zip3'] + 3.0) / 6
    print(f'    overall    {overall:.4f}')
    require(round(overall, 4) == PUBLISHED_OVERALL,
            f'overall coverage is {overall:.4f}, README.md publishes {PUBLISHED_OVERALL}')

    require(all(row[11] is None for row in rows if row[7] == 'Online Transaction'),
            'a card-not-present row carries a postcode, which is what the ZIP bucket explains')
    absent = sum(1 for row in rows if row[11] is None)
    print(f'  no postcode  {absent:,} rows ({absent / len(rows) * 100:.1f}%) — tokenised as `000`')

    fraud = sum(1 for row in rows if row[13] == 'Yes') / len(rows)
    require(round(fraud, 5) == PUBLISHED_FRAUD_RATE,
            f'the fraud rate is {fraud:.5f}, README.md publishes {PUBLISHED_FRAUD_RATE}')
    print(f'  fraud        {fraud * 100:.3f}%   cardholders {len(users)}   '
          f'period {rows[0][2]}-{max(row[2] for row in rows)}')

    for failure in failures:
        print(f'  FAIL  {failure}')
    print('  everything the card claims holds' if not failures
          else f'  {len(failures)} claim(s) do not hold')
    return 1 if failures else 0


def tabseparated(rows: list[tuple]) -> str:
    """TabSeparated, with `\\N` for the absent postcode — ClickHouse's NULL."""
    def field(value: object) -> str:
        if value is None:
            return '\\N'
        if isinstance(value, int):
            return str(value)
        return str(value).replace('\\', '\\\\').replace('\t', '\\t').replace('\n', '\\n')

    return ''.join('\t'.join(field(value) for value in row) + '\n' for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', help='write TabSeparated rows here')
    parser.add_argument('--self-test', action='store_true')
    arguments = parser.parse_args()

    if arguments.self_test:
        return selftest()

    rows = build()
    text = tabseparated(rows)

    if not arguments.out:
        parser.error('one of --out or --self-test')

    path = pathlib.Path(arguments.out)
    path.write_text(text, encoding='utf-8')
    print(f'wrote {path}  {len(rows):,} rows  sha256 '
          f'{hashlib.sha256(text.encode("utf-8")).hexdigest()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
