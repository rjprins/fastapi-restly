"""Warnings for code that runs but is likely a mistake.

``fr.configure(warn_on_misuse=True)`` turns these checks on. When a view
registers, they warn about, for example, a call to ``session.commit()`` in a
view method, a reference field with the name of a foreign key column, and a
writable field that sets a column that the model sets.
"""

import dataclasses
import inspect
import types
import warnings
from typing import Any

import pydantic
from sqlalchemy import inspect as sa_inspect

from ..exc import RestlyMisuseWarning
from ..schemas._base import (
    _generated_primary_key_fields,
    _is_readonly,
    reference_origin_and_target,
)
from ._base import BaseRestView, View, ViewRoute, _serves_route
from ._generated import _uses_default
from ._guards import _WRITE_VERBS
from ._schema_to_model import _relationship_name_for_fk

#: The five endpoint methods defined by RestView / AsyncRestView.
_ENDPOINT_METHOD_NAMES = frozenset(
    {
        "get_many_endpoint",
        "get_one_endpoint",
        "create_endpoint",
        "update_endpoint",
        "delete_endpoint",
    }
)


def _source_of(value: Any) -> str:
    """The source code of a function on a view class, or ``""`` when Python
    has none, such as for a function that was built at run time."""
    func = getattr(value, "__func__", value)
    if not isinstance(func, types.FunctionType):
        return ""
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        return ""


def _warn_on_misuse(view_cls: type[View]) -> None:
    """Opt-in registration-time lint (``fr.configure(warn_on_misuse=True)``).

    Flags the dominant misuse patterns with the idiomatic fix named in
    each message. Heuristic, best-effort, and advisory: every pattern it flags
    has a legitimate use, so it warns (:class:`RestlyMisuseWarning`) rather
    than rejects. Must run *before* parent endpoints are copied into the
    subclass, while ``__dict__`` still holds only what the user wrote; only
    the registered class is linted, not user-defined intermediate bases.
    """
    own = view_cls.__dict__
    name = view_cls.__name__

    # 1. Endpoint-method override where a business-method override was likely
    # meant. A replacement that calls the handler, or the endpoint method of
    # its base, keeps the steps: that is how a view changes the HTTP contract.
    if issubclass(view_cls, BaseRestView):
        for endpoint in sorted(_ENDPOINT_METHOD_NAMES & own.keys()):
            verb = endpoint.removesuffix("_endpoint")
            source = _source_of(own[endpoint])
            if f"handle_{verb}(" in source or f"super().{endpoint}(" in source:
                continue
            if verb in _WRITE_VERBS:
                seams = f"For domain logic override the business method '{verb}'"
                keep = "the bracket"
            else:
                lookup = "where_id" if verb == "get_one" else "apply_list_params"
                seams = f"For the rows override 'scope', for the lookup '{lookup}'"
                keep = "the gate"
            warnings.warn(
                f"{name} overrides the endpoint method '{endpoint}'. Override "
                f"an endpoint method only to change the HTTP contract. {seams}; "
                f"for a gate 'authorize'; for the response 'to_single_response' "
                f"or 'to_list_response'. "
                f"Call 'handle_{verb}' from the replacement to keep {keep}.",
                RestlyMisuseWarning,
                stacklevel=5,
            )

    # 2. Manual session.commit() in a view method. The framework owns the
    # commit; methods that go through write_action / handle_<verb> are exempt.
    for attr, value in own.items():
        source = _source_of(value)
        if (
            ".commit(" in source
            and "write_action" not in source
            and "handle_" not in source
        ):
            warnings.warn(
                f"{name}.{attr} calls session.commit() directly. The framework "
                f"owns the commit: reuse handle_<verb>(), or bracket the "
                f"mutation with write_action(self.Action.<NAME>, ...) so authorize / "
                f"before_action_commit / after_action_commit run.",
                RestlyMisuseWarning,
                stacklevel=5,
            )

    # 3. A reference type (IDRef/IDSchema) whose name is a scalar FK column:
    # the ``post_id: IDRef[Post]`` mistake, where ``data.post_id`` becomes a
    # wrapper instead of the plain id.
    _warn_scalar_named_reference_fields(view_cls)

    # 4. A writable field on a column that the model sets, such as a tenant
    # or an audit column: the request body replaces the column default.
    _warn_writable_model_set_fields(view_cls)


