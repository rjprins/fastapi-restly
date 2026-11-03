"""What a view uses for a setting that it does not set.

Each default is a :class:`~fastapi_restly.views.SchemaType` on ``BaseRestView``,
so it follows each view. This module also finds the class in the MRO that
declares or decides a setting: a subclass with a new ``schema`` must not inherit
a class that was declared for the old one.
"""

import inspect
import weakref
from typing import Any

import pydantic

from .._pagination import NumberedPagination
from ..exc import RestlyConfigurationError
from ..query._impl import _KEY_SETTINGS, _derive_list_params, _record_of
from ..query._paged import PagedListParams, _completed_list_params
from ..schemas import (
    derive_create_schema,
    derive_list_response_schema,
    derive_response_schema,
    derive_schema,
    derive_update_schema,
)
from ._guards import _owner
from ._list_params import _LIST_SETTING_DEFAULTS, _list_setting, _own_filters
from ._schema_types import SchemaType

#: What Restly builds each schema of a view from.
_BUILT_FROM = {
    "schema": "model",
    "response_schema": "schema",
    "list_params_schema": "response_schema",
    "list_response_schema": "response_schema",
    "create_schema": "schema",
    "update_schema": "schema",
}


def _declared_at(cls: type, name: str) -> int | None:
    """Position in the MRO of the class that declares a class for ``name``, or
    None when the nearest value is a :class:`SchemaType`: that follows each
    view."""
    for index, klass in enumerate(cls.__mro__):
        if name in klass.__dict__:
            return None if isinstance(klass.__dict__[name], SchemaType) else index
    return None


def _function_at(cls: type, name: str) -> int | None:
    """Position in the MRO of the class with the function that ``cls`` uses for
    ``name``, or None when the nearest value is a class."""
    for index, klass in enumerate(cls.__mro__):
        if name in klass.__dict__:
            return index if isinstance(klass.__dict__[name], SchemaType) else None
    return None


def _decided_at(cls: type, name: str) -> int | None:
    """Position in the MRO of the class that decides the value of ``name``.

    A declared value is decided where it is declared, unless what it is built
    from is decided nearer to ``cls``: then Restly builds it again. A function
    is decided where it is set, or where its source is decided, if that is
    nearer. Declared list params that
    Restly would generate for the declaring class are also built from the list
    settings and the filter methods.
    """
    declared = _declared_at(cls, name)
    source = _BUILT_FROM.get(name)
    if source is None:
        return declared
    source_at = _decided_at(cls, source)
    if name == "list_params_schema" and declared is not None:
        # Declared list params that are the generated ones also follow the
        # settings and the filter methods of a subclass.
        settings_at = _list_settings_at(cls)
        if settings_at < declared and _generated_for(cls.__mro__[declared]):
            if source_at is None or settings_at < source_at:
                source_at = settings_at
    if declared is None:
        function_at = _function_at(cls, name)
        return min(
            (at for at in (function_at, source_at) if at is not None), default=None
        )
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
    declared = vars(declarer)["list_params_schema"]
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


def _drop_stale_settings(cls: type) -> None:
    """A subclass with a new ``schema`` gets new classes built from it, also when
    a base class declares them. So it gets the nearest function back: the one of
    a base view, or Restly's default."""
    for name in _DEFAULT_SETTINGS:
        declared = _declared_at(cls, name)
        if declared is not None and _decided_at(cls, name) != declared:
            setattr(cls, name, _nearest_function(cls, name))


def _nearest_function(cls: type, name: str) -> SchemaType:
    return next(
        vars(klass)[name]
        for klass in cls.__mro__
        if isinstance(vars(klass).get(name), SchemaType)
    )


def _uses_default(cls: type, name: str) -> bool:
    """Whether the view uses Restly's default for ``name``."""
    return inspect.getattr_static(cls, name, None) is _DEFAULT_SETTINGS[name]


def _derive_view_list_params(view_cls: Any) -> type[pydantic.BaseModel]:
    """The list params that Restly generates for a view: from its response
    schema, its pagination, its list settings and its filter methods."""
    return _derive_list_params(
        view_cls.response_schema,
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


def _list_params_schema(view_cls: Any) -> type[pydantic.BaseModel]:
    """The list params of a view, as its list routes use them.

    List params from ``fr.query.PagedListParams`` get the page fields of the
    view's pagination: the attribute keeps the class that the view declares,
    and each view with other pages gets its own subclass.
    """
    declared = view_cls.list_params_schema
    pagination = getattr(view_cls, "pagination", None)
    if _is_paged_list_params(declared) and isinstance(pagination, NumberedPagination):
        return _completed_list_params(declared, pagination)
    return declared


def _is_paged_list_params(list_params: Any) -> bool:
    """Whether ``list_params`` come from ``fr.query.PagedListParams``."""
    return isinstance(list_params, type) and issubclass(list_params, PagedListParams)


#: What a view uses when it sets nothing. A function reads what it needs: a base
#: view without a model gets ``AttributeError``, as for a setting it does not have.
_DEFAULT_SETTINGS = {
    "schema": SchemaType(lambda view: derive_schema(view.model)),
    "response_schema": SchemaType(lambda view: derive_response_schema(view.schema)),
    "list_params_schema": SchemaType(_derive_view_list_params),
    "list_response_schema": SchemaType(
        lambda view: derive_list_response_schema(
            view.response_schema, pagination=view.pagination
        )
    ),
    "create_schema": SchemaType(
        lambda view: derive_create_schema(view.schema, view.model)
    ),
    "update_schema": SchemaType(
        lambda view: derive_update_schema(view.schema, view.model)
    ),
}
