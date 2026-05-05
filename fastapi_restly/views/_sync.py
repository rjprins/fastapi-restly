from collections.abc import Sequence
from typing import Any, TypeVar, cast

import fastapi
import pydantic
import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Session

from ..db import SessionDep
from ..query import apply_list_params
from ..schemas._base import _resolve_ids_to_sqlalchemy_objects
from ._base import (
    BaseRestView,
    CreateSchemaT,
    IdT,
    ModelT,
    SchemaT,
    UpdateSchemaT,
    apply_create_assignments,
    apply_update_to_object,
    build_create_plan,
    delete,
    get,
    patch,
    post,
    validate_resolved_reference_consistency,
)

T = TypeVar("T", bound=DeclarativeBase)


def make_new_object(
    session: Session,
    model_cls: type[T],
    schema_obj: pydantic.BaseModel,
    schema_cls: type[pydantic.BaseModel] | None = None,
) -> T:
    """Create a new instance of ``model_cls`` from ``schema_obj`` and add it to
    ``session``. Read-only fields and any unset fields' defaults are handled by
    the shared helper. The session is not flushed here.

    See also: :func:`async_make_new_object` for the async equivalent.
    """
    _resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    validate_resolved_reference_consistency(model_cls, schema_obj, schema_cls)
    create_plan = build_create_plan(model_cls, schema_obj, schema_cls)
    obj = model_cls(**create_plan.kwargs)
    apply_create_assignments(obj, create_plan.post_assignments)
    session.add(obj)
    return obj


def update_object(
    session: Session,
    obj: DeclarativeBase,
    schema_obj: pydantic.BaseModel,
    schema_cls: type[pydantic.BaseModel] | None = None,
) -> DeclarativeBase:
    """Apply writable inputs from ``schema_obj`` onto ``obj``. Only fields the
    caller explicitly set are applied; read-only fields are skipped.

    See also: :func:`async_update_object` for the async equivalent.
    """
    _resolve_ids_to_sqlalchemy_objects(session, schema_obj)
    validate_resolved_reference_consistency(type(obj), schema_obj, schema_cls)
    apply_update_to_object(obj, schema_obj, schema_cls)
    return obj


def save_object(session: Session, obj: DeclarativeBase) -> DeclarativeBase:
    """Flush the session and refresh ``obj`` so its server-side defaults and
    generated columns (PKs, timestamps, etc.) are populated.

    See also: :func:`async_save_object` for the async equivalent.
    """
    session.flush()
    session.refresh(obj)
    return obj


