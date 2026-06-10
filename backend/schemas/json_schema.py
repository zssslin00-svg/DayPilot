from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Mapping


class JsonSchemaValidationError(ValueError):
    """Raised when a value does not match the supported JSON Schema subset."""


def validate_json_schema(value: Any, schema: Mapping[str, Any]) -> None:
    """Validate a value against the JSON Schema subset used by DayPilot resources."""

    _validate(value, schema, "$")


def _validate(value: Any, schema: Mapping[str, Any], path: str) -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        _validate_type(value, str(expected_type), path)

    if "const" in schema and value != schema["const"]:
        raise JsonSchemaValidationError(f"{path} must equal {schema['const']!r}.")

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(item) for item in schema["enum"])
        raise JsonSchemaValidationError(f"{path} must be one of: {allowed}.")

    if isinstance(value, str):
        _validate_string(value, schema, path)
    elif isinstance(value, int) and not isinstance(value, bool):
        _validate_number(value, schema, path)
    elif isinstance(value, list):
        _validate_array(value, schema, path)
    elif isinstance(value, dict):
        _validate_object(value, schema, path)


def _validate_type(value: Any, expected_type: str, path: str) -> None:
    if expected_type == "object" and isinstance(value, dict):
        return
    if expected_type == "array" and isinstance(value, list):
        return
    if expected_type == "string" and isinstance(value, str):
        return
    if expected_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
        return
    raise JsonSchemaValidationError(f"{path} must be {expected_type}, got {_type_name(value)}.")


def _validate_string(value: str, schema: Mapping[str, Any], path: str) -> None:
    if "minLength" in schema and len(value) < int(schema["minLength"]):
        raise JsonSchemaValidationError(f"{path} must contain at least {schema['minLength']} characters.")
    if "maxLength" in schema and len(value) > int(schema["maxLength"]):
        raise JsonSchemaValidationError(f"{path} must contain at most {schema['maxLength']} characters.")
    if "pattern" in schema and re.fullmatch(str(schema["pattern"]), value) is None:
        raise JsonSchemaValidationError(f"{path} must match pattern {schema['pattern']!r}.")
    if schema.get("format") == "date":
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            raise JsonSchemaValidationError(f"{path} must be a YYYY-MM-DD date string.")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise JsonSchemaValidationError(f"{path} must be a valid calendar date.") from exc


def _validate_number(value: int, schema: Mapping[str, Any], path: str) -> None:
    if "minimum" in schema and value < int(schema["minimum"]):
        raise JsonSchemaValidationError(f"{path} must be at least {schema['minimum']}.")
    if "maximum" in schema and value > int(schema["maximum"]):
        raise JsonSchemaValidationError(f"{path} must be at most {schema['maximum']}.")


def _validate_array(value: list[Any], schema: Mapping[str, Any], path: str) -> None:
    if "minItems" in schema and len(value) < int(schema["minItems"]):
        raise JsonSchemaValidationError(f"{path} must contain at least {schema['minItems']} items.")
    if "maxItems" in schema and len(value) > int(schema["maxItems"]):
        raise JsonSchemaValidationError(f"{path} must contain at most {schema['maxItems']} items.")
    if schema.get("uniqueItems") is True:
        seen: set[str] = set()
        for item in value:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if key in seen:
                raise JsonSchemaValidationError(f"{path} must contain unique items.")
            seen.add(key)
    item_schema = schema.get("items")
    if isinstance(item_schema, Mapping):
        for index, item in enumerate(value):
            _validate(item, item_schema, f"{path}[{index}]")


def _validate_object(value: dict[str, Any], schema: Mapping[str, Any], path: str) -> None:
    required = schema.get("required", [])
    for name in required:
        if name not in value:
            raise JsonSchemaValidationError(f"{path}.{name} is required.")

    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(value) - set(properties))
        if unknown:
            joined = ", ".join(unknown)
            raise JsonSchemaValidationError(f"{path} contains unsupported field(s): {joined}.")

    if isinstance(properties, Mapping):
        for name, property_schema in properties.items():
            if name in value and isinstance(property_schema, Mapping):
                _validate(value[name], property_schema, f"{path}.{name}")


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__

