from contextlib import AbstractContextManager
from typing import Any, cast, final

import sqlalchemy
from sqlalchemy import ColumnElement, func, select

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
    _identity_criterion,
    _not_found_message,
    delete,
    get,
    patch,
    post,
)
from ._lifecycle import (
    _UNSET,
    _shared_write_action_commit,
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

    Each verb is three tiers (see "Customizing RestView" in the docs):

    * ``<verb>_endpoint``: the endpoint method. Owns the HTTP signature,
      ``response_model``, and ``to_response``. Rarely overridden.
    * ``handle_<verb>``: the handler. Owns ``authorize`` and the commit
      bracket (``before_action_commit`` -> commit -> ``after_action_commit``);
      returns the domain object. Final: call it from a custom route to get
      the bracket, never override it.
    * ``<verb>`` (``get_many`` / ``get_one`` / ``create`` / ``update`` /
      ``delete``): the business method. Auth-free, commit-free; the common
      override point (hash a password, derive a slug, ...).
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
    # Handlers (final: call from a custom route, never override)
    # ====================================================================

    @final
    def handle_get_many(
        self, query_params: Any, *, scope: ReadScope = None
    ) -> ListingResult[ModelT]:
        """List handler: ``authorize`` then the ``get_many`` business method.

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
    def handle_get_one(
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
        obj = self.get_one(id, scope=scope)
        self.authorize(Action.GET_ONE, obj=obj)
        return obj

    def write_action(self, action: str, *, obj: Any = _UNSET, data: Any = None):
        """Run a custom write action through the commit bracket.

        Use this for non-CRUD actions such as publish or change-password::

            with self.write_action("publish", obj=article):  # in-place
                article.status = "published"

        For create-shaped actions, omit ``obj`` and set ``w.obj`` before exit::

            with self.write_action("create", data=req) as w:
                w.obj = self.make_new_object(req)

        Pass ``obj=None`` for writes with no single object. Exceptions skip the
        commit. Inside :meth:`shared_write_action_commit`, the outermost block
        owns the commit and the after-hooks, so this bracket returns after its
        flush but before either one.
        """
        return sync_write_action(self, action, obj=obj, data=data)

    def shared_write_action_commit(self) -> AbstractContextManager[None]:
        """Share one commit across write actions on this session.

        The outermost block commits once, then runs the queued
        ``after_action_commit`` hooks. ``write_action`` and the write handlers
        still authorize, snapshot, mutate, run ``before_action_commit``, and
        flush. They return uncommitted objects inside the block::

            with self.shared_write_action_commit():
                for schema_obj in items:
                    self.handle_create(schema_obj)

        Serialize returned objects and run code that depends on an after-hook
        only after the block exits. Nested blocks on the same session share the
        commit. An exception escaping a deferred-commit block aborts the shared
        commit, including when an enclosing deferred-commit block catches that
        exception. Rollback belongs to the session owner.

        After-hooks run in queue order and stop on the first exception. ``new``
        is the live object after all writes, while ``old`` is each action's
        snapshot. Direct ``session.commit()`` calls raise ``RuntimeError``.
        An async write action needs an async outermost block; a sync one
        joins either.
        """
        return _shared_write_action_commit(self.session)

    @final
    def handle_create(self, schema_obj: CreateSchemaT) -> ModelT:
        """Create handler: ``authorize``, the ``create`` business method, commit bracket.

        Final: override ``create`` for the domain change, ``authorize`` for
        the gate, and ``before_action_commit`` / ``after_action_commit`` for
        side effects. Call it from a custom create route, and from inside
        ``shared_write_action_commit()`` to share one commit with other
        writes; it then returns after the flush, before the commit.
        """
        return run_write_action(
            self, Action.CREATE, data=schema_obj, mutate=lambda: self.create(schema_obj)
        )

    @final
    def handle_update(
        self, id: IdT | ColumnElement[bool], schema_obj: UpdateSchemaT
    ) -> ModelT:
        """Update handler: scoped load, then ``update`` in the commit bracket.

        Final, like :meth:`handle_create`: ``update`` receives the loaded
        object, so the load, the 404, and ``authorize`` stay here. Inside
        ``shared_write_action_commit()`` it returns before the commit.
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
    def handle_delete(self, id: IdT | ColumnElement[bool]) -> None:
        """Delete handler: scoped load, then ``delete`` in the commit bracket.

        Final, like :meth:`handle_create`: a soft delete flips a timestamp in
        ``delete``, and an off-request follow-up runs in
        ``after_action_commit``. Inside ``shared_write_action_commit()`` both
        run when the outermost block exits.
        """
        obj = self.get_one(id)
        run_write_action(self, Action.DELETE, obj=obj, mutate=lambda: self.delete(obj))

    # ====================================================================
    # Business methods (auth-free, commit-free): the common override point
    # ====================================================================

    def get_many(
        self, query_params: Any, *, scope: ReadScope = None
    ) -> ListingResult[ModelT]:
        """List the rows the scope allows, filtered and paged by ``query_params``.

        Auth-free: ``handle_get_many`` adds ``authorize``. The query is the
        resolved scope (``fr.resolve_scope(self)``, or ``scope`` when given)
        plus :meth:`apply_query_params`. A paginated view also runs
        :meth:`count` for ``total_count``; an unpaginated view returns every
        matching row with ``total_count=None``. Relationships the response
        schema names are eager-loaded.

        The handlers always forward ``scope=``, so an override must declare
        the parameter and pass it on to ``super()``.

        :param query_params: the listing parameters (filter, sort, page) as
            the endpoint receives them.
        :param scope: a clause that replaces the resolved scope for this
            read; ``fr.clauses.UNSCOPED`` reads unscoped.
        """
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

    def get_one(
        self, id: IdT | ColumnElement[bool], *, scope: ReadScope = None
    ) -> ModelT:
        """Load the one row ``id`` names, through the scope, or raise 404.

        Auth-free: ``handle_get_one`` adds ``authorize``; a write action
        calls this directly and gates its own action. Visibility is the
        resolved scope (``fr.resolve_scope(self)``, or ``scope`` when
        given), so a row outside it is a 404 for every caller.
        Relationships the response schema names are eager-loaded.

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
            loaded object, which would render as ``WHERE true``) or an
            uncalled clause (a scope, not a row identity).
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
        obj = self.session.scalars(query).unique().one_or_none()
        if obj is None:
            raise NotFound(_not_found_message(self.model, id))
        return cast(ModelT, obj)

    def create(self, schema_obj: CreateSchemaT) -> ModelT:
        """Build a new object from ``schema_obj`` and save it.

        Auth-free and commit-free: ``handle_create`` owns both. The usual
        create override point (hash a password, derive a slug), written as
        :meth:`make_new_object`, the extra step, then :meth:`save_object`.
        """
        obj = self.make_new_object(schema_obj)
        return self.save_object(obj)

    def update(self, obj: ModelT, schema_obj: UpdateSchemaT) -> ModelT:
        """Apply ``schema_obj`` to the loaded ``obj`` and save it.

        Auth-free and commit-free: ``handle_update`` loads ``obj`` through
        ``get_one``, gates, and commits. The usual update override point,
        written as :meth:`update_object`, the extra step, then
        :meth:`save_object`.
        """
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
        """Apply URL filter/sort/pagination to ``query``. Override for a
        non-default URL grammar; the common case is driven by configuration.
        """
        return apply_list_params(query_params, query, self.model, self.schema)

    def count(self, query: sqlalchemy.Select[Any]) -> int:
        """Total for the list, ignoring presentation-layer ordering/pagination.

        The stripped query is made ``DISTINCT`` and wrapped as a subquery, so
        the total is correct across user-provided query shapes, including a
        scope that joins a to-many relationship, whose row fan-out would
        otherwise inflate the count. Override for estimated counts on huge
        tables.
        """
        count_source = query.order_by(None).limit(None).offset(None).distinct()
        count_query = select(func.count()).select_from(count_source.subquery())
        return int(self.session.scalar(count_query) or 0)

    # ====================================================================
    # Domain utilities (final: call from a verb override, never override)
    # ====================================================================

    @final
    def make_new_object(self, schema_obj: CreateSchemaT) -> ModelT:
        """Construct a new ORM object from ``schema_obj`` and add it to the
        session. Does not flush; :meth:`save_object` does.

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
        """Apply writable fields from ``schema_obj`` to ``obj``. Does not flush.

        Final, like :meth:`make_new_object`: an ``updated_by`` stamp is the
        column's ``onupdate`` on the model; payload-derived values go in an
        ``update`` override, after this call.
        """
        return object_update_object(self.session, obj, schema_obj, self.schema)

    @final
    def save_object(self, obj: ModelT) -> ModelT:
        """Flush the session and refresh ``obj`` from the database, eager-loading
        the relationships the response schema names. Does not commit;
        ``handle_<verb>`` owns the commit.

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
        """
