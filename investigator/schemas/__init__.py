"""JSON schema loading and a minimal, dependency-free validator for the run.py contracts.

The schemas in this folder are the contracts (input, output, plan, events). The validator supports the subset
they use: type (incl. lists), required, properties, additionalProperties, enum, const, minLength, maxLength,
minimum, maximum, pattern, items, minItems, maxItems, anyOf, oneOf (treated as anyOf) and local $ref into $defs.
It returns a list of human-readable error strings (empty = valid). Tests may use jsonschema when available,
but the runtime never depends on it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent
_TYPE_MAP = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool,
    "object": dict, "array": list, "null": type(None),
}


def load_schema(name: str) -> dict[str, Any]:
    """Load <name>.schema.json from this folder (name without the suffix, e.g. "input")."""
    path = SCHEMA_DIR / f"{name}.schema.json" if not name.endswith(".json") else SCHEMA_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def _is_type(value: Any, type_name: str) -> bool:
    expected = _TYPE_MAP.get(type_name)
    if expected is None:
        return True
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    return isinstance(value, expected)


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"only local refs are supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def validate(instance: Any, schema: dict[str, Any], path: str = "$", root: dict[str, Any] | None = None) -> list[str]:
    """Return a list of error strings; empty when the instance is valid."""
    root = root if root is not None else schema
    errors: list[str] = []
    if "$ref" in schema:
        merged = dict(_resolve_ref(schema["$ref"], root))
        merged.update({k: v for k, v in schema.items() if k != "$ref"})
        schema = merged

    types = schema.get("type")
    if types is not None:
        type_list = types if isinstance(types, list) else [types]
        if not any(_is_type(instance, t) for t in type_list):
            errors.append(f"{path}: expected type {'/'.join(type_list)}, got {type(instance).__name__}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in {schema['enum']}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match {schema['pattern']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required key {key!r}")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in instance:
                errors.extend(validate(instance[key], sub, f"{path}.{key}", root))
        extra = schema.get("additionalProperties", True)
        if extra is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: unexpected key {key!r}")
        elif isinstance(extra, dict):
            for key in instance:
                if key not in props:
                    errors.extend(validate(instance[key], extra, f"{path}.{key}", root))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                errors.extend(validate(item, items, f"{path}[{i}]", root))

    for keyword in ("anyOf", "oneOf"):
        options = schema.get(keyword)
        if options:
            branch_errors = [validate(instance, opt, path, root) for opt in options]
            if not any(len(b) == 0 for b in branch_errors):
                flat = "; ".join(b[0] for b in branch_errors if b)
                errors.append(f"{path}: no {keyword} branch matched ({flat})")
    return errors
