"""Runtime representation and statement application for query clauses."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any, Generic, Iterator, NoReturn, Sequence, TypeVar, final, overload

from sqlalchemy import ColumnElement, Delete, Select, Update, delete, select, update
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.expression import (
    BindParameter,
    ColumnClause,
    Join,
    ScalarSelect,
    SelectBase,
    Subquery,
)
from sqlalchemy.sql.visitors import ExternallyTraversible, iterate
from typing_extensions import Self

from .._binding import _bind_dependency
from .._contextargs import Contextual, MissingContextValues, _caller_origin

_T = TypeVar("_T")


class Clause:
    """Abstract base: a predicate and/or a statement transform, plus children.

    Build via where_clause(), transform_clause(), all_of()/any_of()/none_of()
    or combine(); never instantiate or mutate directly.
    """

    def __init__(self):
        if type(self) is Clause:
            raise TypeError(
                "Clause is abstract; build via where_clause/transform_clause/"
                "combine or the composites"
            )
        self._where_fn: Contextual | None = None
        self._transform_fn: Contextual | None = None
        self._param_fn: Contextual | None = None
        self._placeholder: BindParameter | None = None
        self._condition: ColumnElement[bool] | None = None
        # context names routable to the transform: its accepted names
        # minus the statement parameter, which is never a context value
        self._transform_routable: frozenset[str] | None = None
        self._children: tuple[Clause, ...] = ()

    def __bool__(self) -> bool:
        # `if item.is_deleted:` would otherwise always be True: a Clause
        # is a query fragment, not an answer
        raise TypeError("a Clause is not a boolean; apply it to a query instead")

    def _own_routing(self) -> Iterator[tuple[Contextual, frozenset[str] | None]]:
        if self._where_fn is not None:
            yield self._where_fn, self._where_fn.accepted
        if self._transform_fn is not None:
            yield self._transform_fn, self._transform_routable
        if self._param_fn is not None:
            yield self._param_fn, self._param_fn.accepted

    def _routing(
        self, _seen: set[int] | None = None
    ) -> Iterator[tuple[Contextual, frozenset[str] | None]]:
        # visited-set: shared subtrees walk once, not once per path
        seen = set() if _seen is None else _seen
        if id(self) in seen:
            return
        seen.add(id(self))
        yield from self._own_routing()
        for child in self._children:
            yield from child._routing(seen)

    def _transforms(self, _seen: set[int] | None = None) -> Iterator[Contextual]:
        seen = set() if _seen is None else _seen
        if id(self) in seen:
            return
        seen.add(id(self))
        if self._transform_fn is not None:
            yield self._transform_fn
        for child in self._children:
            yield from child._transforms(seen)

    @contextmanager
    def bind(self, /, **values: Any):
        """Bind values for the duration of the with block.

        Each value is routed to the one leaf in this tree whose function
        accepts its name, so binding on a composite is equivalent to
        binding on the leaf. A value nobody accepts raises TypeError; so
        does a name accepted by two distinct leaves, an accidental name
        collision. Bind those on each leaf directly.
        """
        # deduplicate on instance: the same leaf reached through several
        # branches (a shared owned_by_tenant, say) binds once
        pairs: list[tuple[Contextual, frozenset[str] | None]] = []
        seen: set[int] = set()
        for fn, routable in self._routing():
            if id(fn) not in seen:
                seen.add(id(fn))
                pairs.append((fn, routable))

        claims = {
            key: [fn for fn, routable in pairs if routable is None or key in routable]
            for key in values
        }
        unclaimed = sorted(key for key, claimants in claims.items() if not claimants)
        if unclaimed:
            raise TypeError("no clause accepts: " + ", ".join(unclaimed))
        for key, claimants in claims.items():
            if len(claimants) > 1:
                raise TypeError(
                    f"{key!r} is accepted by multiple unrelated clauses; "
                    "bind it on each leaf directly"
                )

        with ExitStack() as stack:
            for fn, _ in pairs:
                subset = {k: v for k, v in values.items() if claims[k][0] is fn}
                if subset:
                    stack.enter_context(fn.context(**subset))
            yield

    def select(self, /, *entities: Any) -> Select[Any]:
        """``sqlalchemy.select(*entities)`` with this clause applied.

        The arguments are exactly SQLAlchemy's: mapped classes, columns,
        functions. Types as Select[Any]; for precise row typing build the
        statement with plain select() into its own variable and pass that
        to apply_clauses(), which preserves the statement's exact type.
        The inline nested call widens to Select[Any] under bidirectional
        inference.
        """
        return apply_clauses(select(*entities), self)

    def __repr__(self) -> str:
        fn = self._where_fn or self._transform_fn or self._param_fn
        name = getattr(fn, "__name__", "")
        label = "" if not name or name.startswith("<") else f" {name}"
        if not label and self._condition is not None:
            try:
                condition = str(self._condition).replace("\n", " ")
            except Exception:
                condition = ""
            if condition:
                if len(condition) > 50:
                    condition = condition[:47] + "..."
                label = f" {condition}"
        wanted = sorted(
            {n for _, routable in self._routing() for n in (routable or ())}
        )
        binds = " binds: " + ", ".join(wanted) if wanted else ""
        return f"<{type(self).__name__}{label}{binds}>"


class WhereClause(Clause):
    """A pure predicate: no transforms anywhere in its tree.

    The only kind allowed in any_of()/none_of() and on UPDATE/DELETE,
    and the only callable one: calling it returns the raw ColumnElement
    for use inside plain SQLAlchemy expressions.
    """

    def __call__(self) -> ColumnElement[bool]:
        """Resolve to the raw ColumnElement, for plain SQLAlchemy use.

        The result drops into any expression position: .where(), a join
        condition, a CASE. This path skips apply_clauses' table validation.
        """
        result = _resolve_where(self)
        assert result is not None  # invariant: a WhereClause always has a where
        return result

    def update(self, model: type[DeclarativeBase], /) -> Update:
        """UPDATE on model with this clause applied; see select()."""
        return apply_clauses(update(model), self)

    def delete(self, model: type[DeclarativeBase], /) -> Delete:
        """DELETE on model with this clause applied; see select()."""
        return apply_clauses(delete(model), self)


class TransformClause(Clause):
    """Only reshapes the statement: joins, ordering, limits."""


@final
class Unscoped:
    """Sentinel scope: explicitly no scope, everywhere a scope can appear.

    `UNSCOPED` is the one instance; the class is public so a `scope`
    declaration or a `get_one` / `get_many` override can name the type
    (`Clause | Unscoped`).
    The explicit spelling for a view's `scope` (where `None` means
    "fall back to the model's default"), a namespace's `default_scope`
    (where undeclared defers to
    a base model's namespace, or none), and a `RefExists` scope (where
    `None` is rejected, so a variable that happens to be None can never
    silently unscope). `apply_clauses()` accepts it and applies nothing
    for it, preserving existing filters and other supplied clauses.
    Boolean composition treats it as SQL TRUE: all_of ignores it and
    returns the sole remaining clause unchanged, or UNSCOPED when none
    remain. any_of propagates it, and none_of returns a WhereClause over
    SQL false(). combine ignores it but still requires a transform.
    Composition validates other operands before simplifying.
    Searching for UNSCOPED finds explicit uses. Variables and composition
    can pass the sentinel to other scope declarations and read calls.
    Deliberately not re-exported at the top level: the escape is spelled
    in full.
    """

    def __repr__(self) -> str:
        return "fr.clauses.UNSCOPED"


UNSCOPED = Unscoped()


class CombinedClause(Clause):
    """A named bundle of wheres and transforms; always carries a transform."""


class ContextParam(Clause, Generic[_T]):
    """A named value slot: no predicate, no transform, no SQL of its own.

    Declared in a ContextNamespace (``tenant_id: ContextParam[UUID]``),
    never constructed directly: a slot is pure name and identity, and the
    namespace is its address. One slot, two positions. Embedded in an
    expression (``Item.tenant_id == Current.tenant_id``) it becomes a
    placeholder, filled with the bound value each time the clause
    resolves. Called, it returns the bound value for Python-side use,
    also inside a clause function. Sharing is by identity: every clause
    that embeds the same slot is served by a single bind().

    A member never stands in for its value: ``==``, ``!=`` and a truth
    test raise. Call the member to read it, and in a SQL expression put
    the column first (``Item.role == Current.role``).
    """

    def __init__(self) -> None:
        raise TypeError(
            "a ContextParam is not constructed directly; declare it in a "
            "ContextNamespace: `name: ContextParam[T]` in the class body"
        )

    @classmethod
    def _blank(cls) -> Self:
        # the internal construction path around the teaching __init__
        self = cls.__new__(cls)
        Clause.__init__(self)
        return self

    def __clause_element__(self) -> object:
        # SQLAlchemy's coercion protocol: the slot participates in
        # expressions as a tagged placeholder; _fill_slots() supplies
        # the value at resolve time
        assert self._placeholder is not None  # invariant: set by context_param()
        return self._placeholder

    # object equality answers False / True whatever is bound, and
    # SQLAlchemy renders that bool as WHERE false / WHERE true
    def __eq__(self, other: object) -> NoReturn:
        raise self._not_its_value("==")

    def __ne__(self, other: object) -> NoReturn:
        raise self._not_its_value("!=")

    # defining __eq__ drops the inherited hash; identity is the right one
    __hash__ = object.__hash__

    def __bool__(self) -> NoReturn:
        name = self._name
        raise TypeError(
            f"the context member {name!r} is not its value, so it has no "
            f"truth value; read the value by calling the member: {name}()"
        )

    @property
    def _name(self) -> str:
        assert self._param_fn is not None and self._param_fn.accepted
        return next(iter(self._param_fn.accepted))

    def _not_its_value(self, op: str) -> TypeError:
        name = self._name
        return TypeError(
            f"the context member {name!r} is not its value, so {op} cannot "
            f"compare it; read the value by calling the member ({name}() {op} "
            f"...), or in a SQL expression put the column first "
            f"(Model.column {op} <member>)"
        )

    def __repr__(self) -> str:
        assert self._param_fn is not None
        names = ", ".join(sorted(self._param_fn.accepted or ()))
        for _, origin in self._param_fn.bindings().values():
            if origin:
                return f"<ContextParam {names}, bound at {origin}>"
        if self._param_fn.bindings():
            return f"<ContextParam {names}, bound>"
        return f"<ContextParam {names}>"

    def __call__(self) -> _T:
        """Resolve to the bound value or raise LookupError if unbound."""
        assert self._param_fn is not None  # invariant: set at declaration
        return _teaching_call(self._param_fn)

    def depends(self, source: Any, /) -> Any:
        """A FastAPI dependency that binds this slot per request.

        ``source`` is the dependency the value comes from: a callable, a
        ``Depends(...)``, or an ``Annotated`` alias, so
        ``app.dependency_overrides`` keeps working. The result drops into
        a ``dependencies=[...]`` list at app, router, or view level, and
        is an async dependency underneath: the bind lands in the request
        task, where async and def endpoints alike read it.
        """
        return _bind_dependency([(self, source)], _caller_origin())


def _teaching_call(fn: Contextual, *args):
    try:
        return fn.context_call(*args)
    except MissingContextValues as error:
        first = error.names[0]
        raise LookupError(
            f"{error.label} is missing bound values for: "
            + ", ".join(error.names)
            + f"; bind them around this code with .bind({first}=...) on the "
            "shared ContextParam itself, or on the clause or an enclosing "
            "composite when it routes the name"
        ) from None


def _embedded_slots(expr: object) -> tuple[ContextParam, ...]:
    slots: list[ContextParam] = []
    stack: list = [expr]
    while stack:
        el = stack.pop()
        slot = getattr(el, "_fr_param", None)
        if slot is not None:
            # by identity: `in` would call the member's raising __eq__
            if not any(slot is seen for seen in slots):
                slots.append(slot)
            continue
        stack.extend(el.get_children())
    return tuple(slots)


_HANDWRITTEN = object()  # owners-entry for a user-written bindparam


def _fill_slots(
    expr: ColumnElement[bool], owners: dict[str, object] | None = None
) -> ColumnElement[bool]:
    """Replace embedded slot placeholders with their bound values.

    `owners` maps bindparam key -> owning namespace (the slot's
    contextual) and is shared across all clauses of one apply_clauses
    call: SQLAlchemy compiles binds by key, so two owners under one key
    would silently let the last value win. One name, one owner.
    """
    if owners is None:
        owners = {}
    values: dict[str, object] = {}

    def claim(key: str, owner: object) -> None:
        existing = owners.setdefault(key, owner)
        if existing is owner:
            return
        if _HANDWRITTEN in (existing, owner):
            raise TypeError(
                f"the ContextParam named {key!r} collides with a hand-written "
                f"bindparam({key!r}) in the same statement; rename one"
            )
        raise TypeError(
            f"two different ContextParams named {key!r} appear in one "
            "statement; share one slot or rename one"
        )

    stack: list = [expr]
    while stack:
        el = stack.pop()
        slot = getattr(el, "_fr_param", None)
        if slot is not None:
            key = slot._placeholder.key
            claim(key, slot._param_fn)
            if key not in values:
                assert slot._param_fn is not None
                values[key] = _teaching_call(slot._param_fn)
            continue
        if isinstance(el, BindParameter) and not el.unique:
            claim(el.key, _HANDWRITTEN)
        stack.extend(el.get_children())
    return expr.params(values) if values else expr


def _resolve_where(
    clause: Clause, owners: dict[str, object] | None = None
) -> ColumnElement[bool] | None:
    if clause._where_fn is None:
        return None
    return _fill_slots(_teaching_call(clause._where_fn), owners)


def _resolve_carried_where(clause: Clause) -> ColumnElement[bool]:
    # for combinators, which validate every operand carries a where first
    where = _resolve_where(clause)
    assert where is not None
    return where


def _has_transform(clause: Clause) -> bool:
    return any(True for _ in clause._transforms())


def _without_unscoped(
    name: str, clauses: tuple[Clause | Unscoped, ...]
) -> tuple[Clause, ...]:
    given: list[Clause] = []
    for clause in clauses:
        if clause is UNSCOPED:
            continue
        if not isinstance(clause, Clause):
            raise TypeError(f"{name}() accepts only clauses or UNSCOPED")
        given.append(clause)
    return tuple(given)


def _require_no_transforms(name: str, clauses: tuple[Clause, ...], why: str) -> None:
    if any(_has_transform(c) for c in clauses):
        raise TypeError(f"{name} cannot include transform clauses; {why}")


def _apply_transforms(stmt: Select[Any], clauses: Sequence[Clause]) -> Select[Any]:
    # whole-tree collection, each distinct transform applied once: a join
    # carried inside a composite is never lost, a shared join never doubled
    seen: set[int] = set()
    for clause in clauses:
        for fn in clause._transforms():
            if id(fn) not in seen:
                seen.add(id(fn))
                stmt = _teaching_call(fn, stmt)
    return stmt


def _seed_owners(stmt: ExternallyTraversible) -> dict[str, object]:
    # SQLAlchemy's bind-key space is per statement, so ownership must be
    # too: binds the statement already carries (earlier apply_clauses
    # layers, hand-written bindparams) claim their keys before any
    # clause of this call fills a slot
    owners: dict[str, object] = {}
    for el in iterate(stmt):
        if isinstance(el, BindParameter) and not el.unique:
            slot = getattr(el, "_fr_param", None)
            owners.setdefault(
                el.key, slot._param_fn if slot is not None else _HANDWRITTEN
            )
    return owners


def _resolved_wheres(
    clauses: Sequence[Clause], owners: dict[str, object] | None = None
) -> list[ColumnElement[bool]]:
    if owners is None:
        owners = {}
    return [w for w in (_resolve_where(c, owners) for c in clauses) if w is not None]


def _display_table_name(key: str) -> str:
    # strip the metadata-identity prefix _table_name adds for membership
    head, sep, tail = key.partition(":")
    return tail if sep and head.isdigit() else key


def _table_name(table) -> str:
    # include metadata identity where available: two same-named tables from
    # different registries must not satisfy each other in the validation
    name = str(getattr(table, "key", None) or getattr(table, "name", table))
    metadata = getattr(table, "metadata", None)
    return f"{id(metadata)}:{name}" if metadata is not None else name


def _expression_tables(expr: ColumnElement) -> set[str]:
    # tables the expression references at its outer level; self-contained
    # subqueries (EXISTS, IN (SELECT ...)) bring their own FROMs and are
    # skipped
    names: set[str] = set()
    stack: list = [expr]
    while stack:
        el = stack.pop()
        if isinstance(el, (SelectBase, ScalarSelect, Subquery)):
            continue
        if isinstance(el, ColumnClause):
            if el.table is not None:
                names.add(_table_name(el.table))
            continue
        stack.extend(el.get_children())
    return names


def _statement_tables(stmt: Select[Any] | Update | Delete) -> set[str]:
    froms: list = (
        list(stmt.get_final_froms()) if isinstance(stmt, Select) else [stmt.table]
    )
    names: set[str] = set()
    while froms:
        el = froms.pop()
        if isinstance(el, Join):
            froms.extend([el.left, el.right])
        else:
            names.add(_table_name(el))
    return names


_SelectT = TypeVar("_SelectT", bound=Select[Any])


@overload
def apply_clauses(stmt: _SelectT, /, *clauses: Clause | Unscoped) -> _SelectT: ...
@overload
def apply_clauses(stmt: Update, /, *clauses: WhereClause | Unscoped) -> Update: ...
@overload
def apply_clauses(stmt: Delete, /, *clauses: WhereClause | Unscoped) -> Delete: ...
def apply_clauses(stmt, /, *clauses: Clause | Unscoped):
    """Apply clauses to a statement built with plain SQLAlchemy.

    The bridge between the two worlds: build select()/update()/delete()
    as usual, then let this add the clauses' wheres and, for a Select,
    transforms. UPDATE/DELETE cannot join, so a clause carrying a
    transform is rejected there. A where that references a table the
    statement does not select from is rejected too: the silent
    alternative is a cartesian product. ``UNSCOPED`` among the clauses
    applies nothing: a scope seam passes on what it was given.
    """
    given = _without_unscoped("apply_clauses", clauses)
    for clause in given:
        if clause._where_fn is None and not _has_transform(clause):
            raise TypeError(
                f"{clause!r} contributes no predicate and no transform; a "
                "ContextParam carries a value: embed it in an expression "
                "instead of applying it"
            )
    if isinstance(stmt, Select):
        stmt = _apply_transforms(stmt, given)
    else:
        _require_no_transforms(
            "apply_clauses",
            given,
            f"{type(stmt).__name__.upper()} cannot join; use where-only clauses",
        )
    wheres = _resolved_wheres(given, _seed_owners(stmt))
    _guard_statement_tables(stmt, wheres)
    return stmt.where(*wheres)


def _guard_statement_tables(
    stmt: Select[Any] | Update | Delete, wheres: Sequence[ColumnElement[bool]]
) -> None:
    available = _statement_tables(stmt)
    for where in wheres:
        missing = _expression_tables(where) - available
        if missing:
            shown = sorted(_display_table_name(key) for key in missing)
            raise TypeError(
                "clause references table(s) not in the statement: "
                + ", ".join(shown)
                + "; add the join via a transform_clause, or use EXISTS (.any()/.has())"
            )


def _apply_where_half(stmt: _SelectT, clause: Clause) -> _SelectT:
    """Apply only the predicate half of `clause`; transforms are dropped.

    Reference existence checks use this, so for a scope used in them the
    predicate half must be the whole visibility rule: a clause without
    any predicate is rejected rather than silently checking nothing, and
    a predicate that depends on a dropped join fails the table
    validation above. A transform that itself filters rows (a filtering
    join) is the one shape neither guard can see; express row filtering
    as a where (EXISTS via .any()/.has()) instead.
    """
    wheres = _resolved_wheres([clause], _seed_owners(stmt))
    if not wheres:
        raise TypeError(
            f"{clause!r} carries no predicate; a reference check applies "
            "only the predicate half of a scope, so its row filtering must "
            "live in a where (use EXISTS via .any()/.has() instead of a "
            "filtering join)"
        )
    _guard_statement_tables(stmt, wheres)
    return stmt.where(*wheres)
