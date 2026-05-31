from typing import Any, cast

import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect

from ..db import SessionDep
from ..exceptions import NotFound
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

    Each verb is three tiers (see "the handle design" in the docs): the
    ``<verb>_endpoint`` route shell, the ``handle_<verb>`` request handler
    (authorize + commit bracket), and the bare verb ``<verb>`` (the domain
    operation -- the common override point).
    """

    session: SessionDep

    # ====================================================================
    # Route shells (wire boundary)
    # ====================================================================

    @get("/")
    def get_many_endpoint(self, query_params: Any) -> Any:
        self._reject_unknown_query_params()
        result = self.handle_get_many(query_params)
        return self.to_response(result, ResponseShape.LISTING)

    @get("/{id}")
    def get_one_endpoint(self, id: Any) -> Any:
        obj = self.handle_get_one(id)
        return self.to_response(obj)

    @post("/")
    def create_endpoint(self, schema_obj: Any) -> Any:
        obj = self.handle_create(schema_obj)
        return self.to_response(obj)

    @patch("/{id}")
    def update_endpoint(self, id: Any, schema_obj: Any) -> Any:
        obj = self.handle_update(id, schema_obj)
        return self.to_response(obj)

    @delete("/{id}")
    def delete_endpoint(self, id: Any) -> Any:
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
        """Run a custom write *action* through the full request bracket.

        Used as a context manager from a custom write route: it runs
        ``authorize`` + ``snapshot`` on entry, your body mutates the object, then
        ``before_commit`` -> commit -> ``after_commit`` on exit. Reach for it when
        an action is not a plain create/update/delete; CRUD-shaped logic belongs
        in the ``create`` / ``update`` / ``delete`` overrides instead::

            with self.write_action("publish", obj=article):
                article.status = "published"

        For a create-shaped action (no ``obj=``), deposit the new object on the
        handle (``as w: w.obj = ...``); forgetting that deposit raises
        ``RuntimeError`` on exit instead of silently committing the row. Pass
        ``obj=None`` for a write with no single object. A raise inside the block
        skips the commit.
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
            objects=scalar_result.all(),
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
        obj = self.session.scalars(query).first()
        if obj is None:
            raise NotFound(
                f"{self.model.__name__} with id {id!r} was not found"
            )
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
        """Total for the list, ignoring presentation ordering/pagination."""
        count_source = query.order_by(None).limit(None).offset(None)
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
        ``fr.Forbidden`` / ``fr.NotFound`` to reject. Row *visibility* belongs in
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
        Do the mutation in the business verb or ``before_commit`` instead.
        """

    def _commit(self) -> None:
        """Commit the current transaction. The handle design makes this the
        single commit point for a write request. No-op only when the caller
        opted out via ``commit_session_on_response=False`` (then you own the
        commit). A custom session generator manages close/rollback, but the
        handler still owns the commit -- set ``commit_session_on_response=False``
        if the generator manages its own transaction boundary.
        """
        from ..db._globals import _fr_globals

        if _fr_globals.commit_session_on_response:
            self.session.commit()
