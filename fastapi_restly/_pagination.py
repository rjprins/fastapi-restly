"""List pagination settings and the list response envelopes.

A view's :attr:`~fastapi_restly.views.BaseRestView.pagination` holds a
:class:`NumberedPagination`, or ``None`` for a view that returns every row.
The names are public through :mod:`fastapi_restly.views` and the package
root; the page-size constants through :mod:`fastapi_restly.query`.
"""

import dataclasses
from math import ceil
from typing import Any, Generic, Sequence

import pydantic
from typing_extensions import Self, TypeVar

from .exc import RestlyConfigurationError

#: Default ``page_size`` applied to paginated list endpoints when the client
#: does not send one. A bounded default keeps a forgotten ``page_size`` from
#: returning an entire table. Override per view through
#: :attr:`NumberedPagination.default_page_size`; to return every row, set the
#: view's ``pagination`` to ``None`` instead of removing the cap.
DEFAULT_PAGE_SIZE: int = 50

#: Maximum ``page_size`` accepted by list endpoints. Values above this are
#: rejected with a 422. Override per view through
#: :attr:`NumberedPagination.max_page_size`.
MAX_PAGE_SIZE = 1000

#: The sort parameter's name. A pagination parameter cannot take it.
_SORT_QUERY_PARAM = "sort"

DataT = TypeVar("DataT")


class Envelope(pydantic.BaseModel, Generic[DataT]):
    """List response wrapper: ``{"data": [...]}``.

    The response shape for a view that returns every row
    (``pagination = None``). A paginated view uses its pagination's
    ``envelope``, :class:`PaginatedEnvelope` by default.
    """

    data: Sequence[DataT]


class PaginatedEnvelope(Envelope[DataT]):
    """Paginated list response: ``data`` plus pagination metadata.

    The default ``envelope`` of :class:`NumberedPagination`.
    """

    total_count: int
    page: int
    page_size: int
    total_pages: int


@dataclasses.dataclass(frozen=True, kw_only=True)
class NumberedPagination:
    """Page-number pagination: ``?page=2&page_size=50``.

    Set an instance as a view's
    :attr:`~fastapi_restly.views.BaseRestView.pagination`. Views inherit it,
    so a project base view sets it once, and a view that differs changes one
    setting with :meth:`replace`::

        APP_PAGINATION = fr.NumberedPagination(
            page_size_query_param="size", max_page_size=100
        )

        class AppView(fr.AsyncRestView):
            pagination = APP_PAGINATION

        class LogView(AppView):
            pagination = APP_PAGINATION.replace(max_page_size=10)

    ``envelope`` is the list response model: a generic Pydantic model with one
    type parameter, which each view fills with its response schema. Restly
    fills its fields by name from ``data``, ``total_count``, ``page``,
    ``page_size`` and ``total_pages``. Leave a field out to drop it from the
    response, and rename one with a ``serialization_alias`` or an
    ``alias_generator``. A ``model_validator(mode="before")`` receives those
    values as a dict and can reshape them, for example to nest the metadata.
    Creating the settings builds an empty page from the envelope, so a field
    Restly cannot fill fails at startup instead of on a request.
    """

    #: ``page_size`` when the client sends none.
    default_page_size: int = DEFAULT_PAGE_SIZE
    #: The largest ``page_size`` a client may ask for. A larger one returns 422.
    max_page_size: int = MAX_PAGE_SIZE
    #: The largest page number a client may ask for, or ``None`` for no limit
    #: other than the database's largest offset. A larger one returns 422.
    max_page: int | None = None
    #: The query parameter that carries the page number.
    page_query_param: str = "page"
    #: The query parameter that carries the page size.
    page_size_query_param: str = "page_size"
    #: The list response model. :class:`PaginatedEnvelope` by default.
    envelope: type[pydantic.BaseModel] = PaginatedEnvelope

    def __post_init__(self) -> None:
        _check_count("max_page_size", self.max_page_size)
        if not _is_int(self.default_page_size) or not (
            1 <= self.default_page_size <= self.max_page_size
        ):
            raise RestlyConfigurationError(
                "NumberedPagination.default_page_size must be an int in "
                f"[1, {self.max_page_size}], got {self.default_page_size!r}. "
                "To return every matching row without pagination, set "
                "'pagination = None' on the view."
            )
        if self.max_page is not None:
            _check_count("max_page", self.max_page)
        _check_query_params(
            page_query_param=self.page_query_param,
            page_size_query_param=self.page_size_query_param,
        )
        _check_envelope(self.envelope, _numbered_page_info_sample())

    def replace(self, **changes: Any) -> Self:
        """A copy with ``changes`` applied, checked like a new instance.

        For a view that changes one setting of shared pagination settings::

            pagination = APP_PAGINATION.replace(max_page_size=10)
        """
        return dataclasses.replace(self, **changes)


