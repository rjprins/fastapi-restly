from typing import Any, cast

import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect

from ..db import SessionDep
from ..exc import NotFound
from ..objects import delete_object as object_delete_object
from ..objects import make_new_object as object_make_new_object
from ..objects import save_object as object_save_object
from ..objects import update_object as object_update_object
from ..query import apply_list_params
from ._base import (
    Action,
    BaseRestView,
    CreateSchemaT,
    IdT,
    ListingResult,
    ModelT,
    ResponseShape,
    SchemaT,
    UpdateSchemaT,
    delete,
    get,
    patch,
    post,
)
from ._lifecycle import _UNSET, run_write_action, sync_write_action


class RestView(BaseRestView[ModelT, SchemaT, CreateSchemaT, UpdateSchemaT, IdT]):
    """
    RestView creates a sync CRUD/REST interface for database objects.
    Basic usage::

        class FooView(RestView):
            prefix = "/foo"
            schema = FooRead
            model = Foo

    Each verb is three tiers (see "Customize RestView" in the docs): the
    endpoint method ``<verb>_endpoint``, the handler ``handle_<verb>``
    (authorize + commit bracket), and the bare verb ``<verb>`` (the domain
    operation -- the common override point).
    """

    session: SessionDep

    # ====================================================================
    # Endpoint methods (HTTP contract)
    # ====================================================================

    @get("/")
    def get_many_endpoint(self, query_params: Any) -> Any:
        """``GET /`` endpoint method. Override ``get_many`` for domain
        logic, ``handle_get_many`` for orchestration, ``to_response`` for the
        response shape; replace this method only to change the HTTP contract."""
        self._reject_unknown_query_params()
        result = self.handle_get_many(query_params)
        return self.to_response(result, ResponseShape.LISTING)

    @get("/{id}")
    def get_one_endpoint(self, id: Any) -> Any:
        """``GET /{id}`` endpoint method. Override ``get_one`` for domain
        logic (visibility lives in ``build_query``), ``handle_get_one`` for
        orchestration, ``to_response`` for the response shape; replace this
        method only to change the HTTP contract."""
        obj = self.handle_get_one(id)
        return self.to_response(obj)

    @post("/")
    def create_endpoint(self, schema_obj: Any) -> Any:
        """``POST /`` endpoint method. Override ``create`` for domain
        logic (it is commit-free; the handler owns the commit),
        ``handle_create`` for orchestration, ``to_response`` for the response
        shape; replace this method only to change the HTTP contract."""
        obj = self.handle_create(schema_obj)
        return self.to_response(obj)

    @patch("/{id}")
    def update_endpoint(self, id: Any, schema_obj: Any) -> Any:
        """``PATCH /{id}`` endpoint method. Override ``update`` for
        domain logic, ``handle_update`` for orchestration, ``to_response`` for
        the response shape; replace this method only to change the HTTP
        contract."""
        obj = self.handle_update(id, schema_obj)
        return self.to_response(obj)

    @delete("/{id}")
    def delete_endpoint(self, id: Any) -> Any:
        """``DELETE /{id}`` endpoint method. Override ``delete`` for
        domain logic (e.g. soft delete), ``handle_delete`` for orchestration;
        replace this method only to change the HTTP contract (e.g. return the
        deleted object instead of 204)."""
        self.handle_delete(id)
        return self.to_response(None, ResponseShape.EMPTY)

    # ====================================================================
    # Request handlers (authorize + commit bracket)
    # ====================================================================

    def handle_get_many(self, query_params: Any) -> ListingResult[ModelT]:
        self.authorize(Action.GET_MANY)
        return self.get_many(query_params)

    def handle_get_one(self, id: IdT) -> ModelT:
        obj = self.get_one(id)
        self.authorize(Action.GET_ONE, obj=obj)
        return obj

    def write_action(self, action: str, *, obj: Any = _UNSET, data: Any = None):
        """Run a custom write action through the standard write bracket.

        Use this for non-CRUD actions such as publish or change-password::

            with self.write_action("publish", obj=article):
                article.status = "published"

        For create-shaped actions, omit ``obj`` and set ``w.obj`` before exit.
        Pass ``obj=None`` for writes with no single object. Exceptions skip the
        commit.
        """
        return sync_write_action(self, action, obj=obj, data=data)

    def handle_create(self, schema_obj: CreateSchemaT) -> ModelT:
        return run_write_action(
            self, Action.CREATE, data=schema_obj, mutate=lambda: self.create(schema_obj)
        )

    def handle_update(self, id: IdT, schema_obj: UpdateSchemaT) -> ModelT:
        obj = self.get_one(id)
        return run_write_action(
            self,
            Action.UPDATE,
            obj=obj,
            data=schema_obj,
            mutate=lambda: self.update(obj, schema_obj),
        )

    def handle_delete(self, id: IdT) -> None:
        obj = self.get_one(id)
        run_write_action(self, Action.DELETE, obj=obj, mutate=lambda: self.delete(obj))

    # ====================================================================
    # Domain operations (auth-free, commit-free) -- the common override point
    # ====================================================================

    def get_many(self, query_params: Any) -> ListingResult[ModelT]:
        query = self.build_query()
        query = self.apply_query_params(query, query_params)
        total_count = self.count(query)
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        scalar_result = self.session.scalars(query)
        return ListingResult(
            # unique(): collapse the row fan-out a to-many JOIN in build_query
            # would produce, so the page never repeats the same entity.
            objects=scalar_result.unique().all(),
            total_count=total_count,
            query_params=query_params,
        )

    def get_one(self, id: IdT) -> ModelT:
        pk_cols = sa_inspect(self.model).primary_key
        if len(pk_cols) != 1:
            raise NotImplementedError(
                f"{self.model.__name__} has a composite primary key; "
                "override get_one to fetch it."
            )
        query = self.build_query().where(pk_cols[0] == id)
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        # unique(): parity with get_many. SQLAlchemy documents unique() as
        # required for joined eager loads against collections; only .all()
        # currently enforces it, but the shared loader seam accepts a
        # joinedload-to-many, so both read paths follow the documented
        # contract rather than an enforcement detail.
        obj = self.session.scalars(query).unique().first()
        if obj is None:
            raise NotFound(f"{self.model.__name__} with id {id!r} was not found")
        return cast(ModelT, obj)

    def create(self, schema_obj: CreateSchemaT) -> ModelT:
        obj = self.make_new_object(schema_obj)
        return self.save_object(obj)

    def update(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        obj = self.update_object(obj, schema_obj)
        return self.save_object(obj)

    def delete(self, obj: ModelT) -> None:
        self.delete_object(obj)

    # ====================================================================
    # Read seams
    # ====================================================================

    def build_query(self) -> sqlalchemy.Select[Any]:
        """Return the base SQLAlchemy ``Select`` used by every read -- list,
        count, and retrieve. Override to add ``WHERE`` clauses (tenant scope,
        soft-delete, row-level visibility) that apply to all three.
        """
        return sqlalchemy.select(self.model)

    def apply_query_params(
        self, query: sqlalchemy.Select[Any], query_params: Any
    ) -> sqlalchemy.Select[Any]:
        """Apply URL filter/sort/pagination to ``query``."""
        return apply_list_params(query_params, query, self.model, self.schema)

    def count(self, query: sqlalchemy.Select[Any]) -> int:
        """Total for the list, ignoring presentation ordering/pagination.

        Made ``DISTINCT`` before counting so a ``build_query`` that joins a
        to-many relationship doesn't inflate the total via row fan-out.
        """
        count_source = query.order_by(None).limit(None).offset(None).distinct()
        count_query = select(func.count()).select_from(count_source.subquery())
        return int(self.session.scalar(count_query) or 0)

    # ====================================================================
    # Domain utilities (call from `create`/`update`; not override seams)
    # ====================================================================

    def make_new_object(self, schema_obj: CreateSchemaT) -> ModelT:
        """Construct a new ORM object and add it to the session (no flush).
        Override cooperatively (call ``super()``, then mutate) to stamp
        structural fields like an audit id or a tenant id.
        """
        model_cls = cast(type[ModelT], self.model)
        return object_make_new_object(self.session, model_cls, schema_obj, self.schema)

    def update_object(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        """Apply writable fields to ``obj`` (no flush). Override cooperatively to
        stamp structural fields such as ``updated_by``."""
        return object_update_object(self.session, obj, schema_obj, self.schema)

    def save_object(self, obj: ModelT) -> ModelT:
        """Flush + refresh. Does not commit -- ``handle_<verb>`` owns the commit."""
        return object_save_object(self.session, obj)

    def delete_object(self, obj: ModelT) -> None:
        object_delete_object(self.session, obj)

    # ====================================================================
    # Request-logic seams (authorize + transaction hooks)
    # ====================================================================

    def authorize(
        self, action: str, obj: ModelT | None = None, data: Any = None
    ) -> None:
        """Gate a verb. Sync counterpart of :meth:`AsyncRestView.authorize` -- a
        **no-op** by default; override to enforce policy and raise
        ``fr.exc.Forbidden`` / ``fr.exc.NotFound`` to reject. Row *visibility* belongs in
        ``build_query``.
        """

    def before_commit(
        self, action: str, new: ModelT | None, old: dict[str, Any] | None = None
    ) -> None:
        """In-transaction side effect (outbox/audit), atomic with the write."""

    def after_commit(
        self, action: str, new: ModelT | None, old: dict[str, Any] | None = None
    ) -> None:
        """Post-commit side effect (email, webhook, cache).

        For *external* effects only: the write is already durable, so mutating
        ``new`` or the database here is NOT persisted (and a mutation to ``new``
        leaks into this request's response while being discarded from storage).
        Do the mutation in the business method or ``before_commit`` instead.
        """
