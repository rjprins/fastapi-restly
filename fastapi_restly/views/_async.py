import inspect
from contextlib import AbstractAsyncContextManager
from typing import Any, cast, final

import fastapi
import sqlalchemy
from sqlalchemy import ColumnElement, select

from .._pagination import NumberedPagination
from ..db import AsyncSessionDep
from ..exc import NotFound
from ..objects import async_delete_object as object_async_delete_object
from ..objects import async_make_new_object as object_async_make_new_object
from ..objects import async_save_object as object_async_save_object
from ..objects import async_update_object as object_async_update_object
from ..query._impl import _apply_filters, _apply_sort_and_page, _keep_joins
from ._base import (
    Action,
    BaseRestView,
    CreateSchemaT,
    IdT,
    ListResult,
    ModelT,
    ReadScope,
    ResponseSchemaT,
    ResponseShape,
    UpdateSchemaT,
    _apply_own_filters,
    _check_list_methods,
    _count_statement,
    _identity_criterion,
    _not_found_message,
    _ReadWhere,
    _schema_response,
    delete,
    get,
    patch,
    post,
)
from ._lifecycle import (
    _UNSET,
    _async_shared_write_action_commit,
    async_run_write_action,
    async_write_action,
)
from ._view_types import ViewCreate, ViewID, ViewListResponse, ViewResponse, ViewUpdate


