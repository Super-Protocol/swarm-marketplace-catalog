#!/usr/bin/env python3
"""Does this URL answer 200? Exit 0 if so.

One probe for every service `confidential-rag.sh` raises. It is here rather than
inline because three of those images carry curl, two carry a Python interpreter
and none carries both — so the probe runs on the host, against published ports,
and the question of what tooling is inside a slim image never comes up.
"""

import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=5) as answer:
        sys.exit(0 if answer.status == 200 else 1)
except Exception:
    sys.exit(1)
