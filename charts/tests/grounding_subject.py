#!/usr/bin/env python3
"""The grounding Job acts as the installation administrator, by name.

`identity()` used to take `organizations/<tenant>/members[0]` — whoever Keycloak
listed first. `main()` calls `ensure_model` before `ensure_sources`, every
model-registry operation on the agent is
`@RequirePermission('installation.llm-models.*')`, and an ordinary member of the
organisation does not hold an installation permission. So the moment a tenant
gained a second member who sorted ahead of the administrator, the Job's first
registry call was refused, on every restart until `backoffLimit` was spent, and
it never reached the data sources.

From outside that is invisible: perfect evidence, every workload ready, and none
of the mounted datasets registered — with no Job status the platform will report
for a published space. It cost the Latica stand two windows (SUP-909).

So the case this covers is the one that was broken: **two members, with the
administrator second.** A test with one member, or with the administrator first,
passes against the old code and proves nothing.
"""
from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GROUND = os.path.join(ROOT, 'charts', 'conversational-analyst-grounding', 'files', 'ground.py')

ADMIN = 'gate-rehearsal@local.test'
OTHER = 'cypress-bay-admin@local.test'

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(f'  {"ok  " if ok else "FAIL"}  {what}')
    if not ok:
        failures.append(what)


def load(admin_username: str):
    """Import ground.py with the environment it reads at module level."""
    os.environ.update({
        'API_URL': 'http://api', 'KEYCLOAK_URL': 'http://kc', 'KEYCLOAK_REALM': 'master',
        'KEYCLOAK_CLIENT_ID': 'analyst', 'KEYCLOAK_CLIENT_SECRET': 'x',
        'TENANT_ALIAS': 'cypress-bay', 'ADMIN_USERNAME': admin_username,
        'MODEL_NAME': 'm', 'MODEL_ID': 'm', 'MODEL_BASE_URL': 'http://m', 'MODEL_API_KEY': 'k',
        'LARGE_DATA_ROW_THRESHOLD': '100', 'LARGE_DATA_SIZE_THRESHOLD': '1', 'PREVIEW_ROW_COUNT': '1',
        'CONTEXT_WINDOW_SIZE': '1', 'MEMORY_TOKEN_BUDGET': '1', 'SUMMARIZE_TOKEN_THRESHOLD': '1',
        'BINDINGS': '[]',
    })
    spec = importlib.util.spec_from_file_location('ground_under_test', GROUND)
    module = importlib.util.module_from_spec(spec)
    sys.modules['ground_under_test'] = module
    spec.loader.exec_module(module)
    return module


def with_members(module, members):
    """Stand in for Keycloak: one organisation, and the members it returns."""
    module.kc_token = lambda: 'token'

    def kc_get(path, _token):
        if path == 'organizations':
            return [{'id': 'org-1', 'alias': 'cypress-bay'}, {'id': 'org-2', 'alias': 'latica'}]
        if path == 'organizations/org-1/members':
            return members
        raise AssertionError(f'unexpected read: {path}')

    module.kc_get = kc_get


print('the administrator is selected by name, not by position')
module = load(ADMIN)
# The order that broke the stand: the other member first.
with_members(module, [
    {'id': 'other-id', 'username': OTHER},
    {'id': 'admin-id', 'username': ADMIN},
])
tenant, subject, username = module.identity()
check(tenant == 'org-1', f'the tenant is the organisation whose alias was named ({tenant})')
check(subject == 'admin-id', f'the subject is the administrator and not members[0] ({username})')

print('\nthe administrator first still works')
with_members(module, [
    {'id': 'admin-id', 'username': ADMIN},
    {'id': 'other-id', 'username': OTHER},
])
_, subject, _ = module.identity()
check(subject == 'admin-id', 'unchanged when the administrator happens to sort first')

print('\nan administrator who is not a member is fatal, not substituted')
with_members(module, [{'id': 'other-id', 'username': OTHER}])
try:
    module.identity()
    check(False, 'identity() refuses when the administrator is absent')
except RuntimeError as error:
    message = str(error)
    check(ADMIN in message and OTHER in message,
          'the refusal names the administrator it wanted and the members it found')
    check('installation' in message,
          'the refusal says why it matters rather than only that it failed')

print('\nan empty organisation is still refused for its own reason')
with_members(module, [])
try:
    module.identity()
    check(False, 'identity() refuses an organisation with no members')
except RuntimeError as error:
    check('has no member yet' in str(error), 'the empty case keeps its own message')

print()
if failures:
    print(f'{len(failures)} check(s) failed')
    raise SystemExit(1)
print('everything checked out')
