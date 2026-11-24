#!/usr/bin/env python3
"""Where a card is present, and what it is presented to.

Real US state codes, real cities and a real ZIP prefix for each — because the
shipped checkpoint's vocabulary enumerates states and 3-digit ZIP prefixes, and
an invented geography tokenises to `STATE_XX` and a ZIP bucket nobody trained
on. The merchants are invented; the places they stand in are not.
"""

from __future__ import annotations

# (state, city, a real 5-digit ZIP in that city)
PLACES: list[tuple[str, str, str]] = [
    ('AL', 'Birmingham', '35203'), ('AK', 'Anchorage', '99501'),
    ('AZ', 'Phoenix', '85004'), ('AZ', 'Tucson', '85701'),
    ('AR', 'Little Rock', '72201'),
    ('CA', 'Los Angeles', '90001'), ('CA', 'San Francisco', '94102'),
    ('CA', 'San Diego', '92101'), ('CA', 'Sacramento', '95814'),
    ('CO', 'Denver', '80202'), ('CT', 'Hartford', '06103'),
    ('DC', 'Washington', '20001'), ('DE', 'Wilmington', '19801'),
    ('FL', 'Miami', '33101'), ('FL', 'Orlando', '32801'),
    ('FL', 'Tampa', '33602'),
    ('GA', 'Atlanta', '30303'), ('HI', 'Honolulu', '96813'),
    ('IA', 'Des Moines', '50309'), ('ID', 'Boise', '83702'),
    ('IL', 'Chicago', '60601'), ('IL', 'Springfield', '62701'),
    ('IN', 'Indianapolis', '46204'), ('KS', 'Wichita', '67202'),
    ('KY', 'Louisville', '40202'), ('LA', 'New Orleans', '70112'),
    ('MA', 'Boston', '02108'), ('MD', 'Baltimore', '21201'),
    ('ME', 'Portland', '04101'), ('MI', 'Detroit', '48226'),
    ('MN', 'Minneapolis', '55401'), ('MO', 'St. Louis', '63101'),
    ('MO', 'Kansas City', '64106'), ('MS', 'Jackson', '39201'),
    ('MT', 'Billings', '59101'), ('NC', 'Charlotte', '28202'),
    ('NC', 'Raleigh', '27601'), ('ND', 'Fargo', '58102'),
    ('NE', 'Omaha', '68102'), ('NH', 'Manchester', '03101'),
    ('NJ', 'Newark', '07102'), ('NM', 'Albuquerque', '87102'),
    ('NV', 'Las Vegas', '89101'), ('NY', 'New York', '10001'),
    ('NY', 'Brooklyn', '11201'), ('NY', 'Buffalo', '14202'),
    ('OH', 'Cleveland', '44114'), ('OH', 'Columbus', '43215'),
    ('OK', 'Oklahoma City', '73102'), ('OR', 'Portland', '97204'),
    ('PA', 'Philadelphia', '19103'), ('PA', 'Pittsburgh', '15222'),
    ('RI', 'Providence', '02903'), ('SC', 'Columbia', '29201'),
    ('SD', 'Sioux Falls', '57104'), ('TN', 'Nashville', '37201'),
    ('TN', 'Memphis', '38103'), ('TX', 'Dallas', '75201'),
    ('TX', 'Houston', '77002'), ('TX', 'Austin', '78701'),
    ('TX', 'San Antonio', '78205'), ('UT', 'Salt Lake City', '84101'),
    ('VA', 'Richmond', '23219'), ('VT', 'Burlington', '05401'),
    ('WA', 'Seattle', '98101'), ('WI', 'Milwaukee', '53202'),
    ('WV', 'Charleston', '25301'), ('WY', 'Cheyenne', '82001'),
]

# A card used abroad. Ontario and British Columbia are not in the checkpoint's
# state vocabulary, so these rows tokenise to `STATE_XX` — which is the point of
# carrying them: the coverage report should have something true to say.
FOREIGN: list[tuple[str, str, str]] = [
    ('ON', 'Toronto', 'M5H'), ('BC', 'Vancouver', 'V6B'),
    ('QC', 'Montreal', 'H3B'),
]

