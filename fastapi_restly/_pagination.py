"""List pagination settings and the list response envelopes.

A view's :attr:`~fastapi_restly.views.BaseRestView.pagination` holds a
:class:`NumberedPagination` or an :class:`OffsetPagination`, or a
:class:`NoPagination` (``None`` for short) for a view that returns every row.
The settings classes are public through the package root and
:mod:`fastapi_restly.views`, the envelopes through
:mod:`fastapi_restly.views`, and the page-size constants through
:mod:`fastapi_restly.query`.

What differs per style lives in the settings classes, in private methods: the
query parameters, the fields of the list params, how a statement gets its page,
and what fills the envelope. The rest of Restly calls these methods. Only
``query/_paged.py`` knows which base of list params of your own goes with which
style.
"""

import abc
import dataclasses
from collections.abc import Mapping
from math import ceil
from typing import Annotated, Any, ClassVar, Generic, Optional, Self, cast

import pydantic
from sqlalchemy import Select
from typing_extensions import TypeVar

from .exc import BadQueryParam, RestlyConfigurationError

#: The ``page_size`` or ``limit`` of a list when the client does not send one.
#: A bounded default keeps a forgotten parameter from returning an entire table.
#: Override per view through
#: :attr:`~fastapi_restly.views.NumberedPagination.default_page_size` or
#: :attr:`~fastapi_restly.views.OffsetPagination.default_limit`; to return every
#: row, set the view's ``pagination`` to ``None`` instead of removing the cap.
DEFAULT_PAGE_SIZE: int = 50

#: The largest ``page_size`` or ``limit`` that a list accepts. A larger value
#: returns 422. Override per view through
#: :attr:`~fastapi_restly.views.NumberedPagination.max_page_size` or
#: :attr:`~fastapi_restly.views.OffsetPagination.max_limit`.
MAX_PAGE_SIZE = 1000

#: Largest pagination offset accepted by supported SQL databases. SQLite and
#: PostgreSQL bind ``OFFSET`` as a signed 64-bit integer.
_MAX_PAGINATION_OFFSET = 2**63 - 1

#: The default name of the sort parameter. A view renames it with
#: ``sort_query_param``.
_SORT_QUERY_PARAM = "sort"

DataT = TypeVar("DataT")


class Envelope(pydantic.BaseModel, Generic[DataT]):
    """List response wrapper: ``{"data": [...]}``.

    The default ``envelope`` of :class:`NoPagination`, for a view that returns
    every row. A paginated view uses its pagination's ``envelope``:
    :class:`NumberedEnvelope` or :class:`OffsetEnvelope` by default.
    """

    #: The rows of the list, each as an instance of the view's response
    #: schema. A list, not a ``Sequence``: Pydantic writes the rows of a
    #: ``Sequence`` field with the settings of the envelope, not with the
    #: settings of the response schema, such as ``ser_json_inf_nan``.
    data: list[DataT]


class NumberedEnvelope(Envelope[DataT]):
    """List response with page numbers: ``data`` plus pagination metadata.

    The default ``envelope`` of :class:`NumberedPagination`.
    """

    #: The number of rows that match the filters, on all pages.
    total_count: int
    #: The number of this page. The first page is 1.
    page: int
    #: The largest number of rows on one page: the page size of the
    #: request, or the default page size.
    page_size: int
    #: The number of pages: ``total_count`` divided by ``page_size``, rounded
    #: up. It is 0 when no row matches.
    total_pages: int


class OffsetEnvelope(Envelope[DataT]):
    """List response with a limit and an offset: ``data`` plus pagination
    metadata.

    The default ``envelope`` of :class:`OffsetPagination`.
    """

    #: The number of rows that match the filters, on all pages.
    total_count: int
    #: The largest number of rows in this response: the limit of the request,
    #: or the default limit.
    limit: int
    #: The number of matching rows before the first row of this response.
    offset: int