class AsyncRestView(
    BaseRestView[ModelT, ResponseSchemaT, CreateSchemaT, UpdateSchemaT, IdT]
):
    """
    AsyncRestView creates an async CRUD/REST interface for database objects.
    Basic usage::

        class FooView(AsyncRestView):
            prefix = "/foo"
            schema = FooSchema
            model = Foo

    Each CRUD route runs through three methods (see "Request Lifecycle & Hooks"
    in the docs):

    * ``<verb>_endpoint``: the endpoint method. Owns the HTTP signature,
      ``response_model``, and ``to_response``. Override it to change the
      HTTP contract.
    * ``handle_<verb>``: the handler. Handles the steps: ``authorize``, the
      business method, and the commit bracket (``before_action_commit`` ->
      commit -> ``after_action_commit``); returns the domain object. Final:
      call it from a custom route to run the same steps, never override it.
    * ``<verb>`` (``get_many`` / ``get_one`` / ``create`` / ``update`` /
      ``delete``): the business method. Auth-free, commit-free; the common
      override point (hash a password, ...).
    """

    session: AsyncSessionDep

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _check_list_methods(cls, is_async=True)

    # ====================================================================
    # Endpoint methods (HTTP contract)
    # ====================================================================

    @get("/")
    async def get_many_endpoint(self, list_params: Any) -> ViewListResponse:
        """``GET /`` endpoint method. Override ``get_many`` for domain
        logic, ``to_response`` for the response shape; replace this method only
        to change the HTTP contract."""
        result = await self.handle_get_many(list_params)
        return self.to_response(result, ResponseShape.LIST)

    @get("/{id}")
    async def get_one_endpoint(self, id: ViewID) -> ViewResponse:
        """``GET /{id}`` endpoint method. Override ``get_one`` for domain
        logic (visibility lives in the scope), ``to_response`` for the response
        shape; replace this method only to change the HTTP contract."""
        obj = await self.handle_get_one(id)
        return self.to_response(obj)

    @post("/")
    async def create_endpoint(self, schema_obj: ViewCreate) -> ViewResponse:
        """``POST /`` endpoint method. Override ``create`` for domain
        logic (it is commit-free; the handler owns the commit),
        ``to_response`` for the response shape; replace this method only to
        change the HTTP contract."""
        obj = await self.handle_create(schema_obj)
        return self.to_response(obj)

    @patch("/{id}")
    async def update_endpoint(self, id: ViewID, schema_obj: ViewUpdate) -> ViewResponse:
        """``PATCH /{id}`` endpoint method. Override ``update`` for
        domain logic, ``to_response`` for the response shape; replace this
        method only to change the HTTP contract."""
        obj = await self.handle_update(id, schema_obj)
        return self.to_response(obj)

    @delete("/{id}")
    async def delete_endpoint(self, id: ViewID) -> fastapi.Response:
        """``DELETE /{id}`` endpoint method. Override ``delete`` for
        domain logic (e.g. soft delete); replace this method only to change the
        HTTP contract (e.g. return the deleted object instead of 204)."""
        await self.handle_delete(id)
        return self.to_response(None, ResponseShape.EMPTY)

    # ====================================================================
    # Handlers (final: call from a custom route, never override)
    # ====================================================================

    @final
    async def handle_get_many(
        self, list_params: Any, *, scope: ReadScope = None, where: _ReadWhere = None
    ) -> ListResult[ModelT]:
        """List handler: ``authorize`` then the ``get_many`` business method.

        Final, like every handler: override ``get_many`` for the query and
        ``authorize`` for the gate. Call it from a custom list route.

        :param scope: a clause that replaces the view scope for this read,
            so a custom route can list another surface of the same model
            (a trash list); ``fr.clauses.UNSCOPED`` reads past it.
            Forwarded to ``get_many``.
        :param where: a SQLAlchemy boolean expression or a clause that
            narrows this read inside the scope, so a nested list
            keeps the visibility rules (``where=Task.project_id == id``).
            It is ANDed into the scope as ``fr.all_of`` would, and
            ``get_many`` receives the result as ``scope``. The page and
            ``total_count`` both apply it.
        :raises TypeError: ``where`` is a Python bool (a comparison on a
            loaded object), or neither an expression nor a clause.
        """
        await self.authorize(Action.GET_MANY)
        return await self.get_many(
            list_params, scope=self._narrowed_scope(scope, where)
        )

    @final
    async def handle_get_one(
        self, id: IdT | ColumnElement[bool], *, scope: ReadScope = None
    ) -> ModelT:
        """Retrieve handler: scoped load (404 by visibility) then read-auth.

        Final: override ``get_one`` for the load and ``authorize`` for the
        gate. Call it from a custom read route as "load with scope + 404 +
        read-auth". A write action instead loads with ``get_one(id,
        scope=...)`` and gates only its own action, the way
        ``handle_update`` and ``handle_delete`` do.

        :param id: the primary key, or a SQLAlchemy boolean expression
            that picks the row instead, so a natural-key route is this
            handler with ``Item.slug == slug``. Forwarded to ``get_one``.
        :param scope: a clause that replaces the view scope for this read,
            so a restore route can load the row the view scope hides.
            Forwarded to ``get_one``.
        """
        obj = await self.get_one(id, scope=scope)
        await self.authorize(Action.GET_ONE, obj=obj)
        return obj

    def write_action(self, action: str, *, obj: Any = _UNSET, data: Any = None):
        """Run a custom write action through the commit bracket.

        Use this for non-CRUD actions such as publish or change-password::

            async with self.write_action("publish", obj=article):  # in-place
                article.status = "published"

        For create-shaped actions, omit ``obj`` and set ``w.obj`` before exit::

            async with self.write_action("create", data=req) as w:
                w.obj = await self.make_new_object(req)

        Pass ``obj=None`` for writes with no single object. After
        ``authorize``, the block checks the references in ``data``, as a CRUD
        write does: the ``MustExist``, ``IDRef`` and ``IDSchema`` fields, also
        in nested schemas and in each item of a list. A missing row raises
        :class:`~fastapi_restly.exc.NotFound` (404). Exceptions skip the
        commit. The outermost block on the session owns the commit and the
        after-hooks. So a handler or a ``write_action`` inside this block, or
        this block inside :meth:`shared_write_action_commit`, returns after
        its flush, before either one. An ``IntegrityError`` from ``authorize``
        to the commit becomes :class:`~fastapi_restly.exc.Conflict` (409)
        when it leaves the outermost block, and stays raw inside it. A direct
        ``session.commit()`` inside the block raises ``RuntimeError``.
        """
        return async_write_action(self, action, obj=obj, data=data)

    def shared_write_action_commit(self) -> AbstractAsyncContextManager[None]:
        """Share one commit across write actions on this session.

        The outermost block commits once, then runs the queued
        ``after_action_commit`` hooks. ``write_action`` and the write handlers
        still authorize, snapshot, mutate, run ``before_action_commit``, and
        flush. They return uncommitted objects inside the block::

            async with self.shared_write_action_commit():
                for schema_obj in items:
                    await self.handle_create(schema_obj)

        Serialize returned objects and run code that depends on an after-hook
        only after the block exits. Nested blocks on the same session share the
        commit. An exception escaping a deferred-commit block aborts the shared
        commit, including when an enclosing deferred-commit block catches that
        exception. Rollback belongs to the session owner. An
        ``IntegrityError`` inside the block stays raw, so a route can catch the
        error of one row; one that escapes the outermost block, or that its
        commit raises, becomes :class:`~fastapi_restly.exc.Conflict` (409).

        After-hooks run in queue order and stop on the first exception. ``new``
        is the live object after all writes, while ``old`` is each action's
        snapshot. Direct ``session.commit()`` calls raise ``RuntimeError``.
        An async write action needs an async outermost block; a sync one
        joins either.
        """
        return _async_shared_write_action_commit(self.session)

    @final
    async def handle_create(self, schema_obj: CreateSchemaT) -> ModelT:
        """Create handler: ``authorize``, the reference check, ``create``, commit bracket.

        Final: override ``create`` for the domain change, ``authorize`` for
        the gate, and ``before_action_commit`` / ``after_action_commit`` for
        side effects. Call it from a custom create route, and from inside
        ``shared_write_action_commit()`` to share one commit with other
        writes; it then returns after the flush, before the commit.
        """
        return await async_run_write_action(
            self, Action.CREATE, data=schema_obj, mutate=lambda: self.create(schema_obj)
        )

    @final
    async def handle_update(
        self, id: IdT | ColumnElement[bool], schema_obj: UpdateSchemaT
    ) -> ModelT:
        """Update handler: scoped load, then ``update`` in the commit bracket.

        Final, like :meth:`handle_create`: ``update`` receives the loaded
        object, so the load, the 404, and ``authorize`` stay here. Inside
        ``shared_write_action_commit()`` it returns before the commit.
        """
        obj = await self.get_one(id)
        return await async_run_write_action(
            self,
            Action.UPDATE,
            obj=obj,
            data=schema_obj,
            mutate=lambda: self.update(obj, schema_obj),
        )

    @final
    async def handle_delete(self, id: IdT | ColumnElement[bool]) -> None:
        """Delete handler: scoped load, then ``delete`` in the commit bracket.

        Final, like :meth:`handle_create`: a soft delete flips a timestamp in
        ``delete``, and an off-request follow-up runs in
        ``after_action_commit``. Inside ``shared_write_action_commit()``, the
        mutation runs immediately. The after-hook waits for the outermost
        block to commit.
        """
        obj = await self.get_one(id)
        await async_run_write_action(
            self, Action.DELETE, obj=obj, mutate=lambda: self.delete(obj)
        )

    # ====================================================================
    # Business methods (auth-free, commit-free): the common override point
    # ====================================================================

    async def get_many(
        self, list_params: Any, *, scope: ReadScope = None
    ) -> ListResult[ModelT]:
        """List the rows the scope allows, filtered and paged by ``list_params``.

        Auth-free: ``handle_get_many`` adds ``authorize``. The query is the
        resolved scope (``fr.resolve_scope(self)``, or ``scope`` when given)
        plus :meth:`apply_list_params`. A paginated view also runs
        :meth:`count` for ``total_count``; a view without pagination
        returns every matching row with ``total_count=None``. Relationships
        the response class names are eager-loaded.

        The handlers always forward ``scope=``, so an override must declare
        the parameter and pass it on to ``super()``. A route's ``where=``
        arrives inside ``scope``: ``handle_get_many`` folds it in, and there
        is no separate parameter to declare.

        :param list_params: the list params (filter, sort, page) as the
            endpoint receives them. A ``QueryParams`` or a mapping goes
            through
            :attr:`~fastapi_restly.views.BaseRestView.schema_list_params`
            first, as on the list route.
        :param scope: a clause that replaces the resolved scope for this
            read; ``fr.clauses.UNSCOPED`` reads unscoped.
        """
        # once here, so an override of apply_list_params and the response
        # get the list params as the list route gives them
        list_params = self._validated_list_params(list_params)
        query = self._apply_scope(select(self.model), scope)
        query = await self.apply_list_params(query, list_params)
        total_count = (
            (await self.count(query))
            if isinstance(self.pagination, NumberedPagination)
            else None
        )
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        scalar_result = await self.session.scalars(query)
        return ListResult(
            # unique(): a joined eager load of a collection, from the loader
            # options, repeats the row for each child
            objects=scalar_result.unique().all(),
            total_count=total_count,
            list_params=list_params,
        )

    async def get_one(
        self, id: IdT | ColumnElement[bool], *, scope: ReadScope = None
    ) -> ModelT:
        """Load the one row ``id`` names, through the scope, or raise 404.

        Auth-free: ``handle_get_one`` adds ``authorize``; a write action
        calls this directly and gates its own action. Visibility is the
        resolved scope (``fr.resolve_scope(self)``, or ``scope`` when
        given), so a row outside it is a 404 for every caller.
        Relationships the response class names are eager-loaded.

        The handlers always forward ``scope=``, so an override must declare
        the parameter and pass it on to ``super()``.

        :param id: the primary key, or a SQLAlchemy boolean expression that
            picks the row instead (``get_one(Item.slug == slug)``), which
            is also how a composite key is addressed. A criterion narrows
            inside the scope; only ``scope=`` replaces it.
        :param scope: a clause that replaces the resolved scope for this
            read; ``fr.clauses.UNSCOPED`` reads unscoped.
        :raises fastapi_restly.exc.NotFound: no row matches inside the
            scope. The message names an id, never a predicate.
        :raises sqlalchemy.exc.MultipleResultsFound: more than one row
            matches; an ambiguous key is a bug in the criterion.
        :raises TypeError: ``id`` is a Python bool (a comparison on a
            loaded object, which would render as ``WHERE true``), an
            uncalled clause (a scope, not a row identity), or an expression
            on a table that the query does not select from.
        :raises NotImplementedError: a plain id on a composite primary
            key; pass a predicate.
        """
        query = self._apply_scope(select(self.model), scope).where(
            _identity_criterion(self.model, id)
        )
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        # unique(): the public loader-options seam may return a joined eager
        # load against a collection, and that row fan-out would otherwise
        # read as several matches for one entity.
        obj = (await self.session.scalars(query)).unique().one_or_none()
        if obj is None:
            raise NotFound(_not_found_message(self.model, id))
        return cast(ModelT, obj)

    async def create(self, schema_obj: CreateSchemaT) -> ModelT:
        """Build a new object from ``schema_obj`` and save it.

        Auth-free and commit-free: ``handle_create`` owns both. The usual
        create override point (hash a password from the request), written as
        :meth:`make_new_object`, the extra step, then :meth:`save_object`.
        """
        obj = await self.make_new_object(schema_obj)
        return await self.save_object(obj)

    async def update(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        """Apply ``schema_obj`` to the loaded ``obj`` and save it.

        Auth-free and commit-free: ``handle_update`` loads ``obj`` through
        ``get_one``, gates, and commits. The usual update override point,
        written as :meth:`update_object`, the extra step, then
        :meth:`save_object`.
        """
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

    async def apply_list_params(
        self, query: sqlalchemy.Select[Any], list_params: Any
    ) -> sqlalchemy.Select[Any]:
        """Apply the list params to ``query``: :meth:`apply_filters`, then the
        sort, then the page.

        Async, as :meth:`apply_filters` is: call it with ``await``. Override
        for list params of your own; the common case is driven by
        configuration. A ``QueryParams`` or a mapping goes through
        :attr:`~fastapi_restly.views.BaseRestView.schema_list_params` first,
        as on the list route. In a ``QueryParams``, a key that the route
        reads is left out. Any other key that the list params do not have
        raises ``RequestValidationError``, as on the list route (see
        :attr:`~fastapi_restly.views.BaseRestView.unknown_query_params`).
        """
        list_params = self._validated_list_params(list_params)
        query = await self.apply_filters(query, list_params)
        return _apply_sort_and_page(
            query,
            list_params,
            self.model,
            _schema_response(type(self)),
            pagination=self.pagination,
        )

    async def apply_filters(
        self, stmt: sqlalchemy.Select[Any], list_params: Any
    ) -> sqlalchemy.Select[Any]:
        """Apply the filters of ``list_params`` to ``stmt``, without the sort
        and the page.

        For a route that reads the client's filters but has its own order or
        no rows to page, such as a count per category with ``GROUP BY``.
        Like every custom read route, such a route calls :meth:`authorize`
        and applies the scope itself. :meth:`apply_list_params` calls this
        method first. A ``QueryParams`` or a mapping goes through
        :attr:`~fastapi_restly.views.BaseRestView.schema_list_params` first,
        as on the list route. In a ``QueryParams``, a key that the route
        reads is left out. Any other key that the list params do not have
        raises ``RequestValidationError``, as on the list route (see
        :attr:`~fastapi_restly.views.BaseRestView.unknown_query_params`).

        The generated filters come first, then the view's own filters from
        :func:`~fastapi_restly.views.list_filter`, in the order of the class.
        """
        list_params = self._validated_list_params(list_params)
        filtered = _apply_filters(
            stmt,
            list_params,
            self.model,
            _schema_response(type(self)),
            pagination=self.pagination,
        )
        results: list[tuple[str, Any]] = []
        for own, value in self._filter_method_values(list_params):
            result = getattr(self, own.method)(value)
            if inspect.isawaitable(result):
                result = await result
            results.append((f"{type(self).__name__}.{own.method}", result))
        return _keep_joins(filtered, _apply_own_filters(filtered, results))

    async def count(self, query: sqlalchemy.Select[Any]) -> int:
        """Total for the list, ignoring presentation-layer ordering/pagination.

        It counts the distinct primary keys of the model that ``query``
        selects, without its order and page. So a join to a to-many
        relationship does not count a row twice. No other column is part of
        the count, so a ``json`` column works on PostgreSQL, and the subquery
        of a ``column_property`` does not run for every row. A ``query`` that
        selects columns, not a model, counts its distinct rows. Override for
        estimated counts on huge tables.
        """
        return int((await self.session.scalar(_count_statement(query))) or 0)

    # ====================================================================
    # Domain utilities (final: call from a verb override, never override)
    # ====================================================================

    @final
    async def make_new_object(self, schema_obj: CreateSchemaT) -> ModelT:
        """Construct a new ORM object from ``schema_obj`` and add it to the
        session. Does not flush; :meth:`save_object` does.

        Final: the view-bound spelling of ``fr.objects.async_make_new_object``,
        passing the view's model. ``schema_obj``'s own schema decides what is
        written, not the view's schema. A value that every write path must
        set, such as an audit id, a tenant id or a slug from the title, belongs
        on the model: a column default or a mapper event. A value that only the
        request sets, such as a hashed password, goes in a ``create`` override,
        after this call.
        """
        model_cls = cast(type[ModelT], self.model)
        return await object_async_make_new_object(self.session, model_cls, schema_obj)

    @final
    async def update_object(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        """Apply writable fields from ``schema_obj`` to ``obj``. Does not flush.

        Final, like :meth:`make_new_object`: an ``updated_by`` stamp is the
        column's ``onupdate`` on the model; a value that only the request sets
        goes in an ``update`` override, after this call.
        """
        return await object_async_update_object(self.session, obj, schema_obj)

    @final
    async def save_object(self, obj: ModelT) -> ModelT:
        """Flush the session and refresh ``obj`` from the database, eager-loading
        the relationships the response class names. Does not commit;
        ``handle_<verb>`` owns the commit.

        Final: a side effect per write belongs in ``before_action_commit`` /
        ``after_action_commit`` (or a session event, to see the bulk paths
        too), and the reload strategy is ``get_relationship_loader_options``.

        The refresh leaves relationships unloaded, so without the eager load the
        serializer would reach them one lazy query at a time, which on an async
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

        The default is a **no-op**. Override to enforce policy, raising
        ``fr.exc.Forbidden`` / ``fr.exc.NotFound`` to reject (``action`` says
        which verb; ``obj`` / ``data`` carry the loaded row and the request
        payload). Row *visibility*, hiding a row from every caller, belongs in
        the scope, not here.
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
        hook) while being silently discarded from storage. Do the mutation in
        the business method or ``before_action_commit`` instead.

        An exception here cannot undo the committed write, yet it fails the
        request, so a client that retries repeats the write. Catch and log the
        errors of a best-effort effect here; add an effect that must happen as
        an outbox row in ``before_action_commit``.
        """
