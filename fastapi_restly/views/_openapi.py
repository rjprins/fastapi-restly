"""
Internal OpenAPI post-processing: x-resource-ref annotations.

Called automatically by include_view() — no public API.

FK columns and SQLAlchemy relationship fields backed by IDSchema/IDRef
are annotated with ``x-resource-ref: "<resource-name>"`` in the generated spec.
Full nested-object relationships (plain Pydantic model fields) are left untouched.
"""

import inspect
import types
import weakref
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

import fastapi
import pydantic
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase

from ..schemas import IDSchema

_PATCHED_ATTR = "_fr_resource_refs_patched"


@dataclass
class _Entry:
    model: type[DeclarativeBase]
    resource_name: str
    schema: type[pydantic.BaseModel]
    creation_schema: type[pydantic.BaseModel]
    update_schema: type[pydantic.BaseModel]


_RegistryKey = int
_RegistryRef = weakref.ReferenceType[fastapi.FastAPI | fastapi.APIRouter]
_registry: dict[_RegistryKey, tuple[_RegistryRef, list[_Entry]]] = {}


def _registry_entries(
    parent_router: fastapi.FastAPI | fastapi.APIRouter,
) -> list[_Entry]:
    """Return resource-ref entries for ``parent_router``.

    ``APIRouter`` instances are weak-referenceable but not hashable, so
    ``WeakKeyDictionary`` cannot store them. Key by identity and remove the
    entry when the router/app is collected.
    """
    key = id(parent_router)
    existing = _registry.get(key)
    if existing is not None:
        ref, entries = existing
        if ref() is parent_router:
            return entries

    def cleanup(ref: _RegistryRef, key: _RegistryKey = key) -> None:
        current = _registry.get(key)
        if current is not None and current[0] is ref:
            _registry.pop(key, None)

    ref = weakref.ref(parent_router, cleanup)
    entries: list[_Entry] = []
    _registry[key] = (ref, entries)
    return entries


def _registered_entries(
    parent_router: fastapi.FastAPI | fastapi.APIRouter,
) -> list[_Entry]:
    existing = _registry.get(id(parent_router))
    if existing is None:
        return []
    ref, entries = existing
    if ref() is parent_router:
        return entries
    return []


def _register_for_resource_ref(
    parent_router: fastapi.FastAPI | fastapi.APIRouter, view_cls: type
) -> None:
    """Register a view's model→resource mapping and ensure the spec is patched.

    Silently skips views without a SQLAlchemy model (e.g. plain View subclasses).
    """
    model = getattr(view_cls, "model", None)
    if model is None or not (
        isinstance(model, type) and issubclass(model, DeclarativeBase)
    ):
        return

    resource_name = "".join(
        c.__dict__["prefix"] for c in reversed(view_cls.mro()) if "prefix" in c.__dict__
    ).lstrip("/")

    entry = _Entry(
        model=model,
        resource_name=resource_name,
        schema=view_cls.schema,
        creation_schema=view_cls.creation_schema,
        update_schema=view_cls.update_schema,
    )

    entries = _registry_entries(parent_router)
    entries.append(entry)

    # Only the FastAPI app generates the OpenAPI spec; APIRouter parents have
    # no ``.openapi`` to patch. Views registered on a router lose x-resource-ref
    # annotations until the framework can walk to a root app, which is a
    # separate gap.
    if isinstance(parent_router, fastapi.FastAPI):
        _ensure_patched(parent_router)


def _ensure_patched(app: fastapi.FastAPI) -> None:
    """Wrap app.openapi() once so annotations are injected on first call."""
    if getattr(app, _PATCHED_ATTR, False):
        return

    original_openapi = app.openapi

    def patched_openapi() -> dict[str, Any]:
        spec = original_openapi()
        entries = _registered_entries(app)
        model_to_resource = {e.model: e.resource_name for e in entries}
        _annotate_spec(spec, entries, model_to_resource)
        return spec

    app.openapi = patched_openapi  # type: ignore[method-assign]
    setattr(app, _PATCHED_ATTR, True)