@dataclasses.dataclass(frozen=True, kw_only=True)
class Pagination(abc.ABC):
    """The base class of the pagination settings: :class:`NumberedPagination`,
    :class:`OffsetPagination` and :class:`NoPagination`.

    Use it to annotate the setting on a project base view, so that a view
    below it can choose another style::

        class AppView(fr.AsyncRestView):
            pagination: ClassVar[fr.Pagination | None] = fr.OffsetPagination()

    You cannot create an instance of this class. A subclass of your own does
    not work yet: the methods that a style needs are private.
    """

    #: The list response model.
    envelope: type[pydantic.BaseModel] = Envelope

    #: Whether a list has pages. Then the list runs the count query.
    _has_pages: ClassVar[bool] = True
    #: What to do about list params of your own without the fields of this
    #: style. The last sentences of the message.
    _own_list_params_advice: ClassVar[str] = ""

    def replace(self, **changes: Any) -> Self:
        """A copy with ``changes`` applied, checked like a new instance.

        For a view that changes one setting of shared pagination settings::

            pagination = APP_PAGINATION.replace(
                default_page_size=10, max_page_size=10
            )
        """
        return dataclasses.replace(self, **changes)

    def _page_fields(self) -> dict[str, tuple[str, dict[str, Any], int]]:
        """The page fields of this style, by their Python name. Each has the
        name in the URL, the arguments of its ``pydantic.Field``, and its
        default."""
        return {}

    def _query_params(self) -> tuple[str, ...]:
        """The names of the parameters of this style in the URL."""
        return tuple(url for url, _, _ in self._page_fields().values())

    def _list_params_fields(self, own: bool = False) -> dict[str, Any]:
        """The fields that list params get. Generated list params name a field
        as the URL does. List params of your own (``own=True``) name it by its
        Python name, with the name in the URL as alias, so code reads one name
        whatever the URL calls it."""
        return {
            (name if own else url): (
                Annotated[
                    int, pydantic.Field(alias=url if own else None, **field_args)
                ],
                default,
            )
            for name, (url, field_args, default) in self._page_fields().items()
        }

    def _list_params_validators(self, own: bool = False) -> dict[str, Any]:
        """The model validators that list params get, as for
        :meth:`_list_params_fields`."""
        return {}

    def _own_list_params_keys(self) -> dict[str, str]:
        """The name in the URL of each field of list params of your own."""
        return {name: url for name, (url, _, _) in self._page_fields().items()}

    def _row_limit_query_param(self) -> str | None:
        """The name in the URL of the parameter that limits the rows of a page,
        on a style with pages."""
        return None

    @abc.abstractmethod
    def _apply(self, stmt: Select[Any], values: Mapping[str, Any]) -> Select[Any]:
        """``stmt`` with the page that ``values`` ask for.

        :param values: The values of the list params by query key.
        """

    @abc.abstractmethod
    def _page_info(
        self, data: list[Any], total_count: int, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        """What the envelope is filled from.

        :param data: The rows of the response.
        :param total_count: The result of the count query.
        :param values: The values of the list params by query key. Empty on a
            style without pages.
        """

    def _page_info_sample(self) -> dict[str, Any]:
        """The page info of an empty page, to check an envelope."""
        return self._page_info([], 0, {})


@dataclasses.dataclass(frozen=True, kw_only=True)
class NumberedPagination(Pagination):
    """Page-number pagination: ``?page=2&page_size=50``.

    Set an instance as a view's
    :attr:`~fastapi_restly.views.BaseRestView.pagination`. Views inherit it,
    so a project base view sets it once, and a view that differs changes one
    setting with :meth:`~fastapi_restly.views.Pagination.replace`::

        APP_PAGINATION = fr.NumberedPagination(
            page_size_query_param="size", max_page_size=100
        )

        class AppView(fr.AsyncRestView):
            pagination = APP_PAGINATION

        class LogView(AppView):
            pagination = APP_PAGINATION.replace(
                default_page_size=10, max_page_size=10
            )

    ``envelope`` is the list response model: a generic Pydantic model with one
    type parameter, which each view fills with its response schema. Restly
    fills its fields by name from ``data``, ``total_count``, ``page``,
    ``page_size`` and ``total_pages``. Leave a field out to drop it from the
    response, and rename one with an ``alias`` or an ``alias_generator``. A
    ``model_validator(mode="before")`` receives those values as a dict and can
    reshape them, for example to nest the metadata.
    Creating the settings builds an empty page from the envelope, so a
    required field Restly cannot fill fails at startup instead of on a
    request. An envelope keeps the default ``extra`` setting: Restly passes it
    every page value and it keeps the fields it declares.
    """

    #: ``page_size`` when the client sends none.
    default_page_size: int = DEFAULT_PAGE_SIZE
    #: The largest ``page_size`` a client may ask for. A larger one returns 422.
    max_page_size: int = MAX_PAGE_SIZE
    #: The largest page number a client may ask for. A larger one returns
    #: 422. ``None`` sets no limit of its own: then only a page whose offset
    #: is above ``2**63 - 1`` returns 422.
    max_page: int | None = None
    #: The query parameter that carries the page number.
    page_query_param: str = "page"
    #: The query parameter that carries the page size.
    page_size_query_param: str = "page_size"
    #: The list response model. :class:`NumberedEnvelope` by default.
    envelope: type[pydantic.BaseModel] = NumberedEnvelope

    _own_list_params_advice: ClassVar[str] = (
        "Subclass fr.query.PagedListParams, which gets the fields of the view's "
        "pagination. For a list that pages with limit and offset, set pagination "
        "= fr.OffsetPagination() and subclass fr.query.OffsetListParams. For a "
        "list that returns every row, set pagination = None."
    )

    def __post_init__(self) -> None:
        _check_count("NumberedPagination", "max_page_size", self.max_page_size)
        _check_default(
            "NumberedPagination",
            "default_page_size",
            self.default_page_size,
            self.max_page_size,
        )
        if self.max_page is not None:
            _check_count("NumberedPagination", "max_page", self.max_page)
        _check_query_params(
            "NumberedPagination",
            page_query_param=self.page_query_param,
            page_size_query_param=self.page_size_query_param,
        )
        _check_envelope("NumberedPagination", self.envelope, self._page_info_sample())

    def _row_limit_query_param(self) -> str:
        return self.page_size_query_param

    def _page_fields(self) -> dict[str, tuple[str, dict[str, Any], int]]:
        page = "1-based page number."
        if self.max_page is not None:
            page += f" At most {self.max_page}."
        page_size = (
            f"Number of items per page (1–{self.max_page_size}). "
            f"Defaults to {self.default_page_size}."
        )
        return {
            "page": (
                self.page_query_param,
                {"ge": 1, "le": self.max_page, "description": page},
                1,
            ),
            "page_size": (
                self.page_size_query_param,
                {"ge": 1, "le": self.max_page_size, "description": page_size},
                self.default_page_size,
            ),
        }

    def _list_params_validators(self, own: bool = False) -> dict[str, Any]:
        page_param, page_size_param = self._query_params()
        page_attr, page_size_attr = (
            ("page", "page_size") if own else self._query_params()
        )
        return {
            "_validate_pagination_offset": _offset_validator(
                page_attr, page_size_attr, page_param, page_size_param
            )
        }

    def _apply(self, stmt: Select[Any], values: Mapping[str, Any]) -> Select[Any]:
        page_size = _get_int(values, self.page_size_query_param)
        if page_size is None:
            return stmt
        page = _get_int(values, self.page_query_param) or 1
        return stmt.limit(page_size).offset((page - 1) * page_size)

    def _page_info(
        self, data: list[Any], total_count: int, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        page_size = _get_int(values, self.page_size_query_param)
        if page_size is None:
            page_size = self.default_page_size
        return {
            "data": data,
            "total_count": total_count,
            "page": _get_int(values, self.page_query_param) or 1,
            "page_size": page_size,
            "total_pages": ceil(total_count / page_size) if page_size > 0 else 0,
        }


@dataclasses.dataclass(frozen=True, kw_only=True)
class OffsetPagination(Pagination):
    """Limit and offset pagination: ``?limit=50&offset=100``.

    Set an instance as a view's
    :attr:`~fastapi_restly.views.BaseRestView.pagination`. It works as
    :class:`NumberedPagination` does: views inherit it, and
    :meth:`~fastapi_restly.views.Pagination.replace` changes one setting. For a
    client that says ``skip``::

        class ItemView(fr.AsyncRestView):
            pagination = fr.OffsetPagination(
                offset_query_param="skip", default_limit=20, max_limit=100
            )

    ``envelope`` works as for :class:`NumberedPagination`, and Restly fills it
    by name from ``data``, ``total_count``, ``limit`` and ``offset``. The names
    in the JSON do not follow the names in the URL: to send ``skip`` in the
    response too, give the field an ``alias``.
    """

    #: ``limit`` when the client sends none.
    default_limit: int = DEFAULT_PAGE_SIZE
    #: The largest ``limit`` a client may ask for. A larger one returns 422.
    max_limit: int = MAX_PAGE_SIZE
    #: The largest ``offset`` a client may ask for. A larger one returns 422.
    #: ``None`` sets no limit of its own: then only an offset above
    #: ``2**63 - 1`` returns 422.
    max_offset: int | None = None
    #: The query parameter that carries the limit.
    limit_query_param: str = "limit"
    #: The query parameter that carries the offset.
    offset_query_param: str = "offset"
    #: The list response model. :class:`OffsetEnvelope` by default.
    envelope: type[pydantic.BaseModel] = OffsetEnvelope

    _own_list_params_advice: ClassVar[str] = (
        "Subclass fr.query.OffsetListParams, which gets the fields of the view's "
        "pagination. For a list that pages with page numbers, set pagination = "
        "fr.NumberedPagination() and subclass fr.query.PagedListParams. For a "
        "list that returns every row, set pagination = None."
    )

    def __post_init__(self) -> None:
        _check_count("OffsetPagination", "max_limit", self.max_limit)
        _check_default(
            "OffsetPagination", "default_limit", self.default_limit, self.max_limit
        )
        if self.max_offset is not None:
            _check_count("OffsetPagination", "max_offset", self.max_offset, least=0)
        _check_query_params(
            "OffsetPagination",
            limit_query_param=self.limit_query_param,
            offset_query_param=self.offset_query_param,
        )
        _check_envelope("OffsetPagination", self.envelope, self._page_info_sample())

    def _row_limit_query_param(self) -> str:
        return self.limit_query_param

    def _page_fields(self) -> dict[str, tuple[str, dict[str, Any], int]]:
        limit = (
            f"Largest number of items to return (1–{self.max_limit}). "
            f"Defaults to {self.default_limit}."
        )
        largest = _MAX_PAGINATION_OFFSET
        offset = "Number of items to skip. Defaults to 0."
        if self.max_offset is not None:
            largest = min(self.max_offset, _MAX_PAGINATION_OFFSET)
            offset += f" At most {largest}."
        return {
            "limit": (
                self.limit_query_param,
                {"ge": 1, "le": self.max_limit, "description": limit},
                self.default_limit,
            ),
            "offset": (
                self.offset_query_param,
                {"ge": 0, "le": largest, "description": offset},
                0,
            ),
        }

    def _apply(self, stmt: Select[Any], values: Mapping[str, Any]) -> Select[Any]:
        limit = _get_int(values, self.limit_query_param)
        if limit is not None:
            stmt = stmt.limit(limit)
        return stmt.offset(_get_int(values, self.offset_query_param) or 0)

    def _page_info(
        self, data: list[Any], total_count: int, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        limit = _get_int(values, self.limit_query_param)
        return {
            "data": data,
            "total_count": total_count,
            "limit": self.default_limit if limit is None else limit,
            "offset": _get_int(values, self.offset_query_param) or 0,
        }


@dataclasses.dataclass(frozen=True, kw_only=True)
class NoPagination(Pagination):
    """No pagination: a list returns every matching row and runs no count
    query.

    ``pagination = None`` is the short form of ``NoPagination()``. Set an
    instance to choose the list response model::

        T = TypeVar("T")

        class DataCount(pydantic.BaseModel, Generic[T]):
            data: list[T]
            total_count: int = pydantic.Field(alias="count")

        class TagView(fr.AsyncRestView):
            pagination = fr.NoPagination(envelope=DataCount)

    ``envelope`` works as for :class:`NumberedPagination`, and Restly fills it
    by name from ``data`` and ``total_count``, the number of rows. A generic
    ``pydantic.RootModel`` over the items whose before-validator returns
    ``data`` makes the list a bare JSON array.
    """

    #: The list response model. :class:`Envelope` by default.
    envelope: type[pydantic.BaseModel] = Envelope

    _has_pages: ClassVar[bool] = False

    def __post_init__(self) -> None:
        _check_envelope("NoPagination", self.envelope, self._page_info_sample())

    def _apply(self, stmt: Select[Any], values: Mapping[str, Any]) -> Select[Any]:
        return stmt

    def _page_info(
        self, data: list[Any], total_count: int, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Every row is on the one page, so the total is the number of rows."""
        return {"data": data, "total_count": len(data)}


def _settings_of(pagination: Pagination | None) -> Pagination:
    """The settings that ``pagination`` stands for: ``None`` is short for
    :class:`NoPagination`."""
    return _NO_PAGINATION if pagination is None else pagination


def _query_settings(pagination: Pagination | None) -> tuple[Any, ...]:
    """What the fields of list params depend on: the style, and each setting
    but the envelope."""
    settings = _settings_of(pagination)
    return (
        type(settings),
        *(
            getattr(settings, field.name)
            for field in dataclasses.fields(settings)
            if field.name != "envelope"
        ),
    )


def _get_int(values: Mapping[str, Any], param_name: str) -> Optional[int]:
    value = values.get(param_name)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BadQueryParam(
            f"Invalid value for {param_name!r}: {value!r}. Use an integer."
        )


def _offset_validator(
    page_attr: str, page_size_attr: str, page_param: str, page_size_param: str
) -> Any:
    """A model validator that rejects a page whose SQL offset would not fit in
    a signed 64-bit integer.

    :param page_attr: The field of the page, and ``page_size_attr`` the field
        of the page size.
    :param page_param: The name of the page in the URL, and
        ``page_size_param`` the name of the page size, for the message.
    """

    @pydantic.model_validator(mode="after")
    def _validate_pagination_offset(params: pydantic.BaseModel) -> pydantic.BaseModel:
        page = cast(int, getattr(params, page_attr))
        page_size = cast(int, getattr(params, page_size_attr))
        if page - 1 > _MAX_PAGINATION_OFFSET // page_size:
            raise ValueError(
                f"{page_param} and {page_size_param} produce an offset above "
                f"{_MAX_PAGINATION_OFFSET}"
            )
        return params

    return _validate_pagination_offset


def _build_envelope(
    envelope: type[pydantic.BaseModel], page_info: dict[str, Any]
) -> pydantic.BaseModel:
    """Fill ``envelope`` from ``page_info`` by field name, never by alias, so
    an alias only renames a field in the JSON. Items of another class take
    their values from the attributes of the rows."""
    return envelope.model_validate(
        page_info, from_attributes=True, by_alias=False, by_name=True
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_count(owner: str, name: str, value: Any, *, least: int = 1) -> None:
    if not _is_int(value) or value < least:
        raise RestlyConfigurationError(
            f"{owner}.{name} must be an int of at least {least}, got {value!r}."
        )


def _check_default(owner: str, name: str, value: Any, largest: int) -> None:
    if not _is_int(value) or not 1 <= value <= largest:
        raise RestlyConfigurationError(
            f"{owner}.{name} must be an int in [1, {largest}], got {value!r}. "
            "To return every matching row without pagination, set "
            "'pagination = None' on the view."
        )


def _check_query_param(label: str, name: Any) -> None:
    """Reject a name that cannot be a query parameter of the list params.

    :param label: The setting in messages, such as
        ``NumberedPagination.page_query_param``.
    """
    if not isinstance(name, str) or not name:
        raise RestlyConfigurationError(
            f"{label} must be a non-empty string, got {name!r}."
        )
    if "__" in name or "." in name:
        raise RestlyConfigurationError(
            f"{label} cannot be {name!r}: '__' is reserved for filter operators "
            "and '.' for relation traversal."
        )
    # the list params are a Pydantic model with one field per query
    # parameter; Pydantic drops a field that starts with "_", and one named
    # like a BaseModel attribute breaks or shadows it
    if name.startswith("_") or hasattr(pydantic.BaseModel, name):
        raise RestlyConfigurationError(
            f"{label} cannot be {name!r}: a query parameter cannot start with "
            "'_' or share a name with a Pydantic BaseModel attribute."
        )


def _check_query_params(owner: str, **params: Any) -> None:
    for setting, name in params.items():
        _check_query_param(f"{owner}.{setting}", name)
    if len(set(params.values())) != len(params):
        raise RestlyConfigurationError(
            f"{owner} query parameters must differ, got {params!r}."
        )


def _check_envelope(owner: str, envelope: Any, page_info: dict[str, Any]) -> None:
    if not (isinstance(envelope, type) and issubclass(envelope, pydantic.BaseModel)):
        raise RestlyConfigurationError(
            f"{owner}.envelope must be a Pydantic model class, got {envelope!r}."
        )
    generic = envelope.__pydantic_generic_metadata__
    if generic["origin"] is not None or len(generic["parameters"]) != 1:
        raise RestlyConfigurationError(
            f"{owner}.envelope must be a generic Pydantic model with one type "
            "parameter for the items, such as "
            f"'class Page(BaseModel, Generic[T])', got {envelope.__name__}."
        )
    _check_fillable(f"{owner}.envelope", envelope, page_info)


def _check_fillable(
    label: str,
    envelope: type[pydantic.BaseModel],
    page_info: dict[str, Any],
    other_shape: str = "",
) -> None:
    """Raise if Restly cannot fill ``envelope`` from ``page_info``.

    :param label: The setting in messages, such as
        ``NumberedPagination.envelope``.
    :param other_shape: The last sentence of the message, about what to do for
        a class of another shape.
    """
    # a root, or a validator that runs before the fields, takes the items from
    # the page info itself
    reads_the_page_info = issubclass(envelope, pydantic.RootModel) or any(
        validator.info.mode == "before"
        for validator in envelope.__pydantic_decorators__.model_validators.values()
    )
    if "data" not in envelope.model_fields and not reads_the_page_info:
        raise RestlyConfigurationError(
            f"{label} {envelope.__name__} has no field named data, so it would not "
            f"have the items. Its fields are: {', '.join(envelope.model_fields)}. "
            "Name the field with the items data, and rename it in the JSON with an "
            f"alias.{other_shape}"
        )
    extra = envelope.model_config.get("extra")
    if extra in ("allow", "forbid"):
        raise RestlyConfigurationError(
            f"{label} {envelope.__name__} sets extra={extra!r}. Restly passes an "
            "envelope every page value and it keeps the fields it declares, so "
            f"leave extra at its default.{other_shape}"
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
            f"{label} {envelope.__name__} cannot be filled "
            f"from the page info ({', '.join(page_info)}): {problems}. Name each "
            "field after one of these values, rename it in the JSON with an "
            f"alias, or give it a default.{other_shape}"
        ) from None


#: The pagination of a view or of list params that name none: ``page`` and
#: ``page_size``, 50 rows by default and at most 1000.
_DEFAULT_PAGINATION = NumberedPagination()

#: What ``pagination = None`` means.
_NO_PAGINATION = NoPagination()