def _warn_scalar_named_reference_fields(view_cls: type[View]) -> None:
    """Flag an ``IDRef``/``IDSchema`` field whose name is a scalar foreign-key
    column: ``post_id: IDRef[Post]`` makes ``data.post_id`` a reference wrapper
    instead of the plain id. Steer to ``fr.MustExist[pk, Model]``. Best-effort:
    lints only an explicitly-declared ``schema`` against the view's ``model``,
    and never a relationship-named or list reference (those names aren't columns).
    """
    schema = view_cls.__dict__.get("schema")
    model = getattr(view_cls, "model", None)
    if schema is None or model is None:
        return
    try:
        mapper = sa_inspect(model)
    except Exception:
        return
    for field_name, field_info in schema.model_fields.items():
        origin_target = reference_origin_and_target(field_info.annotation)
        if origin_target is None:
            continue
        column = mapper.columns.get(field_name)
        if column is None or not column.foreign_keys:
            continue  # a relationship-named or non-FK column is legitimate
        origin, target = origin_target
        ref_name = origin.__name__
        target_name = target.__name__ if target is not None else "Model"
        try:
            pk_name = column.type.python_type.__name__
        except Exception:
            pk_name = "int"
        rel = _relationship_name_for_fk(model, field_name)
        rename = (
            f", or name the field `{rel}` for the {ref_name} relationship"
            if rel
            else ""
        )
        warnings.warn(
            f"{schema.__name__}.{field_name} is typed `{ref_name}[{target_name}]`, "
            f"but `{field_name}` maps to the scalar foreign-key column "
            f"`{model.__name__}.{field_name}`. An {ref_name} field resolves to an "
            f"ORM object, so `data.{field_name}` is a reference wrapper, not the "
            f"plain id. Use `{field_name}: fr.MustExist[{pk_name}, {target_name}]` "
            f"for a checked scalar FK{rename}.",
            RestlyMisuseWarning,
            stacklevel=6,
        )


def _model_set_fields(model: type[Any]) -> dict[str, str]:
    """Map each attribute whose write sets a column that the model sets to
    that column.

    The model sets a column that the constructor does not take
    (``init=False``) and that has a default: ``default`` or
    ``default_factory`` on the field, or ``insert_default`` or
    ``server_default`` on the column. A many-to-one relationship maps to its
    foreign key column, because a write to the relationship sets it. A
    primary key that the server generates is left out, because Restly already
    leaves it out of the request bodies.
    """
    if not dataclasses.is_dataclass(model):
        return {}
    mapper = sa_inspect(model)
    generated_keys = _generated_primary_key_fields(model)
    columns: dict[str, str] = {}
    for field in dataclasses.fields(model):
        if field.init or field.name in generated_keys:
            continue
        column = mapper.columns.get(field.name)
        if column is None:
            continue
        if (
            field.default is not dataclasses.MISSING
            or field.default_factory is not dataclasses.MISSING
            or column.default is not None
            or column.server_default is not None
        ):
            columns[field.name] = field.name
    for relationship in mapper.relationships:
        for column in relationship.local_columns:
            key = mapper.get_property_by_column(column).key
            if key in columns:
                columns[relationship.key] = key
    return columns


def _warn_writable_model_set_fields(view_cls: type[View]) -> None:
    """Flag a writable field that sets a column that the model sets.

    A column with ``init=False`` and a default, such as a tenant column with
    ``insert_default=Current.org_id``, gets its value from the model. Restly
    sets each writable field that the constructor does not take after it
    builds the object, so a value in the request body replaces the default:
    a client can pick the tenant. Lints the write classes of the routes that
    the view has: each class that the view declares, and the declared
    ``schema`` when Restly derives a write class from it. A generated
    ``schema`` already marks these columns ReadOnly.
    """
    if not issubclass(view_cls, BaseRestView):
        return
    model = getattr(view_cls, "model", None)
    if model is None:
        return
    try:
        model_set = _model_set_fields(model)
    except Exception:
        return
    if not model_set:
        return
    # Each write class, and whether Restly derives it from ``schema``.
    write_classes: dict[type[pydantic.BaseModel], bool] = {}
    for attr, routes in (
        ("create_schema", (ViewRoute.CREATE.value,)),
        # the PUT route of a react-admin view takes the update body too
        ("update_schema", (ViewRoute.UPDATE.value, "put")),
    ):
        if not any(_serves_route(view_cls, route) for route in routes):
            continue
        if not _uses_default(view_cls, attr):
            write_classes.setdefault(getattr(view_cls, attr), False)
        elif not _uses_default(view_cls, "schema"):
            write_classes.setdefault(view_cls.schema, True)
    for cls, derived in write_classes.items():
        for field_name, field_info in cls.model_fields.items():
            column = model_set.get(field_name)
            if column is None or _is_readonly(field_info):
                continue
            through = (
                ""
                if column == field_name
                else f" and sets `{model.__name__}.{column}` through the relationship"
            )
            fix = (
                "Mark the field `fr.ReadOnly[...]`"
                if derived
                else f"Leave the field out of {cls.__name__}"
            )
            warnings.warn(
                f"{cls.__name__}.{field_name} is writable{through}, but "
                f"`{model.__name__}.{column}` has `init=False` and a default, "
                f"so the model sets it. A value in the request body replaces "
                f"the default. {fix}, so the value always comes from the "
                f"model. If the client may set it, remove `init=False` from "
                f"the column, so that the constructor takes it.",
                RestlyMisuseWarning,
                stacklevel=6,
            )