def _is_id_ref_annotation(annotation: Any) -> bool:
    """Return True if annotation is IDSchema[X], IDRef[X], or list/Optional thereof.

    Returns False for full nested Pydantic model objects — those are not ID references.
    Concrete user-defined subclasses like ``AuthorRead(IDSchema)`` return False;
    only parametrized generics like ``IDSchema[Author]`` or ``IDRef[Author]``
    return True, since those represent model ID references.
    """
    origin = get_origin(annotation)

    # Unwrap Optional / Union  (X | None, Optional[X], Union[X, Y])
    if origin in (Union, types.UnionType):
        return any(
            _is_id_ref_annotation(a)
            for a in get_args(annotation)
            if a is not type(None)
        )

    # list[X] — check the element type
    if origin is list:
        args = get_args(annotation)
        return bool(args and _is_id_ref_annotation(args[0]))

    # Check for parametrized IDSchema/IDRef generics.
    # In Pydantic v2, IDSchema[Author] and IDRef[Author] are concrete classes,
    # so inspect.isclass() returns True for them too. We distinguish via
    # __pydantic_generic_metadata__["origin"]:
    #   - Parametrized: IDSchema[Author]  → origin = IDSchema
    #   - Parametrized: IDRef[Author] → origin = IDRef
    #   - User-defined subclass: AuthorRead(IDSchema) → origin = None (not a parametrization)
    pydantic_meta = getattr(annotation, "__pydantic_generic_metadata__", {})
    origin_cls = pydantic_meta.get("origin")
    if inspect.isclass(origin_cls):
        try:
            return issubclass(origin_cls, IDSchema)
        except TypeError:
            return False

    return False


def _field_openapi_key(schema_cls: type[pydantic.BaseModel], field_name: str) -> str:
    """Return the OpenAPI property key for a field, respecting serialization aliases."""
    field_info = schema_cls.model_fields.get(field_name)
    if field_info is None:
        return field_name
    if field_info.serialization_alias:
        return field_info.serialization_alias
    if field_info.alias:
        return field_info.alias
    return field_name


def _compute_refs(
    schema_cls: type[pydantic.BaseModel],
    model_cls: type[DeclarativeBase],
    model_to_resource: dict[type[DeclarativeBase], str],
) -> dict[str, str]:
    """Return {openapi_property_key: resource_name} for FK columns and ID-ref relationship fields."""
    result: dict[str, str] = {}
    try:
        mapper = sa_inspect(model_cls)
    except Exception:
        return result

    for field_name, field_info in schema_cls.model_fields.items():
        resource_name: str | None = None

        if field_name in mapper.columns:
            fks = list(mapper.columns[field_name].foreign_keys)
            if fks:
                target_table = fks[0].column.table  # Table object identity
                for m in model_cls.registry.mappers:
                    if m.local_table is target_table:
                        resource_name = model_to_resource.get(m.class_)
                        break

        elif field_name in mapper.relationships:
            # Only annotate if the schema field carries ID references, not full nested objects.
            if _is_id_ref_annotation(field_info.annotation):
                target_model = mapper.relationships[field_name].mapper.class_
                resource_name = model_to_resource.get(target_model)

        if resource_name is not None:
            result[_field_openapi_key(schema_cls, field_name)] = resource_name

    return result


def _annotate_spec(
    spec: dict[str, Any],
    entries: list[_Entry],
    model_to_resource: dict[type[DeclarativeBase], str],
) -> None:
    """Mutate spec in-place, adding x-resource-ref to qualifying properties."""
    schemas = spec.get("components", {}).get("schemas", {})
    if not schemas:
        return

    for entry in entries:
        refs = _compute_refs(entry.schema, entry.model, model_to_resource)
        if not refs:
            continue

        for schema_cls in (entry.schema, entry.creation_schema, entry.update_schema):
            props = schemas.get(schema_cls.__name__, {}).get("properties", {})
            for prop_key, resource_name in refs.items():
                if prop_key in props:
                    props[prop_key]["x-resource-ref"] = resource_name
