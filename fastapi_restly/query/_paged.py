"""List params of your own with pages: the fields of the view's pagination."""

import functools
import inspect
from typing import Any

import pydantic

from .._pagination import (
    _DEFAULT_PAGINATION,
    _MAX_PAGINATION_OFFSET,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    OffsetPagination,
    Pagination,
    _offset_validator,
)


class PagedListParams(pydantic.BaseModel):
    """The base of list params of your own with pages: the fields ``page``
    and ``page_size``.

    .. code-block:: python

        class ItemSearch(fr.query.PagedListParams):
            q: str | None = None

    When a view with a :class:`~fastapi_restly.views.NumberedPagination`
    registers, Restly gives the two fields the settings of the view's
    ``pagination``: their names in the URL, such as ``size`` for
    ``page_size_query_param="size"``, the default page size, the bounds, and
    the check that the offset of the page fits in the database. So each view
    gets its own pages from one class. Your code reads ``list_params.page``
    and ``list_params.page_size``, also when the URL names them otherwise.

    Restly's own ``apply_list_params`` applies the page. An override of it
    applies the page itself; see :ref:`own-list-params`. A view with another
    pagination does not take these list params, and a view fails when it
    registers if a subclass declares one of the two fields again: set the
    default and the bounds on the pagination. Outside a view, the fields
    keep their defaults: ``page`` 1, and ``page_size``
    :data:`~fastapi_restly.query.DEFAULT_PAGE_SIZE`, at most
    :data:`~fastapi_restly.query.MAX_PAGE_SIZE`.
    """

    #: The page, from 1.
    page: int = pydantic.Field(1, ge=1)
    #: The number of rows on a page.
    page_size: int = pydantic.Field(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    _validate_pagination_offset = _offset_validator(
        "page", "page_size", "page", "page_size"
    )


class OffsetListParams(pydantic.BaseModel):
    """The base of list params of your own with a limit and an offset: the
    fields ``limit`` and ``offset``.

    .. code-block:: python

        class ItemSearch(fr.query.OffsetListParams):
            q: str | None = None

    When a view with a :class:`~fastapi_restly.views.OffsetPagination`
    registers, Restly gives the two fields the settings of the view's
    ``pagination``: their names in the URL, such as ``skip`` for
    ``offset_query_param="skip"``, the default limit and the bounds. So each
    view gets its own limits from one class. Your code reads
    ``list_params.limit`` and ``list_params.offset``, also when the URL names
    them otherwise.

    Restly's own ``apply_list_params`` applies the limit and the offset. An
    override of it applies them itself; see :ref:`own-list-params`. A view with
    another pagination does not take these list params, and a view fails when it
    registers if a subclass declares one of the two fields again: set the
    default and the bounds on the pagination. Outside a view, the fields keep
    their defaults: ``offset`` 0, and ``limit``
    :data:`~fastapi_restly.query.DEFAULT_PAGE_SIZE`, at most
    :data:`~fastapi_restly.query.MAX_PAGE_SIZE`.
    """

    #: The largest number of rows to return.
    limit: int = pydantic.Field(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    #: The number of rows to skip.
    offset: int = pydantic.Field(0, ge=0, le=_MAX_PAGINATION_OFFSET)


#: The style that each base of list params of your own needs, as the settings
#: that the base uses outside a view.
_PAGINATION_OF_BASE: dict[type[pydantic.BaseModel], Pagination] = {
    PagedListParams: _DEFAULT_PAGINATION,
    OffsetListParams: OffsetPagination(),
}

#: The pagination of each class from :func:`_completed_list_params`.
_COMPLETED_WITH: dict[type[pydantic.BaseModel], Pagination] = {}


def _page_fields_base(list_params: Any) -> type[pydantic.BaseModel] | None:
    """The base that ``list_params`` get their page fields from, if any."""
    if isinstance(list_params, type):
        for base in _PAGINATION_OF_BASE:
            if issubclass(list_params, base):
                return base
    return None


def _takes_page_fields(list_params: Any, pagination: Any) -> bool:
    """Whether ``list_params`` get their page fields from ``pagination``."""
    base = _page_fields_base(list_params)
    return base is not None and isinstance(pagination, type(_PAGINATION_OF_BASE[base]))


def _redeclared_page_fields(
    list_params: type[pydantic.BaseModel], base: type[pydantic.BaseModel]
) -> list[str]:
    """The fields of ``base`` that a subclass declares again. A view replaces
    them with the fields of its pagination."""
    below_base = list_params.__mro__[: list_params.__mro__.index(base)]
    return sorted(
        {
            name
            for cls in below_base
            for name in inspect.get_annotations(cls)
            if name in base.model_fields
        }
    )


def _pagination_of_list_params(params_model: type) -> Pagination:
    """The pagination of list params without a record: the one of the view that
    completed them, the one of their base, or the default."""
    completed_with = _COMPLETED_WITH.get(params_model)
    if completed_with is not None:
        return completed_with
    base = _page_fields_base(params_model)
    return _PAGINATION_OF_BASE[base] if base is not None else _DEFAULT_PAGINATION


@functools.cache
def _completed_list_params(
    declared: type[pydantic.BaseModel], pagination: Pagination
) -> type[pydantic.BaseModel]:
    """``declared`` with the page fields of ``pagination``: the subclass that a
    view with that pagination uses as its list params."""
    completed: type[pydantic.BaseModel] = pydantic.create_model(  # type: ignore[call-overload]
        declared.__name__,
        __base__=declared,
        __module__=declared.__module__,
        __validators__=pagination._list_params_validators(own=True),
        **pagination._list_params_fields(own=True),
    )
    _COMPLETED_WITH[completed] = pagination
    return completed
