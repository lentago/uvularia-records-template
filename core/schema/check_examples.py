#!/usr/bin/env python3
"""Validate every example in this directory against its schema, with no third-party
libraries. Each `<name>.good.json` must validate against `<name>.schema.json`, as
must any named variant `<name>.<variant>.good.json`; each `<name>.bad.json` must
FAIL (an expected failure, which proves the check can fail).

This carries a small validator for the subset of JSON Schema draft 2020-12 the
uvularia schemas use: type (incl. unions and null), enum, const, pattern, minLength,
maxLength, minimum, maximum, minItems, maxItems, properties, required,
additionalProperties (boolean), items, allOf, anyOf, oneOf, not, if/then/else, and
local $ref ("#/$defs/..."). Nothing here needs installing; Python 3.12 stdlib only.

Run from anywhere:  python3 core/schema/check_examples.py
Exit status is non-zero if any example does not behave as its name promises.
"""

import json
import re
import sys
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = SCHEMA_DIR / "examples"


# --------------------------------------------------------------------------- #
# The validator.                                                              #
# --------------------------------------------------------------------------- #

def _type_ok(instance, t):
    if t == "object":
        return isinstance(instance, dict)
    if t == "array":
        return isinstance(instance, list)
    if t == "string":
        return isinstance(instance, str)
    if t == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if t == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if t == "boolean":
        return isinstance(instance, bool)
    if t == "null":
        return instance is None
    raise ValueError(f"unknown type keyword in schema: {t!r}")


def _resolve_ref(ref, root):
    if not ref.startswith("#/"):
        raise ValueError(f"only local refs are supported, got {ref!r}")
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def validate(schema, instance, root, path="$"):
    """Return a list of human-readable error strings; empty means valid."""
    errors = []

    if schema is True:
        return errors
    if schema is False:
        return [f"{path}: schema forbids any value here"]

    if "$ref" in schema:
        errors += validate(_resolve_ref(schema["$ref"], root), instance, root, path)
        rest = {k: v for k, v in schema.items() if k != "$ref"}
        if rest:
            errors += validate(rest, instance, root, path)
        return errors

    if "type" in schema:
        types = schema["type"]
        if isinstance(types, str):
            types = [types]
        if not any(_type_ok(instance, t) for t in types):
            got = "null" if instance is None else type(instance).__name__
            return [f"{path}: expected type {schema['type']}, got {got}"]

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']}")

    if isinstance(instance, str):
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: {instance!r} does not match pattern {schema['pattern']!r}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} is below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} is above maximum {schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
        if "items" in schema:
            for i, element in enumerate(instance):
                errors += validate(schema["items"], element, root, f"{path}[{i}]")

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required property '{name}'")
        props = schema.get("properties", {})
        for name, subschema in props.items():
            if name in instance:
                errors += validate(subschema, instance[name], root, f"{path}.{name}")
        if schema.get("additionalProperties") is False:
            for name in instance:
                if name not in props:
                    errors.append(f"{path}: property '{name}' is not allowed")

    if "allOf" in schema:
        for sub in schema["allOf"]:
            errors += validate(sub, instance, root, path)
    if "anyOf" in schema:
        if all(validate(sub, instance, root, path) for sub in schema["anyOf"]):
            errors.append(f"{path}: does not match any of the anyOf branches")
    if "oneOf" in schema:
        matched = sum(1 for sub in schema["oneOf"] if not validate(sub, instance, root, path))
        if matched != 1:
            errors.append(f"{path}: matched {matched} of the oneOf branches, exactly 1 required")
    if "not" in schema and not validate(schema["not"], instance, root, path):
        errors.append(f"{path}: must not match the 'not' schema")

    if "if" in schema:
        if not validate(schema["if"], instance, root, path):
            if "then" in schema:
                errors += validate(schema["then"], instance, root, path)
        elif "else" in schema:
            errors += validate(schema["else"], instance, root, path)

    return errors


# --------------------------------------------------------------------------- #
# The harness.                                                                #
# --------------------------------------------------------------------------- #

def _load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main():
    schemas = {}
    for schema_path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        name = schema_path.name[: -len(".schema.json")]
        schemas[name] = _load(schema_path)

    if not schemas:
        print("no schemas found", file=sys.stderr)
        return 1

    failures = 0
    checked = 0

    for name in sorted(schemas):
        schema = schemas[name]

        good = EXAMPLE_DIR / f"{name}.good.json"
        if not good.exists():
            failures += 1
            print(f"FAIL  missing good example for schema '{name}'")
        # Optional named variants, <name>.<variant>.good.json — e.g. the previous
        # release's format, which must keep validating (see README.md).
        goods = ([good] if good.exists() else []) + sorted(EXAMPLE_DIR.glob(f"{name}.*.good.json"))
        for path in goods:
            checked += 1
            errors = validate(schema, _load(path), schema)
            if errors:
                failures += 1
                print(f"FAIL  {path.name}: expected to validate, but:")
                for err in errors:
                    print(f"        - {err}")
            else:
                print(f"ok    {path.name} validates")

        bad = EXAMPLE_DIR / f"{name}.bad.json"
        if bad.exists():
            checked += 1
            why_path = EXAMPLE_DIR / f"{name}.bad.why"
            why = why_path.read_text(encoding="utf-8").strip() if why_path.exists() else "(no .why file)"
            errors = validate(schema, _load(bad), schema)
            if errors:
                print(f"ok    {bad.name} fails as expected — {why}")
            else:
                failures += 1
                print(f"FAIL  {bad.name}: expected to FAIL validation, but it passed")
        else:
            failures += 1
            print(f"FAIL  missing bad example for schema '{name}'")

    print()
    if failures:
        print(f"{failures} problem(s) across {checked} example(s).")
        return 1
    print(f"all {checked} example(s) behaved as named.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
