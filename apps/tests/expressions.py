#!/usr/bin/env python3
"""Every `{{ … }}` in a definition is an expression the marketplace will parse.

    apps/tests/expressions.py apps/*/app.yaml

This is the check that was missing when three listings passed `apps/tests/run.sh`
and were then refused by the stand's publish parse (SUP-230). The cause: one
listing carried a model's Jinja chat template inline in
`deployment.values.base`, and the marketplace does not care that the braces were
meant for a different template language — it scans every string in the
expression-bearing positions and parses whatever is between `{{` and `}}` with
its own grammar. `{{ bos_token }}` is not a namespace, `raise_exception(…)` is
not a reference, and the publish failed with six issues on a definition CI had
called green.

The JSON Schema cannot see this: the braces sit inside a perfectly valid string.
So the schema pass was never the whole contract, and this file is the rest of it.

It is a port, not an approximation. The grammar is tiny — a reference is a dotted
path optionally piped through the single filter `json`, and a condition compares
two operands or joins two conditions — so `parseReference`, `parseOperand` and
`parseCondition` are reproduced here from
`libs/marketplace-spec/src/expressions/parser.ts` in swarm-cloud, and the
positions and their allowed namespaces from `validation/lint-app.ts` and
`expressions/scope.ts`. Specification §7.1 and §7.3 are the published form of the
same thing, at the revision `run.sh` pins.

Importing the real validator would be better still, but it lives in a private
TypeScript repository this one does not depend on. If that ever becomes a
published package, replace this file with a call to it.

Verified against it rather than assumed equivalent: run
`lintAppDefinition` from swarm-cloud's built `libs/marketplace-spec` over this
repository's listings plus the definition that failed the stand, and the two
agree on all eleven — ten clean, one with the same six issues in the same order
and the same wording. Worth redoing if this file is ever edited:

    node -e "import('<swarm-cloud>/libs/marketplace-spec/dist/index.js').then(async m => {
      const { readFileSync } = await import('node:fs');
      const { load } = await import('js-yaml');
      for (const p of process.argv.slice(1))
        console.log(p, m.lintAppDefinition(load(readFileSync(p,'utf8'))));
    })" apps/*/app.yaml

Note that `lintAppDefinition` *returns* its issues rather than throwing — a
harness that only catches exceptions will call everything green, which is its own
small version of the bug this file exists for.
"""

from __future__ import annotations

import re
import sys

import yaml

# §7.1. The seven namespaces, in the order the real error message lists them.
NAMESPACES = ["params", "components", "namespace", "data", "secrets", "binding", "consumer"]

# §7.3, by position. `binding` is in none of them: it exists only inside
# provisioner environments, which these positions are not.
SCOPES = {
    "values.base": {"params", "components", "namespace", "data", "secrets", "consumer"},
    "patch.value": {"params", "components", "namespace", "data", "secrets", "consumer"},
    "patch.when": {"params", "data", "consumer"},
    "ui.visible": {"params"},
    "output.value": {"params", "components", "namespace", "data", "consumer"},
    "output.when": {"params", "data", "consumer"},
}

INTERPOLATION = re.compile(r"\{\{(.*?)\}\}", re.S)
REFERENCE_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_-]+)*$")
COMPARISON_OPERATORS = ["==", "!=", ">=", "<=", ">", "<"]
NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


class Invalid(Exception):
    pass


def parse_reference(source: str, scope: set[str]) -> None:
    trimmed = source.strip()
    parts = [part.strip() for part in trimmed.split("|")]
    raw, filters = parts[0], parts[1:]

    if len(filters) > 1:
        raise Invalid(f'"{trimmed}" chains multiple filters; only a single "| json" is supported')
    if len(filters) == 1 and filters[0] != "json":
        raise Invalid(f'"{filters[0]}" is not a known filter; the only filter is "json"')
    if not raw or not REFERENCE_PATTERN.match(raw):
        raise Invalid(f'"{trimmed}" is not a valid reference')

    segments = raw.split(".")
    namespace = segments[0]
    if namespace not in NAMESPACES:
        raise Invalid(f'"{namespace}" is not a known namespace ({", ".join(NAMESPACES)})')
    if namespace == "namespace" and len(segments) > 1:
        raise Invalid('"namespace" is a scalar and has no properties')
    if namespace not in scope:
        raise Invalid(f'"{namespace}" is not allowed here; §7.3 permits '
                      f'{", ".join(sorted(scope))}')