def _numbered_page_info(
    *, total_count: int, page: int, page_size: int
) -> dict[str, Any]:
    """The metadata a :class:`NumberedPagination` envelope is filled from,
    beside ``data``."""
    return {
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": ceil(total_count / page_size) if page_size > 0 else 0,
    }


def _numbered_page_info_sample() -> dict[str, Any]:
    return {"data": [], **_numbered_page_info(total_count=0, page=1, page_size=1)}


def _listing_envelope(
    pagination: NumberedPagination | None, item_schema: type[pydantic.BaseModel]
) -> type[pydantic.BaseModel]:
    """The list response model of a view: its pagination's envelope, or
    :class:`Envelope` without pagination, parametrized with the item schema.
    Pydantic caches the parametrized class, so every call returns the same
    one."""
    envelope: Any = Envelope if pagination is None else pagination.envelope
    return envelope[item_schema]


def _build_envelope(
    envelope: type[pydantic.BaseModel], page_info: dict[str, Any]
) -> pydantic.BaseModel:
    """Fill ``envelope`` from ``page_info`` by field name, never by alias, so
    an alias only renames a field on the wire."""
    return envelope.model_validate(page_info, by_alias=False, by_name=True)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_count(name: str, value: Any) -> None:
    if not _is_int(value) or value < 1:
        raise RestlyConfigurationError(
            f"NumberedPagination.{name} must be an int of at least 1, got {value!r}."
        )


def _check_query_params(**params: Any) -> None:
    for setting, name in params.items():
        if not isinstance(name, str) or not name:
            raise RestlyConfigurationError(
                f"NumberedPagination.{setting} must be a non-empty string, "
                f"got {name!r}."
            )
        if "__" in name or "." in name:
            raise RestlyConfigurationError(
                f"NumberedPagination.{setting} cannot be {name!r}: '__' is "
                "reserved for filter operators and '.' for relation traversal."
            )
        if name == _SORT_QUERY_PARAM:
            raise RestlyConfigurationError(
                f"NumberedPagination.{setting} cannot be {name!r}: it is the "
                "sort parameter."
            )
    if len(set(params.values())) != len(params):
        raise RestlyConfigurationError(
            f"NumberedPagination query parameters must differ, got {params!r}."
        )


def _check_envelope(envelope: Any, page_info: dict[str, Any]) -> None:
    if not (isinstance(envelope, type) and issubclass(envelope, pydantic.BaseModel)):
        raise RestlyConfigurationError(
            f"NumberedPagination.envelope must be a Pydantic model class, "
            f"got {envelope!r}."
        )
    generic = envelope.__pydantic_generic_metadata__
    if generic["origin"] is not None or len(generic["parameters"]) != 1:
        raise RestlyConfigurationError(
            f"NumberedPagination.envelope must be a generic Pydantic model with "
            f"one type parameter for the items, such as "
            f"'class Page(BaseModel, Generic[T])', got {envelope.__name__}."
        )
    try:
        _build_envelope(envelope, page_info)
    except pydantic.ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or envelope.__name__}: "
            f"{error['msg']}"
            for error in exc.errors(include_url=False)
        )
        raise RestlyConfigurationError(
            f"NumberedPagination.envelope {envelope.__name__} cannot be filled "
            f"from the page info ({', '.join(page_info)}): {problems}. Name each "
            "field after one of these values, rename it on the wire with an "
            "alias, or give it a default."
        ) from None
