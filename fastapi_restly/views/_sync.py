from contextlib import AbstractContextManager
from typing import Any, cast, final

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
    ReadScope,
    ResponseShape,
    SchemaT,
    UpdateSchemaT,
    delete,
    get,
    patch,
    post,
)
from ._lifecycle import (
    _UNSET,
    _defer_write_action_commit,
    run_write_action,
    sync_write_action,
)


class RestView(BaseRestView[ModelT, SchemaT, CreateSchemaT, UpdateSchemaT, IdT]):
    """
    RestView creates a sync CRUD/REST interface for database objects.
    Basic usage::

        class FooView(RestView):
            prefix = "/foo"
            schema = FooRead
            model = Foo

    Each verb is three tiers (see "Customizing RestView" in the docs): the
    endpoint method ``<verb>_endpoint``, the handler ``handle_<verb>``
    (authorize + commit bracket; final, call it from a custom route), and the
    bare verb ``<verb>`` (the domain operation -- the common override point).
    """

    session: SessionDep

    # ====================================================================
    # Endpoint methods (HTTP contract)
    # ====================================================================

    @get("/")
    def get_many_endpoint(self, query_params: Any) -> Any:
        """``GET /`` endpoint method. Override ``get_many`` for domain
        logic, ``to_response`` for the response shape; replace this method only
        to change the HTTP contract."""
        result = self.handle_get_many(query_params)
        return self.to_response(result, ResponseShape.LISTING)

    @get("/{id}")
    def get_one_endpoint(self, id: Any) -> Any:
        """``GET /{id}`` endpoint method. Override ``get_one`` for domain
        logic (visibility lives in the scope), ``to_response`` for the response
        shape; replace this method only to change the HTTP contract."""
        obj = self.handle_get_one(id)
        return self.to_response(obj)

    @post("/")
    def create_endpoint(self, schema_obj: Any) -> Any:
        """``POST /`` endpoint method. Override ``create`` for domain
        logic (it is commit-free; the handler owns the commit),
        ``to_response`` for the response shape; replace this method only to
        change the HTTP contract."""
        obj = self.handle_create(schema_obj)
        return self.to_response(obj)

    @patch("/{id}")
    def update_endpoint(self, id: Any, schema_obj: Any) -> Any:
        """``PATCH /{id}`` endpoint method. Override ``update`` for
        domain logic, ``to_response`` for the response shape; replace this
        method only to change the HTTP contract."""
        obj = self.handle_update(id, schema_obj)
        return self.to_response(obj)

    @delete("/{id}")
    def delete_endpoint(self, id: Any) -> Any:
        """``DELETE /{id}`` endpoint method. Override ``delete`` for
        domain logic (e.g. soft delete); replace this method only to change the
        HTTP contract (e.g. return the deleted object instead of 204)."""
        self.handle_delete(id)
        return self.to_response(None, ResponseShape.EMPTY)

    # ====================================================================
    # Request handlers (final: call from a custom route, never override)
    # ====================================================================

    @final
    def handle_get_many(
        self, query_params: Any, *, scope: ReadScope = None
    ) -> ListingResult[ModelT]:
        """List handler: ``authorize`` then the ``get_many`` domain op.

        Final, like every handler: override ``get_many`` for the query and
        ``authorize`` for the gate. Call it from a custom listing route.

        :param scope: a clause that replaces the view scope for this read,
            so a custom route can list another surface of the same model
            (a trash listing); ``fr.clauses.UNSCOPED`` reads past it.
            Forwarded to ``get_many``.
        """
        self.authorize(Action.GET_MANY)
        return self.get_many(query_params, scope=scope)

    @final
    def handle_get_one(self, id: IdT, *, scope: ReadScope = None) -> ModelT:
        """Retrieve handler: scoped load (404 by visibility) then read-auth.

        Sync counterpart of :meth:`AsyncRestView.handle_get_one`. Final:
        override ``get_one`` for the load and ``authorize`` for the gate.
        Call it from a custom read route as "load with scope + 404 +
        read-auth". A write action instead loads with ``get_one(id,
        scope=...)`` and gates only its own action, the way ``handle_update``
        and ``handle_delete`` do.

        :param scope: a clause that replaces the view scope for this read,
            so a restore route can load the row the view scope hides.
            Forwarded to ``get_one``.
        """
        obj = self.get_one(id, scope=scope)
        self.authorize(Action.GET_ONE, obj=obj)
        return obj

    def write_action(self, action: str, *, obj: Any = _UNSET, data: Any = None):
        """Run a custom write action through the standard write bracket.

        Use this for non-CRUD actions such as publish or change-password::

            with self.write_action("publish", obj=article):
                article.status = "published"

        For create-shaped actions, omit ``obj`` and set ``w.obj`` before exit.
        Pass ``obj=None`` for writes with no single object. Exceptions skip the
        commit. Inside :meth:`defer_write_action_commit`, the outermost block
        owns the commit and the after-hooks.
        """
        return sync_write_action(self, action, obj=obj, data=data)

    def defer_write_action_commit(self) -> AbstractContextManager[None]:
        """Commit the session once after the outermost block succeeds.

        Sync counterpart of :meth:`AsyncRestView.defer_write_action_commit`::

            with self.defer_write_action_commit():
                for schema_obj in items:
                    self.handle_create(schema_obj)

        Nested blocks on the same session share the commit. Exceptions abort
        the block without rolling back the session. The write handlers and
        ``write_action`` defer their commit and after-hooks. Direct
        ``session.commit()`` calls are not deferred.
        """
        return _defer_write_action_commit(self.session)

    @final
    def handle_create(self, schema_obj: CreateSchemaT) -> ModelT:
        """Create handler: ``authorize``, the ``create`` domain op, commit bracket.

        Final: override ``create`` for the domain change, ``authorize`` for
        the gate, and ``before_action_commit`` / ``after_action_commit`` for
        side effects. Call it from a custom create route, and from inside
        ``defer_write_action_commit()`` to share one commit with other writes.
        """
        return run_write_action(
            self, Action.CREATE, data=schema_obj, mutate=lambda: self.create(schema_obj)
        )

    @final
    def handle_update(self, id: IdT, schema_obj: UpdateSchemaT) -> ModelT:
        """Update handler: scoped load, then ``update`` in the commit bracket.

        Final, like :meth:`handle_create`: ``update`` receives the loaded
        object, so the load, the 404, and ``authorize`` stay here.
        """
        obj = self.get_one(id)
        return run_write_action(
            self,
            Action.UPDATE,
            obj=obj,
            data=schema_obj,
            mutate=lambda: self.update(obj, schema_obj),
        )

    @final
    def handle_delete(self, id: IdT) -> None:
        """Delete handler: scoped load, then ``delete`` in the commit bracket.

        Final, like :meth:`handle_create`: a soft delete flips a timestamp in
        ``delete``, and an off-request follow-up runs in
        ``after_action_commit``.
        """
        obj = self.get_one(id)
        run_write_action(self, Action.DELETE, obj=obj, mutate=lambda: self.delete(obj))

    # ====================================================================
    # Domain operations (auth-free, commit-free) -- the common override point
    # ====================================================================

    def get_many(
        self, query_params: Any, *, scope: ReadScope = None
    ) -> ListingResult[ModelT]:
        query = self._apply_scope(select(self.model), scope)
        query = self.apply_query_params(query, query_params)
        total_count = self.count(query) if self.paginated else None
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        scalar_result = self.session.scalars(query)
        return ListingResult(
            # unique(): collapse the row fan-out a to-many JOIN in the query
            # would produce, so the page never repeats the same entity.
            objects=scalar_result.unique().all(),
            total_count=total_count,
            query_params=query_params,
        )

    def get_one(self, id: IdT, *, scope: ReadScope = None) -> ModelT:
        pk_cols = sa_inspect(self.model).primary_key
        if len(pk_cols) != 1:
            raise NotImplementedError(
                f"{self.model.__name__} has a composite primary key; "
                "override get_one to fetch it."
            )
        query = self._apply_scope(select(self.model), scope).where(pk_cols[0] == id)
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
        """Remove ``obj`` and flush. Does not commit: ``handle_delete`` does.

        Override (on the view or on a soft-delete mixin) to flip a timestamp
        instead of removing the row, without calling ``super()``. A raw row
        delete elsewhere is ``fr.objects.delete_object(self.session, obj)``.
        """
        object_delete_object(self.session, obj)

    # ====================================================================
    # Read seams
    # ====================================================================

    def apply_query_params(
        self, query: sqlalchemy.Select[Any], query_params: Any
    ) -> sqlalchemy.Select[Any]:
        """Apply URL filter/sort/pagination to ``query``."""
        return apply_list_params(query_params, query, self.model, self.schema)

    def count(self, query: sqlalchemy.Select[Any]) -> int:
        """Total for the list, ignoring presentation ordering/pagination.

        Made ``DISTINCT`` before counting so a scope that joins a to-many
        relationship doesn't inflate the total via row fan-out.
        """
        count_source = query.order_by(None).limit(None).offset(None).distinct()
        count_query = select(func.count()).select_from(count_source.subquery())
        return int(self.session.scalar(count_query) or 0)

    # ====================================================================
    # Domain utilities (final: call from a verb override, never override)
    # ====================================================================

    @final
    def make_new_object(self, schema_obj: CreateSchemaT) -> ModelT:
        """Construct a new ORM object and add it to the session (no flush).

        Final: the view-bound spelling of ``fr.objects.make_new_object``,
        passing the view's model and response schema (the schema carries the
        read-only markers). A server-stamped field (an audit id, a tenant
        id) is a column default on the model, which covers every write path;
        a value derived from the payload goes in a ``create`` override, after
        this call.
        """
        model_cls = cast(type[ModelT], self.model)
        return object_make_new_object(self.session, model_cls, schema_obj, self.schema)

    @final
    def update_object(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        """Apply writable fields to ``obj`` (no flush).

        Final, like :meth:`make_new_object`: an ``updated_by`` stamp is the
        column's ``onupdate`` on the model; payload-derived values go in an
        ``update`` override, after this call.
        """
        return object_update_object(self.session, obj, schema_obj, self.schema)

    @final
    def save_object(self, obj: ModelT) -> ModelT:
        """Flush + refresh, eager-loading the relationships the response schema
        names. Does not commit -- ``handle_<verb>`` owns the commit.

        Final: a side effect per write belongs in ``before_action_commit`` /
        ``after_action_commit`` (or a session event, to see the bulk paths
        too), and the reload strategy is ``get_relationship_loader_options``.

        The refresh leaves relationships unloaded, so without the eager load the
        serializer would reach them one lazy query at a time. Reads apply the
        same options in ``get_one`` / ``get_many``.
        """
        obj = object_save_object(self.session, obj)
        statement = self._get_response_reload_statement(obj)
        if statement is not None:
            # unique(): the loader-options seam is public and may return a
            # joinedload against a collection, which fans the row set out.
            self.session.scalars(statement).unique().all()
        return obj

    # ====================================================================
    # Request-logic seams (authorize + transaction hooks)
    # ====================================================================

    def authorize(
        self, action: str, obj: ModelT | None = None, data: Any = None
    ) -> None:
        """Gate a verb. Sync counterpart of :meth:`AsyncRestView.authorize` -- a
        **no-op** by default; override to enforce policy and raise
        ``fr.exc.Forbidden`` / ``fr.exc.NotFound`` to reject. Row *visibility* belongs in
        the scope.
        """

    def before_action_commit(
        self, action: str, new: ModelT | None, old: dict[str, Any] | None = None
    ) -> None:
        """In-transaction side effect (outbox/audit), atomic with the write."""

    def after_action_commit(
        self, action: str, new: ModelT | None, old: dict[str, Any] | None = None
    ) -> None:
        """Post-commit side effect (email, webhook, cache).

        For *external* effects only: the write is already durable, so mutating
        ``new`` or the database here is NOT persisted (and a mutation to ``new``
        leaks into this request's response while being discarded from storage).
        Do the mutation in the business method or ``before_action_commit`` instead.
        """
