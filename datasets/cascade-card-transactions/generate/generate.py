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

    rows = build()
    text = tabseparated(rows)

    if arguments.self_test:
        print(f'  transactions {len(rows):,} rows  sha256 '
              f'{hashlib.sha256(text.encode("utf-8")).hexdigest()}')
        print(f'  columns      {", ".join(COLUMNS)}')
        fraud = sum(1 for row in rows if row[13] == 'Yes')
        online = sum(1 for row in rows if row[7] == 'Online Transaction')
        unknown = {mcc for _, _, mcc_names in [(0, 0, UNKNOWN_CATEGORIES)] for mcc, _, _ in mcc_names}
        foreign = {state for state, _, _ in FOREIGN}
        print(f'  cardholders  {len({row[0] for row in rows})}, '
              f'cards per holder up to {max(row[1] for row in rows) + 1}')
        print(f'  period       {rows[0][2]} to {max(row[2] for row in rows)}')
        print(f'  fraud        {fraud:,} rows ({fraud / len(rows) * 100:.3f}%)')
        print('  vocabulary coverage this table should report:')
        print(f'    mcc        {1 - sum(1 for row in rows if row[12] in unknown) / len(rows):.4f}')
        print(f'    state      {1 - sum(1 for row in rows if row[10] in foreign) / len(rows):.4f}')
        print('    entrymode  1.0000   user 1.0000   card 1.0000')
        print(f'    zip3       {1 - online / len(rows):.4f}  '
              f'({online:,} card-not-present rows carry no postcode)')
        return 0

    if not arguments.out:
        parser.error('one of --out or --self-test')

    path = pathlib.Path(arguments.out)
    path.write_text(text, encoding='utf-8')
    print(f'wrote {path}  {len(rows):,} rows  sha256 '
          f'{hashlib.sha256(text.encode("utf-8")).hexdigest()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