class RestView(BaseRestView[ModelT, SchemaT, CreateSchemaT, UpdateSchemaT, IdT]):
    """
    RestView creates a synchronous CRUD/REST interface for database objects.
    Basic usage::

        class FooView(RestView):
            prefix = "/foo"
            schema = FooRead
            model = Foo

    Where ``Foo`` is a SQLAlchemy model and ``FooRead`` a Pydantic model.
    """

    session: SessionDep  # type: ignore[reportIncompatibleVariableOverride]

    @get("/")
    def list(self, query_params: Any) -> Any:
        self._reject_unknown_query_params()
        objs = self.perform_list(query_params)
        if not self.include_pagination_metadata:
            return [self.to_response_schema(obj) for obj in objs]

        total = self.count_listing(query_params)
        return self._build_pagination_payload(query_params, objs, total)

    def build_query(self) -> sqlalchemy.Select[Any]:
        """
        Return the base SQLAlchemy ``Select`` used by every read on this
        view's model — listing, count, and retrieve. Override to add
        ``WHERE`` clauses that should apply to all of them — e.g. tenant
        scoping, soft-delete filtering, row-level permission visibility.
        Call ``super().build_query()`` and chain ``.where(...)`` to compose
        with any base-class or mixin filters.

        Because retrieve also routes through this query, a row hidden from
        listing cannot be fetched directly via ``GET /{id}`` — visibility
        stays consistent across endpoints by construction.
        """
        return sqlalchemy.select(self.model)

    def perform_list(
        self, query_params: Any, query: sqlalchemy.Select[Any] | None = None
    ) -> Sequence[ModelT]:
        """
        Handle a GET request on "/". This should return a list of objects.
        Accepts a query argument that can be used for narrowing down the selection.
        Feel free to override this method, e.g.:

            def perform_list(self, query_params, query=None):
                query = make_my_query()
                objs = super().perform_list(query_params, query)
                return add_my_info(objs)

        ``query_params`` is the validated query-parameter Pydantic model
        injected by FastAPI; pagination bounds (``page`` / ``page_size``)
        have already been validated by the schema returned from
        :func:`fastapi_restly.query.create_list_params_schema`.

        For WHERE-clause-only filtering that should also apply to the
        pagination total *and* to retrieve, override :meth:`build_query`
        instead.
        """
        if query is None:
            query = self.build_query()
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        query = apply_list_params(query_params, query, self.model, self.schema)
        scalar_result = self.session.scalars(query)
        return scalar_result.all()

    def count_listing(self, query_params: Any) -> int:
        filtered_query = apply_list_params(
            query_params, self.build_query(), self.model, self.schema
        )
        filtered_query = filtered_query.order_by(None).limit(None).offset(None)
        count_query = select(func.count()).select_from(filtered_query.subquery())
        return int(self.session.scalar(count_query) or 0)

    @get("/{id}")
    def get(self, id: Any) -> Any:
        obj = self.perform_get(id)
        return self.to_response_schema(obj)

    def perform_get(self, id: IdT) -> ModelT:
        """
        Handle a GET request on "/{id}". This should return a single object.
        Return a 404 if not found.

        Routes through :meth:`build_query`, so any read-side filters layered
        there (tenant scoping, soft-delete, row-level permissions) apply to
        retrieve as well — a row hidden from listing returns 404 here too,
        without a separate post-fetch guard.
        """
        pk_cols = sa_inspect(self.model).primary_key
        if len(pk_cols) != 1:
            raise NotImplementedError(
                f"{self.model.__name__} has a composite primary key; "
                "override perform_get to fetch it."
            )
        query = self.build_query().where(pk_cols[0] == id)
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        obj = self.session.scalars(query).first()
        if obj is None:
            raise fastapi.HTTPException(
                status_code=404,
                detail=f"{self.model.__name__} with id {id!r} was not found",
            )
        return cast(ModelT, obj)

    @post("/")
    def create(self, schema_obj: Any) -> Any:
        obj = self.perform_create(schema_obj)
        return self.to_response_schema(obj)

    def perform_create(self, schema_obj: CreateSchemaT) -> ModelT:
        """
        Handle a POST request on "/". This should create a new object.
        Feel free to override this method.
        """
        obj = self.make_new_object(schema_obj)
        obj = self.save_object(obj)
        return obj

    @patch("/{id}")
    def update(self, id: Any, schema_obj: Any) -> Any:
        obj = self.perform_update(id, schema_obj)
        return self.to_response_schema(obj)

    def perform_update(self, id: IdT, schema_obj: UpdateSchemaT) -> ModelT:
        """
        Handle a PATCH request on "/{id}". This should partially update an existing
        object.
        Feel free to override this method.
        """
        obj = self.perform_get(id)
        obj = self.update_object(obj, schema_obj)
        return self.save_object(obj)

    @delete("/{id}")
    def delete(self, id: Any) -> fastapi.Response:
        return self.perform_delete(id)

    def perform_delete(self, id: IdT) -> fastapi.Response:
        obj = self.perform_get(id)
        self.delete_object(obj)
        return fastapi.Response(status_code=204)

    def delete_object(self, obj: ModelT) -> None:
        """
        Delete ``obj`` and flush the session.

        ``perform_delete`` calls ``perform_get`` first, so this method receives an
        existing object. Override it to change the deletion mechanics, for
        example to implement soft-delete.
        """
        self.session.delete(obj)
        self.session.flush()

    def make_new_object(self, schema_obj: CreateSchemaT) -> ModelT:
        """
        Build a new ORM object from ``schema_obj`` and add it to the session.

        This does not flush. The default ``perform_create`` calls
        ``save_object`` afterwards; override this method for construction-time
        changes that must happen before that save boundary.
        """
        model_cls = cast(type[ModelT], self.model)
        return make_new_object(self.session, model_cls, schema_obj, self.schema)

    def update_object(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        """
        Apply writable fields from ``schema_obj`` to ``obj``.

        This does not flush. The default ``perform_update`` calls
        ``save_object`` afterwards; override this method for update-time changes
        that must happen before that save boundary.
        """
        return cast(ModelT, update_object(self.session, obj, schema_obj, self.schema))

    def save_object(self, obj: ModelT) -> ModelT:
        """
        Flush the session and refresh ``obj`` from the database.

        This is the explicit persistence boundary used by the default create and
        update handlers. Override it for behavior that should run after every
        successful create/update flush.
        """
        return cast(ModelT, save_object(self.session, obj))
