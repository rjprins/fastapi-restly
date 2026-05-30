from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

import sqlalchemy
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect

from ..db import AsyncSessionDep
from ..exceptions import NotFound
from ..objects import async_delete_object as object_async_delete_object
from ..objects import async_make_new_object as object_async_make_new_object
from ..objects import async_save_object as object_async_save_object
from ..objects import async_update_object as object_async_update_object
from ..query import apply_list_params
from ._base import (
    BaseRestView,
    CreateSchemaT,
    IdT,
    ListingResult,
    ModelT,
    SchemaT,
    UpdateSchemaT,
    delete,
    get,
    patch,
    post,
)
from ._lifecycle import async_run_write_action

#: Return type of a ``mutate`` thunk passed to ``handle_write``.
_WriteT = TypeVar("_WriteT")


class AsyncRestView(BaseRestView[ModelT, SchemaT, CreateSchemaT, UpdateSchemaT, IdT]):
    """
    AsyncRestView creates an async CRUD/REST interface for database objects.
    Basic usage::

        class FooView(AsyncRestView):
            prefix = "/foo"
            schema = FooRead
            model = Foo

    Each verb is three tiers (see "the handle design" in the docs):

    * ``<verb>_endpoint`` — the route shell (wire). Owns the HTTP signature,
      ``response_model``, and ``to_response``. Rarely overridden.
    * ``handle_<verb>`` — the request handler. Owns ``authorize`` and the
      commit bracket (``before_commit`` -> commit -> ``after_commit``); returns
      the domain object. Reuse from custom actions to get the bracket.
    * ``<verb>`` (``get_many`` / ``get_one`` / ``create`` / ``update`` /
      ``delete``) — the domain operation. Auth-free, commit-free; the common
      override point (hash a password, derive a slug, ...).
    """

    session: AsyncSessionDep

    # ====================================================================
    # Route shells (wire boundary)
    # ====================================================================

    @get("/")
    async def get_many_endpoint(self, query_params: Any) -> Any:
        self._reject_unknown_query_params()
        result = await self.handle_get_many(query_params)
        return self.to_response(result, "get_many")

    @get("/{id}")
    async def get_one_endpoint(self, id: Any) -> Any:
        obj = await self.handle_get_one(id)
        return self.to_response(obj, "get_one")

    @post("/")
    async def create_endpoint(self, schema_obj: Any) -> Any:
        obj = await self.handle_create(schema_obj)
        return self.to_response(obj, "create")

    @patch("/{id}")
    async def update_endpoint(self, id: Any, schema_obj: Any) -> Any:
        obj = await self.handle_update(id, schema_obj)
        return self.to_response(obj, "update")

    @delete("/{id}")
    async def delete_endpoint(self, id: Any) -> Any:
        await self.handle_delete(id)
        return self.to_response(None, "delete")

    # ====================================================================
    # Request handlers (authorize + commit bracket)
    # ====================================================================

    async def handle_get_many(self, query_params: Any) -> ListingResult[ModelT]:
        """List request handler: ``authorize`` then the ``get_many`` domain op."""
        await self.authorize("get_many")
        return await self.get_many(query_params)

    async def handle_get_one(self, id: IdT) -> ModelT:
        """Retrieve handler: scoped load (404 by visibility) then read-auth.

        Reusable from custom actions as "load with scope + 404 + read-auth".
        """
        obj = await self.get_one(id)
        await self.authorize("get_one", obj=obj)
        return obj

    async def handle_write(
        self,
        action: str,
        *,
        obj: Any = None,
        data: Any = None,
        mutate: Callable[[], Awaitable[_WriteT]],
    ) -> _WriteT:
        """General write handler: run ``mutate`` through the full request
        bracket -- ``authorize`` -> ``snapshot`` (when ``obj`` is given) ->
        ``mutate`` -> ``before_commit`` -> commit -> ``after_commit`` -- and
        return its result.

        The CRUD write handlers delegate here, and custom write actions should
        too (load with ``handle_get_one`` first for scope + 404 + read-auth,
        then call ``handle_write`` instead of hand-rolling the bracket). Override
        to wrap or change the lifecycle for the whole view; the default delegates
        to :func:`async_run_write_action`.
        """
        return await async_run_write_action(
            self, action, obj=obj, data=data, mutate=mutate
        )

    async def handle_create(self, schema_obj: CreateSchemaT) -> ModelT:
        return await self.handle_write(
            "create", data=schema_obj, mutate=lambda: self.create(schema_obj)
        )

    async def handle_update(self, id: IdT, schema_obj: UpdateSchemaT) -> ModelT:
        obj = await self.get_one(id)
        return await self.handle_write(
            "update",
            obj=obj,
            data=schema_obj,
            mutate=lambda: self.update(obj, schema_obj),
        )

    async def handle_delete(self, id: IdT) -> None:
        obj = await self.get_one(id)
        await self.handle_write("delete", obj=obj, mutate=lambda: self.delete(obj))

    # ====================================================================
    # Domain operations (auth-free, commit-free) -- the common override point
    # ====================================================================

    async def get_many(self, query_params: Any) -> ListingResult[ModelT]:
        """Return the scoped, filtered, paginated page plus the total count.

        Routes through :meth:`build_query` (scope) + :meth:`apply_query_params`
        (filter/sort/page) + :meth:`count`. Auth-free; ``handle_get_many`` adds
        the ``authorize`` call.
        """
        query = self.build_query()
        query = self.apply_query_params(query, query_params)
        total_count = await self.count(query)
        loader_options = self.get_relationship_loader_options()
        if loader_options:
            query = query.options(*loader_options)
        scalar_result = await self.session.scalars(query)
        return ListingResult(
            objects=scalar_result.all(),
            total_count=total_count,
            query_params=query_params,
        )

    async def get_one(self, id: IdT) -> ModelT:
        """Load one object through :meth:`build_query` (scope + 404).

        Auth-free: visibility comes from ``build_query``, so a row hidden by the
        scope is a clean 404 for every caller. ``handle_get_one`` adds read-auth.
        """
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
        obj = (await self.session.scalars(query)).first()
        if obj is None:
            raise NotFound(
                f"{self.model.__name__} with id {id!r} was not found"
            )
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
        """Delete ``obj``. Override (e.g. on a soft-delete mixin) to flip a
        timestamp instead of removing the row.
        """
        await self.delete_object(obj)

    # ====================================================================
    # Read seams
    # ====================================================================

    def build_query(self) -> sqlalchemy.Select[Any]:
        """Return the base SQLAlchemy ``Select`` used by every read on this
        view's model -- list, count, and retrieve. Override to add ``WHERE``
        clauses that should apply to all of them (tenant scope, soft-delete
        filtering, row-level permission visibility). Call ``super().build_query()``
        and chain ``.where(...)`` to compose with base-class or mixin filters.

        Because retrieve also routes through this query, a row hidden from the
        list cannot be fetched directly via ``GET /{id}`` -- visibility stays
        consistent across endpoints by construction.
        """
        return sqlalchemy.select(self.model)

    def apply_query_params(
        self, query: sqlalchemy.Select[Any], query_params: Any
    ) -> sqlalchemy.Select[Any]:
        """Apply URL filter/sort/pagination to ``query``. Override for a
        non-default URL grammar; the common case is driven by configuration.
        """
        return apply_list_params(query_params, query, self.model, self.schema)

    async def count(self, query: sqlalchemy.Select[Any]) -> int:
        """Total for the list, ignoring presentation-layer ordering/pagination.

        Wrapping the stripped query as a subquery preserves correct totals for
        DISTINCT, GROUP BY, and other user-provided query shapes. Override for
        estimated counts on huge tables.
        """
        count_source = query.order_by(None).limit(None).offset(None)
        count_query = select(func.count()).select_from(count_source.subquery())
        return int(await self.session.scalar(count_query) or 0)

    # ====================================================================
    # Domain utilities (call from `create`/`update`; not override seams)
    # ====================================================================

    async def make_new_object(self, schema_obj: CreateSchemaT) -> ModelT:
        """Construct a new ORM object from ``schema_obj`` and add it to the
        session. Calls :meth:`prepare_create` so structural mixins can stamp
        extra fields. Does not flush -- :meth:`save_object` does.
        """
        model_cls = cast(type[ModelT], self.model)
        obj = await object_async_make_new_object(
            self.session, model_cls, schema_obj, self.schema
        )
        for key, value in (await self.prepare_create(schema_obj)).items():
            setattr(obj, key, value)
        return obj

    async def update_object(
        self, obj: ModelT, schema_obj: UpdateSchemaT
    ) -> ModelT:
        """Apply writable fields from ``schema_obj`` to ``obj`` (plus any
        :meth:`prepare_update` stamps). Does not flush.
        """
        obj = await object_async_update_object(
            self.session, obj, schema_obj, self.schema
        )
        for key, value in (await self.prepare_update(obj, schema_obj)).items():
            setattr(obj, key, value)
        return obj

    async def save_object(self, obj: ModelT) -> ModelT:
        """Flush the session and refresh ``obj`` from the database. Does not
        commit -- ``handle_<verb>`` owns the commit.
        """
        return await object_async_save_object(self.session, obj)

    async def delete_object(self, obj: ModelT) -> None:
        """Remove ``obj`` from the session and flush. Does not commit."""
        await object_async_delete_object(self.session, obj)

    # ====================================================================
    # Cooperative stamping seams (extra fields; structural mixins override)
    # ====================================================================

    async def prepare_create(self, schema_obj: CreateSchemaT) -> dict[str, Any]:
        """Return a dict of EXTRA fields to stamp on a new object (audit ids,
        tenant id, ownership). Structural mixins layer cooperatively::

            async def prepare_create(self, schema_obj):
                fields = await super().prepare_create(schema_obj)
                fields["tenant_id"] = self.request.user.tenant_id
                return fields
        """
        return {}

    async def prepare_update(
        self, obj: ModelT, schema_obj: UpdateSchemaT
    ) -> dict[str, Any]:
        """Return a dict of EXTRA fields to stamp on update. Same cooperative
        pattern as :meth:`prepare_create`.
        """
        return {}

    # ====================================================================
    # Request-logic seams (authorize + transaction hooks)
    # ====================================================================

    async def authorize(
        self, action: str, obj: ModelT | None = None, data: Any = None
    ) -> None:
        """Gate a verb. Called by ``handle_<verb>`` at the right phase. The
        default consults :attr:`permissions`. Override to add row-level
        (``obj``) or data-aware (``data``) checks; raise ``fr.Forbidden`` /
        ``fr.NotFound`` to reject. Row *visibility* belongs in ``build_query``.
        """
        self._check_permission(action)

    async def before_commit(
        self, action: str, new: ModelT | None, old: dict[str, Any] | None = None
    ) -> None:
        """In-transaction side effect (outbox rows, audit rows), committed
        atomically with the write. ``old`` is the pre-mutation snapshot dict.
        """

    async def after_commit(
        self, action: str, new: ModelT | None, old: dict[str, Any] | None = None
    ) -> None:
        """Post-commit side effect (email, webhook, cache invalidation). ``old``
        enables dirty detection ("notify only if the status changed").
        """

    async def _commit(self) -> None:
        """Commit the current transaction. The handle design makes this the
        single commit point for a write request. No-op only when the caller
        opted out via ``commit_session_on_response=False`` (then you own the
        commit). A custom session generator manages close/rollback, but the
        handler still owns the commit -- set ``commit_session_on_response=False``
        if the generator manages its own transaction boundary.
        """
        from ..db._globals import _fr_globals

        if _fr_globals.commit_session_on_response:
            await self.session.commit()
