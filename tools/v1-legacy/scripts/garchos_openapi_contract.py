"""OpenAPI-backed public contract validation for the GarchOS workflow boundary."""

from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml


class ContractConfigurationError(RuntimeError):
    """Raised when the shared OpenAPI contract cannot be loaded safely."""


class ContractValidationError(ValueError):
    """Raised when a public payload does not match the shared OpenAPI schema."""


class OpenAPIContract:
    """Read-only view of GarchOS public schemas from the mounted OpenAPI file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise ContractConfigurationError(
                "GarchOS Workflow OpenAPI is unavailable"
            ) from exc
        try:
            document = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ContractConfigurationError(
                "GarchOS Workflow OpenAPI is invalid YAML"
            ) from exc
        if not isinstance(document, dict):
            raise ContractConfigurationError("GarchOS Workflow OpenAPI must be an object")
        schemas = ((document.get("components") or {}).get("schemas"))
        if not isinstance(schemas, dict) or not schemas:
            raise ContractConfigurationError(
                "GarchOS Workflow OpenAPI is missing components.schemas"
            )
        self._document = document
        self._schemas = schemas
        self.sha256 = hashlib.sha256(raw).hexdigest()

    @classmethod
    def from_environment(cls) -> "OpenAPIContract":
        configured = os.environ.get("GARCHOS_WORKFLOW_OPENAPI", "").strip()
        if not configured:
            raise ContractConfigurationError("GARCHOS_WORKFLOW_OPENAPI is required")
        return cls(configured)

    def schema(self, name: str) -> dict[str, Any]:
        schema = self._schemas.get(name)
        if not isinstance(schema, dict):
            raise ContractConfigurationError(f"unknown OpenAPI schema: {name}")
        return deepcopy(schema)

    def validate_schema(self, name: str, payload: Any) -> Any:
        if name not in self._schemas:
            raise ContractConfigurationError(f"unknown OpenAPI schema: {name}")
        wrapper = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": self._document["components"],
        }
        validator = jsonschema.Draft202012Validator(
            wrapper,
            format_checker=jsonschema.FormatChecker(),
        )
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.absolute_path) or "$"
            raise ContractValidationError(
                f"{name} validation failed at {location} ({error.validator or 'schema'})"
            ) from error
        return payload


@lru_cache(maxsize=4)
def _contract_for_path(path: str) -> OpenAPIContract:
    return OpenAPIContract(path)


def validate_schema(name: str, payload: Any) -> Any:
    """Validate one public object using the configured shared contract."""

    configured = os.environ.get("GARCHOS_WORKFLOW_OPENAPI", "").strip()
    if not configured:
        raise ContractConfigurationError("GARCHOS_WORKFLOW_OPENAPI is required")
    return _contract_for_path(configured).validate_schema(name, payload)
