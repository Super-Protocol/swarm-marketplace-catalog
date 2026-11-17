#!/usr/bin/env python3
"""
Mail configuration moves nothing a deployment attests (SUP-269).

Renders one case several times — with no mail at all, with SMTP, with SMTP under a
different host, sender and credentials, and with Resend — and requires every attested
field to be identical across all of them. Two things may differ: a Secret's `/data`,
which the platform drops from the snapshot, and the one pod-template hash that exists
so a reconfigure restarts the API (`checksum/mail`) — and that one only while the object
declares it excluded. Nothing else may move, excluded or not.

`evidence_drift.py` asks the hostname version of this question and tolerates declared
exclusions. This one is stricter on purpose: there is no reason for any part of a mail
setup to reach an attested object at all, so the only acceptable answer is "no
difference".

It also checks the other half of the promise: the SMTP password and the Resend key reach
the Secret, and appear in no other rendered object.

    charts/tests/mail_invariance.py charts/tests/cases/api-external-only.yaml
"""

import subprocess
import sys

import yaml
from evidence_drift import PLATFORM_STRIPPED, SECRET_STRIPPED, covers, declared, differing_pointers

CHART = "confidential-router-api"
# The restart hash. It varies with the mail settings by design, so it is the one
# field allowed to — provided the object excludes it from the snapshot.
MAIL_CHECKSUM = "/spec/template/metadata/annotations/checksum~1mail"

SMTP_PASSWORD = "golden-smtp-password-not-a-real-one"
RESEND_KEY = "re_golden_mail_not_a_real_key"

SHAPES = {
    "no mail": [],
    "smtp": [
        "mail.provider=smtp",
        "mail.from=no-reply@mail-a.example",
        "mail.fromName=Router A",
        "mail.smtp.host=smtp.mail-a.example",
        "mail.smtp.port=587",
        "mail.smtp.security=starttls",
        "mail.smtp.user=mailer@mail-a.example",
        f"mail.smtp.password={SMTP_PASSWORD}",
    ],
    "smtp elsewhere": [
        "mail.provider=smtp",
        "mail.from=hello@mail-b.example",
        "mail.smtp.host=mx.mail-b.example",
        "mail.smtp.port=465",
        "mail.smtp.security=tls",
        "mail.smtp.user=other@mail-b.example",
        "mail.smtp.password=a-different-password",
    ],
    "resend": [
        "mail.provider=resend",
        "mail.from=no-reply@mail-c.example",
        f"mail.resendApiKey={RESEND_KEY}",
    ],
}


def render(values: str, overrides: list[str]) -> tuple[str, dict]:
    manifests = subprocess.run(
        ["helm", "template", "cr", f"charts/{CHART}", "--namespace", "confidential-router", "--values", values]
        + [arg for override in overrides for arg in ("--set-string", override)],
        capture_output=True, text=True, check=True,
    ).stdout
    documents = {}
    for document in yaml.safe_load_all(manifests):
        if document:
            documents[(document["kind"], document["metadata"]["name"])] = document
    return manifests, documents


def main(values: str) -> int:
    failures = 0
    _, baseline = render(values, SHAPES["no mail"])

    for shape, overrides in SHAPES.items():
        if not overrides:
            continue
        manifests, rendered = render(values, overrides)

        for key in sorted(set(baseline) | set(rendered)):
            kind, name = key
            here, there = baseline.get(key), rendered.get(key)
            if here is None or there is None:
                print(f"  FAIL  {kind}/{name} is rendered with {'no mail' if there is None else shape} only")
                failures += 1
                continue
            stripped = PLATFORM_STRIPPED + (SECRET_STRIPPED if kind == "Secret" else ())
            for pointer in differing_pointers(here, there):
                if pointer == MAIL_CHECKSUM and MAIL_CHECKSUM in declared(there):
                    continue
                if not any(covers(prefix, pointer) for prefix in stripped):
                    print(f"  FAIL  {kind}/{name}{pointer} differs between no mail and {shape}")
                    failures += 1

        # Every credential in the Secret, and in nothing else.
        for credential in (SMTP_PASSWORD, RESEND_KEY):
            if credential not in " ".join(overrides):
                continue
            holders = [
                f"{kind}/{name}"
                for (kind, name), document in rendered.items()
                if credential in yaml.safe_dump(document)
            ]
            if not holders or any(not holder.startswith("Secret/") for holder in holders):
                print(f"  FAIL  {shape}: the credential is in {holders or 'nothing'}, expected a Secret alone")
                failures += 1
        if failures == 0:
            print(f"  ok    {shape}: every attested field identical to a deployment with no mail")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
