from contextlib import AbstractContextManager
from typing import Any, cast, final

import fastapi
import sqlalchemy
from sqlalchemy import ColumnElement

from .._pagination import NumberedPagination
from ..db import SessionDep
from ..objects import make_new_object as object_make_new_object
from ..objects import update_object as object_update_object
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
    _ReadWhere,
    _schema_response,
    delete,
    get,
    patch,
    post,
)
from ._lifecycle import (
    _bracket,
    _require_result,
    _shared_write_action_commit,
    sync_write_action,
)
from ._view_types import ViewCreate, ViewID, ViewListResponse, ViewResponse, ViewUpdate


class RestView(
    BaseRestView[ModelT, ResponseSchemaT, CreateSchemaT, UpdateSchemaT, IdT]
):
    """
    RestView creates a sync CRUD/REST interface for database objects.
    Basic usage::

        class FooView(RestView):
            prefix = "/foo"
            schema = FooSchema
            model = Foo

    Each CRUD route runs through three methods (see "Request Lifecycle & Hooks"
    in the docs):

    * ``<verb>_endpoint``: the endpoint method. Owns the HTTP signature,
      ``response_model``, and ``to_response``. Override it to change the
      HTTP contract.
    * ``handle_<verb>``: the handler. Handles the steps: ``authorize``, the
      load or the business method, and the commit bracket
      (``before_action_commit`` -> commit -> ``after_action_commit``);
      returns the domain object. Final: call it from a custom route to run
      the same steps, never override it.
    * ``create`` / ``update`` / ``delete``: the business method. Auth-free,
      commit-free; the common override point (hash a password, ...). It
      changes objects in the session and does not flush: the commit bracket
      saves.

    ``get_one`` and ``get_many`` are the loads. They are final, as the
    handlers are, and start from :meth:`~fastapi_restly.views.BaseRestView.select`: the scope and the loader
    options. Change a load through ``scope``, ``where_id``, ``not_found``,
    ``apply_list_params`` and ``get_loader_options``.
    """

    session: SessionDep

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _check_list_methods(cls, is_async=False)

    # ====================================================================
    # Endpoint methods (HTTP contract)
    # ====================================================================

    @get("/")
    def get_many_endpoint(self, list_params: Any) -> ViewListResponse:
        """``GET /`` endpoint method. Put the rows in ``scope``, the list
        params in ``apply_list_params``, and the response shape in
        ``to_response``; replace this method only to change the HTTP
        contract."""
        result = self.handle_get_many(list_params)
        return self.to_response(result, ResponseShape.LIST)

    @get("/{id}")
    def get_one_endpoint(self, id: ViewID) -> ViewResponse:
        """``GET /{id}`` endpoint method. Put the rows in ``scope``, the
        lookup of the id in ``where_id``, and the response shape in
        ``to_response``; replace this method only to change the HTTP
        contract."""
        obj = self.handle_get_one(id)
        return self.to_response(obj)

    @post("/")
    def create_endpoint(self, data: ViewCreate) -> ViewResponse:
        """``POST /`` endpoint method. Override ``create`` for domain
        logic (it is commit-free; the handler owns the commit),
        ``to_response`` for the response shape; replace this method only to
        change the HTTP contract."""
        obj = self.handle_create(data)
        return self.to_response(obj)

    @patch("/{id}")
    def update_endpoint(self, id: ViewID, data: ViewUpdate) -> ViewResponse:
        """``PATCH /{id}`` endpoint method. Override ``update`` for
        domain logic, ``to_response`` for the response shape; replace this
        method only to change the HTTP contract."""
        obj = self.handle_update(id, data)
        return self.to_response(obj)

    @delete("/{id}")
    def delete_endpoint(self, id: ViewID) -> fastapi.Response:
        """``DELETE /{id}`` endpoint method. Override ``delete`` for
        domain logic (e.g. soft delete); replace this method only to change the
        HTTP contract (e.g. return the deleted object instead of 204)."""
        self.handle_delete(id)
        return self.to_response(None, ResponseShape.EMPTY)

    # ====================================================================
    # Handlers (final: call from a custom route, never override)
    # ====================================================================

    @final
    def handle_get_many(
        self, list_params: Any, *, scope: ReadScope = None, where: _ReadWhere = None
    ) -> ListResult[ModelT]:
        """List handler: ``authorize``, then the ``get_many`` load.

        Final, like every handler: ``authorize`` is the gate, and the list
        params and the scope decide the rows. Call it from a custom list
        route.

        :param scope: a clause that replaces the view scope for this read,
            so a custom route can list another surface of the same model
            (a trash list); ``fr.clauses.UNSCOPED`` reads past it.
            Forwarded to ``get_many``.
        :param where: a SQLAlchemy boolean expression or a clause that
            narrows this read inside the scope, so a nested list
            keeps the visibility rules (``where=Task.project_id == id``).
            The page and ``total_count`` both apply it. Forwarded to
            ``get_many``.
        :raises TypeError: ``where`` is a Python bool (a comparison on a
            loaded object), or neither an expression nor a clause.
        """
        self.authorize(Action.GET_MANY)
        return self.get_many(list_params, scope=scope, where=where)

    @final
    def handle_get_one(
        self, id: IdT | ColumnElement[bool], *, scope: ReadScope = None
    ) -> ModelT:
        """Retrieve handler: scoped load (404 by visibility) then read-auth.

        Final: ``authorize`` is the gate, and ``where_id`` and ``not_found``
        shape the load. Call it from a custom read route as "load with scope + 404 +
        read-auth". A write action instead loads with ``get_one(id,
        scope=...)`` and gates only its own action, the way
        ``handle_update`` and ``handle_delete`` do.

        :param id: the id from the path, which ``where_id`` turns into the
            WHERE, or a SQLAlchemy boolean expression that picks the row
            instead, so a natural-key route is this handler with
            ``Item.slug == slug``. Forwarded to ``get_one``.
        :param scope: a clause that replaces the view scope for this read,
            so a restore route can load the row the view scope hides.
            Forwarded to ``get_one``.
        """
        obj = self.get_one(id, scope=scope)
        self.authorize(Action.GET_ONE, obj=obj)
        return obj

    def write_action(
        self, action: str, *, obj: Any, data: Any = None
    ) -> AbstractContextManager[None]:
        """Run one write action of a custom route: ``authorize``, the reference
        check, your change, the save and the commit.

        ``obj`` is the row that the action is about. It is required:

        - A row from the database, such as one from ``get_one``. The block
          changes it. ``authorize`` gets the row, and ``old`` is its snapshot::

              with self.write_action(self.Action.PUBLISH, obj=post):
                  post.published = True

        - A new object that is not in the database yet: the action creates
          it. Restly adds it to the session when the block starts, so a flush
          in the block gives it an id. ``authorize`` gets ``obj=None``, as on
          ``POST /``, and ``old`` is ``None``. Pass the request as ``data``,
          so that ``authorize`` sees what the action creates::

              upload = Upload(filename=file.filename)
              with self.write_action(self.Action.CREATE, obj=upload, data=form):
                  self.session.flush()  # upload.id, for the lines
                  self.session.add_all(UploadLine(upload_id=upload.id) ...)

        - ``None``: the action has no single row, such as a change to several
          rows. ``authorize`` gets ``obj=None``, and the hooks get
          ``new=None`` and ``old=None``.

        ``action`` is the name that ``authorize`` and the hooks get. It is
        one of the names that the view's ``Action`` class declares, such as
        ``self.Action.PUBLISH``. ``data``
        goes to ``authorize``, and Restly checks the references in it, as a
        CRUD write does: the ``MustExist``, ``IDRef`` and ``IDSchema`` fields,
        also in nested schemas and in each item of a list. A missing row
        raises :class:`~fastapi_restly.exc.NotFound` (404).

        The block runs under ``session.no_autoflush``: nothing is written
        until the block ends, and a query in the block does not see your
        unflushed changes. When the block ends, Restly flushes the session.
        Then it refreshes ``obj`` and every other row of the view's model that
        the action wrote, and loads them with
        :meth:`~fastapi_restly.views.BaseRestView.get_loader_options`, so the
        route can return them. When the block deleted ``obj``,
        as a merge deletes its source, ``new`` is ``None``. Then Restly runs
        ``before_action_commit(action, new, old)``, commits, and runs
        ``after_action_commit(action, new, old)``. The block gets nothing
        from ``as``. Code after the block runs after the commit, but not when
        the block runs inside another block: then it runs before the commit.
        Work that needs the commit goes in ``after_action_commit``.

        An exception in the block skips the save and the commit. The
        outermost block on the session owns the commit and the after-hooks.
        So a handler or a ``write_action`` inside this block, or this block
        inside :meth:`shared_write_action_commit`, returns after its save and
        ``before_action_commit``. Its save also writes the changes of this
        block so far, so set the required values of the block's objects
        before you call it. An ``IntegrityError`` from ``authorize`` to
        the commit becomes :class:`~fastapi_restly.exc.Conflict` (409) when
        it leaves the outermost block, and stays raw inside it. A direct
        ``session.commit()`` inside the block raises ``RuntimeError``.

        :raises ValueError: the view's ``Action`` does not declare ``action``.
        :raises TypeError: ``obj`` is neither ``None`` nor an object of a
            SQLAlchemy model.
        """
        return sync_write_action(self, action, obj=obj, data=data)

    def shared_write_action_commit(self) -> AbstractContextManager[None]:
        """Share one commit across write actions on this session.

        The outermost block commits once, then runs the queued
        ``after_action_commit`` hooks. ``write_action`` and the write handlers
        still authorize, check the references, change, save, and run
        ``before_action_commit``. They return uncommitted objects inside the block::

            with self.shared_write_action_commit():
                for item in items:
                    self.handle_create(item)

        Serialize returned objects and run code that depends on an after-hook
        only after the block exits. Nested blocks on the same session share the
        commit. An exception escaping a deferred-commit block aborts the shared
        commit, including when an enclosing deferred-commit block catches that
        exception. A write action that raises after it changed objects stops
        the commit too, unless the rollback of a savepoint around it undid the
        change: run each write that a route may refuse in
        ``session.begin_nested()``. Rollback belongs to the session owner. An
        ``IntegrityError`` inside the block stays raw, so a route can catch the
        error of one row; one that escapes the outermost block, or that its
        commit raises, becomes :class:`~fastapi_restly.exc.Conflict` (409).

        After-hooks run in queue order and stop on the first exception. ``new``
        is the live object after all writes, while ``old`` is each action's
        snapshot. Direct ``session.commit()`` calls raise ``RuntimeError``.
        An async write action needs an async outermost block; a sync one
        joins either.
        """
        return _shared_write_action_commit(self.session)

    @final
    def handle_create(self, data: CreateSchemaT) -> ModelT:
        """Create handler: ``authorize``, the reference check, ``create``, commit bracket.

        Final: override ``create`` for the domain change, ``authorize`` for
        the gate, and ``before_action_commit`` / ``after_action_commit`` for
        side effects. ``authorize`` gets the payload, and no row. The object
        that ``create`` returns is the subject of the action: the save adds
        it, flushes, refreshes it, and the hooks get it as ``new``. Call this
        from a custom create route, and from inside
        ``shared_write_action_commit()`` to share one commit with other
        writes; it then returns after the save, before the commit.

        :raises TypeError: ``create`` returned ``None``.
        """
        with _bracket(self, Action.CREATE, None, data) as bracket:
            result = self.create(data)
            bracket.subject = _require_result(self, "create", result)
        return cast(ModelT, bracket.subject)

    @final
    def handle_update(
        self, id: IdT | ColumnElement[bool], data: UpdateSchemaT
    ) -> ModelT:
        """Update handler: scoped load, then ``update`` in the commit bracket.

        Final, like :meth:`handle_create`: ``update`` receives the loaded
        object, so the load, the 404, ``authorize`` and the reference check
        stay here. ``old`` is the snapshot of the loaded row, and ``new`` is
        the object that ``update`` returns, after the save. Inside
        ``shared_write_action_commit()`` it returns before the commit.

        :raises TypeError: ``update`` returned ``None``.
        """
        obj = self.get_one(id)
        with _bracket(self, Action.UPDATE, obj, data) as bracket:
            result = self.update(obj, data)
            bracket.subject = _require_result(self, "update", result)
        return cast(ModelT, bracket.subject)

    @final
    def handle_delete(self, id: IdT | ColumnElement[bool]) -> None:
        """Delete handler: scoped load, then ``delete`` in the commit bracket.

        Final, like :meth:`handle_create`. The loaded row is the subject.
        After a hard delete, the hooks get ``new=None``. A soft delete flips a
        timestamp in ``delete`` and keeps the row, so the hooks get the row as
        ``new``. An off-request follow-up runs in ``after_action_commit``.
        Inside ``shared_write_action_commit()``, the change runs at once, and
        the after-hook waits for the outermost block to commit.
        """
        obj = self.get_one(id)
        with _bracket(self, Action.DELETE, obj, None):
            self.delete(obj)

    # ====================================================================
    # Loads (final, auth-free): change them through the read seams
    # ====================================================================

    @final
    def get_many(
        self, list_params: Any, *, scope: ReadScope = None, where: _ReadWhere = None
    ) -> ListResult[ModelT]:
        """The load of the list: the rows the scope allows, filtered and paged
        by ``list_params``.

        Final, and auth-free: ``handle_get_many`` adds ``authorize``. The
        statement is :meth:`~fastapi_restly.views.BaseRestView.select`, then :meth:`apply_list_params`. A
        paginated view also runs :meth:`count` for ``total_count``; a view
        without pagination returns every matching row with
        ``total_count=None``. Override ``apply_list_params`` to filter, sort
        or page in another way, and ``to_single_response`` to add a value to
        each row.

        :param list_params: the list params (filter, sort, page) as the
            endpoint receives them. A ``QueryParams`` or a mapping goes
            through
            :attr:`~fastapi_restly.views.BaseRestView.schema_list_params`
            first, as on the list route.
        :param scope: a clause that replaces the view scope for this read;
            ``fr.clauses.UNSCOPED`` reads unscoped.
        :param where: a SQLAlchemy boolean expression or a clause that
            narrows this read inside the scope.
        :raises TypeError: ``where`` is a Python bool (a comparison on a
            loaded object), or neither an expression nor a clause.
        """
        # once here, so apply_list_params and the response get the list
        # params as the list route gives them
        list_params = self._validated_list_params(list_params)
        stmt = self.select(scope=self._narrowed_scope(scope, where))
        stmt = self.apply_list_params(stmt, list_params)
        total_count = (
            (self.count(stmt))
            if isinstance(self.pagination, NumberedPagination)
            else None
        )
        scalar_result = self.session.scalars(stmt)
        return ListResult(
            # unique(): a joined eager load of a collection, from the loader
            # options, repeats the row for each child
            objects=scalar_result.unique().all(),
            total_count=total_count,
            list_params=list_params,
        )

    @final
    def get_one(
        self, id: IdT | ColumnElement[bool], *, scope: ReadScope = None
    ) -> ModelT:
        """The load of one row: the row ``id`` names, through the scope, or
        the exception of :meth:`~fastapi_restly.views.BaseRestView.not_found`.

        Final, and auth-free: ``handle_get_one`` adds ``authorize``; a write
        action calls this directly and gates its own action. The statement
        is :meth:`~fastapi_restly.views.BaseRestView.select` with :meth:`~fastapi_restly.views.BaseRestView.where_id`, so a row outside the scope
        is a 404 for every caller. Override ``where_id`` to find the row by
        another key, and ``not_found`` to change the 404.

        :param id: the id from the path, or a SQLAlchemy boolean expression
            that picks the row instead (``get_one(Item.slug == slug)``). An
            expression narrows inside the scope; only ``scope=`` replaces it.
        :param scope: a clause that replaces the view scope for this read;
            ``fr.clauses.UNSCOPED`` reads unscoped.
        :raises fastapi_restly.exc.NotFound: no row matches inside the
            scope, or the exception that ``not_found`` returns.
        :raises sqlalchemy.exc.MultipleResultsFound: more than one row
            matches; an ambiguous key is a bug in the criterion.
        :raises TypeError: ``id`` is a Python bool (a comparison on a
            loaded object, which would render as ``WHERE true``), an
            uncalled clause (a scope, not a row identity), or an expression
            on a table that the query does not select from; or ``where_id``
            or ``not_found`` returned a value of the wrong kind.
        """
        stmt = self.select(scope=scope).where(self._row_criterion(id))
        # unique(): the public loader-options seam may return a joined eager
        # load against a collection, and that row fan-out would otherwise
        # read as several matches for one entity.
        obj = (self.session.scalars(stmt)).unique().one_or_none()
        if obj is None:
            raise self._not_found_error(id)
        return cast(ModelT, obj)

    def _not_found_error(self, id: Any) -> BaseException:
        """What :meth:`~fastapi_restly.views.BaseRestView.not_found` returns for ``id``, checked."""
        error = self.not_found(None if isinstance(id, ColumnElement) else id)
        if not isinstance(error, BaseException):
            raise TypeError(
                f"{type(self).__name__}.not_found must return an exception, "
                f"such as fr.exc.NotFound(...), got {type(error).__name__}."
            )
        return error

    # ====================================================================
    # Business methods (auth-free, commit-free): the common override point
    # ====================================================================

    def create(self, data: CreateSchemaT) -> ModelT:
        """Build a new object from ``data`` and return it.

        Auth-free, flush-free and commit-free: ``handle_create`` checks the
        references before this method, and saves and commits after it. The
        default is ``fr.objects.make_new_object`` with the view's
        model: it adds the object to the session, without a flush, so the
        object has no id yet. Override to change the object before the save
        (hash a password from the request): call ``super().create()``, change
        the object, and return it.

        Nothing is written until the save after this method: the handler runs
        it under ``session.no_autoflush``, so a query here does not flush the
        new object, and it does not see your unflushed changes. Call
        ``self.session.flush()`` when you need the id inside this method. A
        direct call, outside ``handle_create``, writes nothing.
        """
        model_cls = cast(type[ModelT], self.model)
        return object_make_new_object(self.session, model_cls, data)

    def update(self, obj: ModelT, data: UpdateSchemaT) -> ModelT:
        """Apply ``data`` to the loaded ``obj`` and return it.

        Auth-free, flush-free and commit-free: ``handle_update`` loads ``obj``
        through ``get_one``, gates, checks the references, and saves and
        commits after this method. The default is
        ``fr.objects.update_object``. Override to check the stored row
        first, or to change the object after ``super().update()``. As in
        ``create``, nothing is written before the save, and a direct call
        writes nothing.
        """
        return object_update_object(self.session, obj, data)

    def delete(self, obj: ModelT) -> None:
        """Mark ``obj`` for deletion. Does not flush or commit:
        ``handle_delete`` does.

        Override (on the view or on a soft-delete mixin) to flip a timestamp
        instead of removing the row, without calling ``super()``. A raw row
        delete elsewhere is ``fr.objects.delete_object(self.session, obj)``.
        """
        self.session.delete(obj)

    # ====================================================================
    # Read seams
    # ====================================================================

    def apply_list_params(
        self, query: sqlalchemy.Select[Any], list_params: Any
    ) -> sqlalchemy.Select[Any]:
        """Apply the list params to ``query``: :meth:`apply_filters`, then the
        sort, then the page.

        Override for list params of your own; the common case is driven by
        configuration. A ``QueryParams`` or a mapping goes through
        :attr:`~fastapi_restly.views.BaseRestView.schema_list_params` first,
        as on the list route. In a ``QueryParams``, a key that the route
        reads is left out. Any other key that the list params do not have
        raises ``RequestValidationError``, as on the list route (see
        :attr:`~fastapi_restly.views.BaseRestView.unknown_query_params`).
        """
        list_params = self._validated_list_params(list_params)
        query = self.apply_filters(query, list_params)
        return _apply_sort_and_page(
            query,
            list_params,
            self.model,
            _schema_response(type(self)),
            pagination=self.pagination,
        )

    def apply_filters(
        self, stmt: sqlalchemy.Select[Any], list_params: Any
    ) -> sqlalchemy.Select[Any]:
        """Apply the filters of ``list_params`` to ``stmt``, without the sort
        and the page.

        For a route that reads the client's filters but has its own order or
        no rows to page, such as a count per category with ``GROUP BY``.
        Like every custom read route, such a route calls :meth:`authorize`
        and starts from :meth:`~fastapi_restly.views.BaseRestView.select`, which applies the scope. :meth:`apply_list_params` calls this
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
        results = [
            (f"{type(self).__name__}.{own.method}", getattr(self, own.method)(value))
            for own, value in self._filter_method_values(list_params)
        ]
        return _keep_joins(filtered, _apply_own_filters(filtered, results))

    def count(self, query: sqlalchemy.Select[Any]) -> int:
        """Total for the list, ignoring presentation-layer ordering/pagination.

        It counts the distinct primary keys of the model that ``query``
        selects, without its order and page. So a join to a to-many
        relationship does not count a row twice. No other column is part of
        the count, so a ``json`` column works on PostgreSQL, and the subquery
        of a ``column_property`` does not run for every row. A ``query`` that
        selects columns, not a model, counts its distinct rows. Override for
        estimated counts on huge tables.
        """
        return int(self.session.scalar(_count_statement(query)) or 0)

    # ====================================================================
    # Request-logic seams (authorize + transaction hooks)
    # ====================================================================

    def authorize(
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

    def before_action_commit(
        self, action: str, new: ModelT | None, old: dict[str, Any] | None = None
    ) -> None:
        """In-transaction side effect (outbox rows, audit rows), committed
        atomically with the write. ``old`` is the pre-mutation snapshot dict.
        """

    def after_action_commit(
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
