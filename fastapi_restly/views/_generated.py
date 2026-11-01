"""What registration generates for a view.

A view that does not declare a response schema or list params gets them
generated from its schema. This module finds the class in the MRO that
declares or decides a setting, so that registration generates only what is
missing. It also finds the primary key fields that the server generates.
"""

import dataclasses
import weakref
from typing import Any

import pydantic
from sqlalchemy import inspect as sa_inspect

from .._pagination import NumberedPagination
from ..exc import RestlyConfigurationError
from ..query._impl import _KEY_SETTINGS, _derive_list_params, _record_of
from ..query._paged import PagedListParams, _completed_list_params
from ..schemas import derive_schema_response
from ._guards import _owner
from ._list_params import _LIST_SETTING_DEFAULTS, _list_setting, _own_filters


def _generated_primary_key_fields(model_cls: type[Any]) -> frozenset[str]:
    """The primary key attributes the server generates, never the client.

    A derived create or update schema leaves these out even when the schema
    does not mark them ReadOnly. Generated means an autoincrement column, a
    column default, or a dataclass field outside ``__init__``. A key without
    any of these is a natural key, and the client supplies it.
    """
    mapper = sa_inspect(model_cls)
    autoincrement = getattr(mapper.local_table, "autoincrement_column", None)
    not_in_init = (
        {f.name for f in dataclasses.fields(model_cls) if not f.init}
        if dataclasses.is_dataclass(model_cls)
        else set()
    )
    generated = set()
    for column in mapper.primary_key:
        key = mapper.get_property_by_column(column).key
        if (
            column is autoincrement
            or column.default is not None
            or column.server_default is not None
            or key in not_in_init
        ):
            generated.add(key)
    return frozenset(generated)


#: What registration builds each class of a view from.
_BUILT_FROM = {
    "schema": "model",
    "schema_response": "schema",
    "schema_list_params": "schema_response",
    "schema_create": "schema",
    "schema_update": "schema",
}


def _mark_generated(cls: type, name: str) -> None:
    generated = cls.__dict__.get("_fr_generated", frozenset())
    cls._fr_generated = generated | {name}  # type: ignore[attr-defined]


def _declared_at(cls: type, name: str) -> int | None:
    """Position in the MRO of the class that declares ``name``, or None.

    A value that registration generated is not a declaration.
    """
    for index, klass in enumerate(cls.__mro__):
        if name in klass.__dict__ and name not in klass.__dict__.get(
            "_fr_generated", ()
        ):
            return index
    return None


def _decided_at(cls: type, name: str) -> int | None:
    """Position in the MRO of the class that decides the value of ``name``.

    A declared value is decided where it is declared, unless what it is built
    from is decided nearer to ``cls``: then registration rebuilds it. A
    generated value is decided where its source is decided. So a copy that
    registration wrote into a subclass does not count as a change. Declared
    list params that Restly would generate for the declaring class are also
    built from the list settings and the filter methods.
    """
    declared = _declared_at(cls, name)
    source = _BUILT_FROM.get(name)
    if source is None:
        return declared
    source_at = _decided_at(cls, source)
    if name == "schema_list_params" and declared is not None:
        # Declared list params that are the generated ones also follow the
        # settings and the filter methods of a subclass.
        settings_at = _list_settings_at(cls)
        if settings_at < declared and _generated_for(cls.__mro__[declared]):
            if source_at is None or settings_at < source_at:
                source_at = settings_at
    if declared is None:
        return source_at
    # A declared schema stays, whatever model a subclass sets.
    if name != "schema" and source_at is not None and source_at < declared:
        return source_at
    return declared


_GENERATED_FOR: "weakref.WeakKeyDictionary[type, bool]" = weakref.WeakKeyDictionary()