def parse_operand(source: str, scope: set[str]) -> None:
    trimmed = source.strip()
    if trimmed in ("true", "false") or NUMBER.match(trimmed):
        return
    if (trimmed.startswith('"') and trimmed.endswith('"') and len(trimmed) >= 2) or (
        trimmed.startswith("'") and trimmed.endswith("'") and len(trimmed) >= 2
    ):
        return
    if "|" in trimmed:
        raise Invalid('the "| json" filter is only allowed in interpolation, not in conditions')
    parse_reference(trimmed, scope)


def split_logical(source: str):
    for operator in ("||", "&&"):
        index = source.find(operator)
        if index >= 0:
            return operator, source[:index], source[index + len(operator):]
    return None


def parse_condition(source: str, scope: set[str]) -> None:
    trimmed = source.strip()
    if not trimmed:
        raise Invalid("condition is empty")

    split = split_logical(trimmed)
    if split:
        operator, left, right = split
        if not left.strip() or not right.strip():
            raise Invalid(f'"{trimmed}" is missing an operand around "{operator}"')
        parse_condition(left, scope)
        parse_condition(right, scope)
        return

    for operator in COMPARISON_OPERATORS:
        index = trimmed.find(operator)
        if index < 0:
            continue
        left, right = trimmed[:index], trimmed[index + len(operator):]
        if not left.strip() or not right.strip():
            raise Invalid(f'"{trimmed}" is missing an operand around "{operator}"')
        parse_operand(left, scope)
        parse_operand(right, scope)
        return

    raise Invalid(f'"{trimmed}" is not a comparison; conditions must compare two operands '
                  f"(== != > >= < <=)")


def walk_strings(node, pointer=""):
    """Every string in a tree, with a pointer — what the real validator walks."""
    if isinstance(node, str):
        yield node, pointer
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from walk_strings(item, f"{pointer}[{index}]")
    elif isinstance(node, dict):
        for key, child in node.items():
            yield from walk_strings(child, f"{pointer}.{key}" if pointer else key)


def check_interpolations(value: str, context: str, pointer: str, report) -> None:
    for match in INTERPOLATION.finditer(value):
        inner = match.group(1)
        try:
            parse_reference(inner, SCOPES[context])
        except Invalid as error:
            report(f"{pointer}: {error}")


def check_condition(value, context: str, pointer: str, report) -> None:
    if not value:
        return
    try:
        parse_condition(str(value), SCOPES[context])
    except Invalid as error:
        report(f"{pointer}: {error}")


def check_definition(path: str) -> list[str]:
    definition = yaml.safe_load(open(path))
    problems: list[str] = []
    report = problems.append

    for section in definition.get("ui", {}).get("sections", []) or []:
        for field in section.get("fields", []) or []:
            check_condition(field.get("visible"), "ui.visible",
                            f"ui.{section.get('id')}.{field.get('parameterId')}.visible", report)

    for component in definition.get("components", []) or []:
        name = component.get("name")
        values = (component.get("deployment") or {}).get("values") or {}
        for value, pointer in walk_strings(values.get("base") or {}):
            check_interpolations(value, "values.base",
                                 f"components.{name}.values.base.{pointer}", report)
        for index, patch in enumerate(values.get("patches") or []):
            where = f"components.{name}.patches[{index}]"
            check_condition(patch.get("when"), "patch.when", f"{where}.when", report)
            for value, pointer in walk_strings(patch.get("value")):
                check_interpolations(value, "patch.value",
                                     f"{where}.value.{pointer}".rstrip("."), report)

    for output in definition.get("outputs", []) or []:
        where = f"outputs.{output.get('id')}"
        check_interpolations(output.get("value") or "", "output.value", f"{where}.value", report)
        check_condition(output.get("when"), "output.when", f"{where}.when", report)

    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    failed = False
    for path in argv[1:]:
        problems = check_definition(path)
        if problems:
            failed = True
            print(f"  FAIL  {path}")
            for problem in problems[:12]:
                print(f"          {problem}")
            if len(problems) > 12:
                print(f"          … and {len(problems) - 12} more")
        else:
            print(f"  ok    {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
