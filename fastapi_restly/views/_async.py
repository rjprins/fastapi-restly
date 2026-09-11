from contextlib import AbstractAsyncContextManager
from typing import Any, cast, final

import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect

from ..db import AsyncSessionDep
from ..exc import NotFound
from ..objects import async_delete_object as object_async_delete_object
from ..objects import async_make_new_object as object_async_make_new_object
from ..objects import async_save_object as object_async_save_object
from ..objects import async_update_object as object_async_update_object
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
    _async_defer_write_action_commit,
    async_run_write_action,
    async_write_action,
)


class AsyncRestView(BaseRestView[ModelT, SchemaT, CreateSchemaT, UpdateSchemaT, IdT]):
    """
    AsyncRestView creates an async CRUD/REST interface for database objects.
    Basic usage::

        class FooView(AsyncRestView):
            prefix = "/foo"
            schema = FooRead
            model = Foo

    Each verb is three tiers (see "Customizing RestView" in the docs):

    * ``<verb>_endpoint`` — the endpoint method. Owns the HTTP signature,
      ``response_model``, and ``to_response``. Rarely overridden.
    * ``handle_<verb>`` — the handler. Owns ``authorize`` and the
      commit bracket (``before_action_commit`` -> commit -> ``after_action_commit``); returns
      the domain object. Reuse from custom actions to get the bracket.
    * ``<verb>`` (``get_many`` / ``get_one`` / ``create`` / ``update`` /
      ``delete``) — the domain operation. Auth-free, commit-free; the common
      override point (hash a password, derive a slug, ...).
    """

    session: AsyncSessionDep

    # ====================================================================
    # Endpoint methods (HTTP contract)
    # ====================================================================

    @get("/")
    async def get_many_endpoint(self, query_params: Any) -> Any:
        """``GET /`` endpoint method. Override ``get_many`` for domain
        logic, ``handle_get_many`` for orchestration, ``to_response`` for the
        response shape; replace this method only to change the HTTP contract."""
        result = await self.handle_get_many(query_params)
        return self.to_response(result, ResponseShape.LISTING)

    @get("/{id}")
    async def get_one_endpoint(self, id: Any) -> Any:
        """``GET /{id}`` endpoint method. Override ``get_one`` for domain
        logic (visibility lives in the scope), ``handle_get_one`` for
        orchestration, ``to_response`` for the response shape; replace this
        method only to change the HTTP contract."""
        obj = await self.handle_get_one(id)
        return self.to_response(obj)

    @post("/")
    async def create_endpoint(self, schema_obj: Any) -> Any:
        """``POST /`` endpoint method. Override ``create`` for domain
        logic (it is commit-free; the handler owns the commit),
        ``handle_create`` for orchestration, ``to_response`` for the response
        shape; replace this method only to change the HTTP contract."""
        obj = await self.handle_create(schema_obj)
        return self.to_response(obj)

    @patch("/{id}")
    async def update_endpoint(self, id: Any, schema_obj: Any) -> Any:
        """``PATCH /{id}`` endpoint method. Override ``update`` for
        domain logic, ``handle_update`` for orchestration, ``to_response`` for
        the response shape; replace this method only to change the HTTP
        contract."""
        obj = await self.handle_update(id, schema_obj)
        return self.to_response(obj)

    @delete("/{id}")
    async def delete_endpoint(self, id: Any) -> Any:
        """``DELETE /{id}`` endpoint method. Override ``delete`` for
        domain logic (e.g. soft delete), ``handle_delete`` for orchestration;
        replace this method only to change the HTTP contract (e.g. return the
        deleted object instead of 204)."""
        await self.handle_delete(id)
        return self.to_response(None, ResponseShape.EMPTY)

    # ====================================================================
    # Request handlers (authorize + commit bracket)
    # ====================================================================

    async def handle_get_many(
        self, query_params: Any, *, scope: ReadScope = None
    ) -> ListingResult[ModelT]:
        """List handler: ``authorize`` then the ``get_many`` domain op.

        :param scope: a clause that replaces the view scope for this read,
            so a custom route can list another surface of the same model
            (a trash listing); ``fr.clauses.UNSCOPED`` reads past it.
            Forwarded to ``get_many``.
        """
        await self.authorize(Action.GET_MANY)
        return await self.get_many(query_params, scope=scope)

    async def handle_get_one(self, id: IdT, *, scope: ReadScope = None) -> ModelT:
        """Retrieve handler: scoped load (404 by visibility) then read-auth.

        Reusable from a custom read route as "load with scope + 404 +
        read-auth". A write action instead loads with ``get_one(id,
        scope=...)`` and gates only its own action, the way
        ``handle_update`` and ``handle_delete`` do.

        :param scope: a clause that replaces the view scope for this read,
            so a restore route can load the row the view scope hides.
            Forwarded to ``get_one``.
        """
        obj = await self.get_one(id, scope=scope)
        await self.authorize(Action.GET_ONE, obj=obj)
        return obj

    def write_action(self, action: str, *, obj: Any = _UNSET, data: Any = None):
        """Run a custom write action through the standard write bracket.

        Use this for non-CRUD actions such as publish or change-password::

            async with self.write_action("publish", obj=article):  # in-place
                article.status = "published"

        For create-shaped actions, omit ``obj`` and set ``w.obj`` before exit::

            async with self.write_action("create", data=req) as w:
                w.obj = await self.make_new_object(req)

        Pass ``obj=None`` for writes with no single object. Exceptions skip the
        commit. Inside :meth:`defer_write_action_commit`, the outermost block
        owns the commit and the after-hooks.
        """
        return async_write_action(self, action, obj=obj, data=data)

    def defer_write_action_commit(self) -> AbstractAsyncContextManager[None]:
        """Commit the session once after the outermost block succeeds.

        ``write_action`` and the write handlers still authorize, snapshot,
        mutate, and run ``before_action_commit``. They flush their changes and
        queue ``after_action_commit`` until this block commits::

            async with self.defer_write_action_commit():
                for schema_obj in items:
                    await self.handle_create(schema_obj)

        Nested blocks on the same session share the commit. An exception
        escaping any block aborts it, including when an enclosing block catches
        that exception. Rollback belongs to the session owner. For partial
        success, put ``session.begin_nested()`` around each inner commit bracket
        and catch the row exception outside its savepoint.

        After-hooks run in queue order and stop on the first exception. ``new``
        is the live object after all writes, while ``old`` is each action's
        snapshot. Direct ``session.commit()`` calls are not deferred.

        Sync actions can join through ``AsyncSession.run_sync()``. Their hooks
        use the same bridge. Async actions require an async outermost block.
        """
        return _async_defer_write_action_commit(self.session)

    async def handle_create(self, schema_obj: CreateSchemaT) -> ModelT:
        return await async_run_write_action(
            self, Action.CREATE, data=schema_obj, mutate=lambda: self.create(schema_obj)
        )

    async def handle_update(self, id: IdT, schema_obj: UpdateSchemaT) -> ModelT:
        obj = await self.get_one(id)
        return await async_run_write_action(
            self,
            Action.UPDATE,
            obj=obj,
            data=schema_obj,
            mutate=lambda: self.update(obj, schema_obj),
        )

    async def handle_delete(self, id: IdT) -> None:
        obj = await self.get_one(id)
        await async_run_write_action(
            self, Action.DELETE, obj=obj, mutate=lambda: self.delete(obj)
        )

    # ====================================================================
    # Domain operations (auth-free, commit-free) -- the common override point
    # ====================================================================

    async def get_many(
        self, query_params: Any, *, scope: ReadScope = None
    ) -> ListingResult[ModelT]:
        """Return the scoped, filtered, paginated page plus the total count.

        Routes through ``scope`` when given, else the view scope
        (:attr:`~fastapi_restly.views.BaseRestView.scope`),
        + :meth:`apply_query_params` (filter/sort/page) + :meth:`count`.
        Auth-free; ``handle_get_many`` adds the ``authorize`` call.

        The handlers always forward ``scope=``, so an override must
        declare the parameter and pass it on to ``super()``.
        """
        query = self._apply_scope(select(self.model), scope)
        query = self.apply_query_params(query, query_params)
        total_count = (await self.count(query)) if self.paginated else None
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        scalar_result = await self.session.scalars(query)
        return ListingResult(
            # unique(): collapse the row fan-out a to-many JOIN in the query
            # would produce, so the page never repeats the same entity.
            objects=scalar_result.unique().all(),
            total_count=total_count,
            query_params=query_params,
        )

    async def get_one(self, id: IdT, *, scope: ReadScope = None) -> ModelT:
        """Load one object through the scope (scope + 404).

        Auth-free: visibility comes from ``scope`` when given, else the
        view scope, so a row outside it is a clean 404 for every caller.
        ``handle_get_one`` adds read-auth; a custom action that needs a
        different auth decision calls this directly with its own scope.

        The handlers always forward ``scope=``, so an override must
        declare the parameter and pass it on to ``super()``.
        """
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
        obj = (await self.session.scalars(query)).unique().first()
        if obj is None:
            raise NotFound(f"{self.model.__name__} with id {id!r} was not found")
        return cast(ModelT, obj)

    async def create(self, schema_obj: CreateSchemaT) -> ModelT:
        """Build a new object and save it. Override from scratch for domain
        logic (e.g. hash a password): never commits, so the bracket can't break.
        """
        obj = await self.make_new_object(schema_obj)
        return await self.save_object(obj)

    async def update(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        """Apply the update payload to ``obj`` and save it."""
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)

    async def delete(self, obj: ModelT) -> None:
        """Remove ``obj`` and flush. Does not commit: ``handle_delete`` does.

        Override (on the view or on a soft-delete mixin) to flip a timestamp
        instead of removing the row, without calling ``super()``. A raw row
        delete elsewhere is ``fr.objects.async_delete_object(self.session, obj)``.
        """
        await object_async_delete_object(self.session, obj)

    # ====================================================================
    # Read seams
    # ====================================================================

    def apply_query_params(
        self, query: sqlalchemy.Select[Any], query_params: Any
    ) -> sqlalchemy.Select[Any]:
        """Apply URL filter/sort/pagination to ``query``. Override for a
        non-default URL grammar; the common case is driven by configuration.
        """
        return apply_list_params(query_params, query, self.model, self.schema)

    async def count(self, query: sqlalchemy.Select[Any]) -> int:
        """Total for the list, ignoring presentation-layer ordering/pagination.

        The stripped query is made ``DISTINCT`` and wrapped as a subquery, so the
        total is correct across user-provided query shapes -- including a scope
        that joins a to-many relationship, whose row fan-out would otherwise
        inflate the count. Override for estimated counts on huge tables.
        """
        count_source = query.order_by(None).limit(None).offset(None).distinct()
        count_query = select(func.count()).select_from(count_source.subquery())
        return int(await self.session.scalar(count_query) or 0)

    # ====================================================================
    # Domain utilities (final: call from a verb override, never override)
    # ====================================================================

    @final
    async def make_new_object(self, schema_obj: CreateSchemaT) -> ModelT:
        """Construct a new ORM object from ``schema_obj`` and add it to the
        session. Does not flush -- :meth:`save_object` does.

        Final: the view-bound spelling of
        ``fr.objects.async_make_new_object``, passing the view's model and
        response schema (the schema carries the read-only markers). A
        server-stamped field (an audit id, a tenant id) is a column default
        on the model, which covers every write path; a value derived from
        the payload goes in a ``create`` override, after this call.
        """
        model_cls = cast(type[ModelT], self.model)
        return await object_async_make_new_object(
            self.session, model_cls, schema_obj, self.schema
        )

    @final
    async def update_object(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        """Apply writable fields from ``schema_obj`` to ``obj``. Does not flush.

        Final, like :meth:`make_new_object`: an ``updated_by`` stamp is the
        column's ``onupdate`` on the model; payload-derived values go in an
        ``update`` override, after this call.
        """
        return await object_async_update_object(
            self.session, obj, schema_obj, self.schema
        )

    @final
    async def save_object(self, obj: ModelT) -> ModelT:
        """Flush the session and refresh ``obj`` from the database, eager-loading
        the relationships the response schema names. Does not commit --
        ``handle_<verb>`` owns the commit.

        Final: a side effect per write belongs in ``before_action_commit`` /
        ``after_action_commit`` (or a session event, to see the bulk paths
        too), and the reload strategy is ``get_relationship_loader_options``.

        The refresh leaves relationships unloaded, so without the eager load the
        serializer would reach them one lazy query at a time -- which on an async
        session is not slow but fatal: a lazy load in the endpoint coroutine has
        no greenlet to suspend into and raises ``MissingGreenlet``. Reads apply
        the same options in ``get_one`` / ``get_many``.
        """
        obj = await object_async_save_object(self.session, obj)
        statement = self._get_response_reload_statement(obj)
        if statement is not None:
            # unique(): the loader-options seam is public and may return a
            # joinedload against a collection, which fans the row set out.
            (await self.session.scalars(statement)).unique().all()
        return obj

    # ====================================================================
    # Request-logic seams (authorize + transaction hooks)
    # ====================================================================

    async def authorize(
        self, action: str, obj: ModelT | None = None, data: Any = None
    ) -> None:
        """Gate a verb. Called by ``handle_<verb>`` at the right phase: before
        the write for ``create``, and after the scoped load for ``update`` /
        ``delete`` / ``get_one`` (so ``obj`` is available for row-level checks).

        The default is a **no-op** -- override to enforce policy, raising
        ``fr.exc.Forbidden`` / ``fr.exc.NotFound`` to reject (``action`` says which verb;
        ``obj`` / ``data`` carry the loaded row and the request payload). Row
        *visibility* -- hiding a row from every caller -- belongs in the
        scope, not here.
        """

    async def before_action_commit(
        self, action: str, new: ModelT | None, old: dict[str, Any] | None = None
    ) -> None:
        """In-transaction side effect (outbox rows, audit rows), committed
        atomically with the write. ``old`` is the pre-mutation snapshot dict.
        """

    async def after_action_commit(
        self, action: str, new: ModelT | None, old: dict[str, Any] | None = None
    ) -> None:
        """Post-commit side effect (email, webhook, cache invalidation). ``old``
        enables dirty detection ("notify only if the status changed").

        For *external* effects only: the write is already durable, so mutating
        ``new`` or the database here is NOT persisted. A mutation to ``new`` also
        leaks into this request's response (which serializes ``new`` after this
        hook) while being silently discarded from storage -- do the mutation in
        the business method or ``before_action_commit`` instead.
        """