def _generated_for(declarer: type) -> bool:
    """Whether the list params that ``declarer`` declares are the ones that
    Restly would generate for it.

    Only then does a subclass with other settings get new list params.
    List params from a narrower schema, or with settings that the class does
    not have, would get wider keys.
    """
    if declarer in _GENERATED_FOR:
        return _GENERATED_FOR[declarer]
    declared = vars(declarer)["schema_list_params"]
    generated: type[pydantic.BaseModel] | None = None
    if _record_of(declared) is not None:
        try:
            generated = _derive_view_list_params(declarer)
        except (AttributeError, ValueError, RestlyConfigurationError):
            pass  # the class cannot generate list params, such as a mixin
    result = (
        generated is not None
        and list(generated.model_fields) == list(declared.model_fields)
        and _record_of(generated) == _record_of(declared)
    )
    _GENERATED_FOR[declarer] = result
    return result


def _list_settings_at(cls: type) -> int:
    """Position in the MRO of the nearest class that sets the pagination, a
    list setting or a filter method, or drops a filter method with None."""
    names = {"pagination", *_LIST_SETTING_DEFAULTS}
    for klass in cls.__mro__:
        names.update(
            name
            for name, value in vars(klass).items()
            if hasattr(value, "_fr_list_filter")
        )
    return next(
        index for index, klass in enumerate(cls.__mro__) if names & vars(klass).keys()
    )


def _needs_generating(cls: type, name: str) -> bool:
    """Whether registration must build ``name`` for this view class.

    A declared value is inherited. Registration builds it when no class
    declares it, or when what it is built from is decided nearer to ``cls``,
    as in a subclass that declares a new ``schema``.
    """
    if name in cls.__dict__.get("_fr_generated", ()):
        return False
    declared = _declared_at(cls, name)
    return declared is None or _decided_at(cls, name) != declared


def _schema_response(view_cls: type) -> type[pydantic.BaseModel]:
    """The response class of a view.

    Registration sets ``schema_response``. A view that is not registered
    yet, or a subclass that changes ``schema``, gets the class that
    registration would set.
    """
    if _needs_generating(view_cls, "schema_response"):
        return derive_schema_response(view_cls.schema)
    return view_cls.schema_response


def _derive_view_list_params(view_cls: type) -> type[pydantic.BaseModel]:
    """The list params that registration generates for a view: from its
    response class, its pagination, its list settings and its filter
    methods."""
    if not hasattr(view_cls, "model"):
        raise ValueError(
            f"'{view_cls.__name__}.model' must be specified: it is needed to "
            "generate the list params."
        )
    return _derive_list_params(
        _schema_response(view_cls),
        view_cls.model,
        pagination=view_cls.pagination,
        settings={name: getattr(view_cls, name) for name in _KEY_SETTINGS},
        owner=view_cls.__name__,
        # a base view serves many schemas
        unchecked={name for name in _KEY_SETTINGS if name not in vars(view_cls)},
        own_filters=_own_filters(view_cls),
        sort_query_param=view_cls.sort_query_param,
        default_sort=_list_setting(view_cls, "default_sort"),
        default_sort_from=(
            None
            if "default_sort" in vars(view_cls)
            else _owner(view_cls, "default_sort").__name__
        ),
    )


def _schema_list_params(view_cls: type) -> type[pydantic.BaseModel]:
    """The list params of a view, as its list routes use them.

    Registration sets ``schema_list_params``. A view that is not registered
    yet, such as a subclass of a registered view, gets the class that
    registration would set. List params from ``fr.query.PagedListParams``
    get the page fields of the view's pagination: the attribute keeps the
    class that the view declares, and each view with other pages gets its
    own subclass.
    """
    if _needs_generating(view_cls, "schema_list_params"):
        return _derive_view_list_params(view_cls)
    declared = view_cls.schema_list_params
    pagination = getattr(view_cls, "pagination", None)
    if _is_paged_list_params(declared) and isinstance(pagination, NumberedPagination):
        return _completed_list_params(declared, pagination)
    return declared


def _is_paged_list_params(list_params: Any) -> bool:
    """Whether ``list_params`` come from ``fr.query.PagedListParams``."""
    return isinstance(list_params, type) and issubclass(list_params, PagedListParams)
