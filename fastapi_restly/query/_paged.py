"""List params of your own with pages: the page fields of the view's
pagination."""

import functools
from typing import Annotated

import pydantic

from .._pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, NumberedPagination
from ._impl import _offset_validator


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
    applies the page itself; see :ref:`own-list-params`. A view without
    pagination does not take these list params. Outside a view, the fields
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


@functools.cache
def _completed_list_params(
    declared: type[PagedListParams], pagination: NumberedPagination
) -> type[PagedListParams]:
    """``declared`` with the page fields of ``pagination``: the subclass that a
    view with that pagination uses as its list params.

    The fields keep their Python names, so code reads ``page_size`` whatever
    the URL calls it; the alias is the name in the URL.
    """
    page_description = "1-based page number."
    if pagination.max_page is not None:
        page_description += f" At most {pagination.max_page}."
    page = Annotated[
        int,
        pydantic.Field(
            ge=1,
            le=pagination.max_page,
            alias=pagination.page_query_param,
            description=page_description,
        ),
    ]
    page_size = Annotated[
        int,
        pydantic.Field(
            ge=1,
            le=pagination.max_page_size,
            alias=pagination.page_size_query_param,
            description=(
                f"Number of items per page (1–{pagination.max_page_size}). "
                f"Defaults to {pagination.default_page_size}."
            ),
        ),
    ]
    validator = _offset_validator(
        "page",
        "page_size",
        pagination.page_query_param,
        pagination.page_size_query_param,
    )
    return pydantic.create_model(  # type: ignore[call-overload,no-any-return]
        declared.__name__,
        __base__=declared,
        __module__=declared.__module__,
        __validators__={"_validate_pagination_offset": validator},
        page=(page, 1),
        page_size=(page_size, pagination.default_page_size),
    )