# MCC -> (category, merchant-name pool). Every code is in the checkpoint's
# `KNOWN_MCCS`; the merchants are invented, which is harmless because the
# tokenizer hashes merchant names into 2,000 buckets and every name hits one.
CATEGORIES: list[tuple[int, str, list[str]]] = [
    (5411, 'grocery', ['Harbor Market', 'Greenfield Grocers', 'Cedar Street Foods', 'Blue Line Market']),
    (5541, 'fuel', ['Milepost Fuel', 'Northway Service', 'Redstone Petrol']),
    (5812, 'restaurant', ['The Copper Kettle', 'Ninth Avenue Grill', 'Saltbox Kitchen', 'Rivergate Bistro']),
    (5814, 'fast food', ['Quickfire Burgers', 'Noodle Cart', 'Two Birds Chicken']),
    (5912, 'pharmacy', ['Alder Pharmacy', 'Wellspring Drug', 'Corner Chemist']),
    (5311, 'department store', ['Marbury & Co', 'Lakeside Department Store']),
    (5300, 'wholesale club', ['Bulkhouse Club', 'Warehouse Nine']),
    (5651, 'clothing', ['Fenwick Clothiers', 'Stitchline Apparel', 'Union Dry Goods']),
    (5661, 'shoes', ['Carrow Shoes', 'Bootmakers Row']),
    (5732, 'electronics', ['Voltway Electronics', 'Pixel & Wire']),
    (5045, 'computers', ['Northgate Computing', 'Terminal Supply']),
    (5712, 'furniture', ['Ashgrove Furnishings', 'Longbench Interiors']),
    (5722, 'appliances', ['Hearth Appliance', 'Kettering Home']),
    (5719, 'home goods', ['Thistle Home', 'Linen & Larder']),
    (5941, 'sporting goods', ['Summit Outfitters', 'Paddle & Pedal']),
    (5942, 'books', ['Margin Books', 'The Quiet Shelf']),
    (5947, 'gifts', ['Paper Lantern Gifts', 'The Small Parcel']),
    (5977, 'cosmetics', ['Vellum Beauty', 'Rosewater Co']),
    (5094, 'jewellery', ['Thorne & Bell', 'Quicksilver Jewellers']),
    (5921, 'off-licence', ['Barrelhouse Wines', 'Cask & Crate']),
    (5813, 'bar', ['The Lamplighter', 'Dockside Tap']),
    (5499, 'convenience', ['Latchkey Convenience', 'Nightowl Mart']),
    (5533, 'auto parts', ['Axle & Co Parts', 'Camshaft Supply']),
    (7531, 'auto body', ['Fender Lane Bodyworks']),
    (7538, 'auto service', ['Tollgate Motors Service', 'Pitstop Mechanics']),
    (7542, 'car wash', ['Bright Lane Car Wash']),
    (7549, 'towing', ['Hookline Recovery']),
    (4111, 'transit', ['Metro Transit Authority', 'Harbor Ferry']),
    (4121, 'taxi', ['Amber Cab', 'Citywide Rides']),
    (4131, 'coach', ['Longhaul Coaches']),
    (4784, 'tolls', ['Turnpike Authority']),
    (4511, 'airline', ['Meridian Air', 'Northbeam Airways']),
    (3000, 'airline', ['Meridian Air']),
    (3058, 'airline', ['Northbeam Airways']),
    (7011, 'hotel', ['The Grainhouse Hotel', 'Parkway Inn', 'Hotel Verity']),
    (3504, 'hotel', ['The Grainhouse Hotel']),
    (3387, 'car hire', ['Clearway Rentals']),
    (4722, 'travel agency', ['Compass Point Travel']),
    (4411, 'cruise', ['Bluewater Cruises']),
    (4814, 'telecom', ['Larkfield Telecom', 'Signalworks Mobile']),
    (4899, 'media', ['Brightwire Media']),
    (4900, 'utilities', ['Cascade Power & Light', 'Riverbend Water']),
    (6300, 'insurance', ['Hollis Insurance Group']),
    (8011, 'doctor', ['Pinehurst Family Practice']),
    (8021, 'dentist', ['Oakfield Dental']),
    (8043, 'optician', ['Clearsight Opticians']),
    (8062, 'hospital', ['St Albert Hospital']),
    (8099, 'medical', ['Kingsway Diagnostics']),
    (8111, 'legal', ['Harlow & Pike']),
    (8931, 'accounting', ['Ledgerline Accounting']),
    (7276, 'tax preparation', ['Aprilstone Tax']),
    (7230, 'salon', ['The Barber Room', 'Juniper Salon']),
    (7210, 'laundry', ['Spincycle Laundry']),
    (7349, 'cleaning', ['Brightwork Cleaning']),
    (7832, 'cinema', ['The Roxy Cinema', 'Palace Picture House']),
    (7922, 'theatre', ['Granary Theatre']),
    (7995, 'betting', ['Fairfield Wagers']),
    (5815, 'digital media', ['Streamline Media']),
    (5816, 'digital games', ['Hexfield Games']),
    (5192, 'periodicals', ['The Daily Register']),
    (5193, 'florist', ['Marigold Flowers']),
    (5211, 'builders merchant', ['Yardline Timber']),
    (5251, 'hardware', ['Kesterton Hardware']),
    (5261, 'garden centre', ['Wintergreen Nursery']),
    (9402, 'postal', ['Postal Service']),
    (1711, 'heating and plumbing', ['Fairweather Heating']),
    (4829, 'money transfer', ['Sendline Transfers']),
]

# Codes the checkpoint's vocabulary does NOT carry: real MCCs, outside
# `KNOWN_MCCS`. A small share of the table uses them so the coverage report has
# something to report other than 100%.
UNKNOWN_CATEGORIES: list[tuple[int, str, list[str]]] = [
    (7372, 'software', ['Keystone Software']),
    (5944, 'jeweller', ['Ardent & Finch']),
    (8398, 'charity', ['Riverlands Trust']),
    (5968, 'subscription', ['Monthly Box Co']),
]
