"""Helpers that read the OpenAPI spec of an app in tests."""

import warnings
from typing import Any

import fastapi

from fastapi_restly.exc import RestlyDuplicateSchemaNameWarning

Spec = dict[str, Any]


def spec(app_or_spec: fastapi.FastAPI | Spec) -> Spec:
    """The spec of an app. Two classes with one name fail the test."""
    if isinstance(app_or_spec, dict):
        return app_or_spec
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)
        return app_or_spec.openapi()


def components(app_or_spec: fastapi.FastAPI | Spec) -> set[str]:
    """The names of the schema components, without FastAPI's own."""
    return set(spec(app_or_spec)["components"]["schemas"]) - {
        "HTTPValidationError",
        "ValidationError",
    }


def response_ref(
    app_or_spec: fastapi.FastAPI | Spec, path: str, method: str, status: str = "200"
) -> str:
    """The component name of a route's JSON response."""
    operation = spec(app_or_spec)["paths"][path][method]
    schema = operation["responses"][status]["content"]["application/json"]["schema"]
    return schema["$ref"].removeprefix("#/components/schemas/")
