"""Composable, context-bound query clauses for SQLAlchemy.

A Clause carries a predicate ("where") and/or a statement transform
("transform"). Clause itself is abstract; each constructor names the
kind it builds: where_clause() -> WhereClause, transform_clause() ->
TransformClause, combine() -> CombinedClause. The fourth kind, a
ContextParam value slot, is declared in a ContextNamespace
(`name: ContextParam[T]`), not constructed. A CombinedClause always
carries at least one transform; a bundle of only wheres is all_of's
job. Functions passed to the constructors are wrapped with @contextual
(see contextargs): parameters the caller does not supply are injected
from values bound via Clause.bind().

A WhereClause is also callable: calling it (optionally with an
ephemeral bind as keyword arguments) returns the raw ColumnElement,
for use inside plain SQLAlchemy: join conditions, CASE expressions,
a hand-built .where(). Note that this bypasses apply_clauses' table
validation: raw SQLAlchemy land, raw SQLAlchemy rules.

Composites built with all_of/any_of/none_of/combine keep their operands
as children, and Clause.bind() routes each value down the tree to the
leaf that accepts it. Binding on a composite is therefore equivalent to
binding on the leaf itself:

    visible = all_of(owned_by_tenant, none_of(is_deleted))

    with visible.bind(tenant_id=tid):          # same as
    with owned_by_tenant.bind(tenant_id=tid):  # this

Routing is strict: a value nobody accepts raises, and a value accepted
by more than one distinct contextual instance raises too; that only
happens with aliases or an accidental name collision, and in both cases
binding on the leaf directly is the unambiguous fix. The same leaf
reached through several branches is fine and binds once.

Statement construction stays plain SQLAlchemy: build select()/update()/
delete() as usual and pass the result through apply_clauses(), the
bridge between the two worlds. The Clause.select/.update/.delete
methods are shorthand for the common single-clause path; select()
takes the same entities SQLAlchemy's select() takes. Wherever a
clause resolves, keyword arguments are an ephemeral bind: the
shorthand methods and apply_clauses alike, with no signature
reserving a keyword name. apply_clauses collects transforms
from the whole clause tree, each distinct transform applied once, so a
join carried inside an all_of or combine is never lost. any_of and
none_of reject operands that carry a transform: OR/NOT over an
inner-join-dependent predicate silently changes which rows exist at
all. Express such conditions as EXISTS (relationship .any()/.has())
in a plain where.
"""

from __future__ import annotations

from contextlib import ExitStack as _ExitStack
from contextlib import contextmanager as _contextmanager
from typing import Any as _Any
from typing import Generic as _Generic
from typing import Iterator as _Iterator
from typing import Sequence as _Sequence
from typing import TypeVar as _TypeVar
from typing import final as _final
from typing import overload as _overload

from sqlalchemy import ColumnElement as _ColumnElement
from sqlalchemy import Delete as _Delete
from sqlalchemy import Select as _Select
from sqlalchemy import Update as _Update
from sqlalchemy import bindparam as _sqla_bindparam
from sqlalchemy import delete as _sqla_delete
from sqlalchemy import select as _sqla_select
from sqlalchemy import update as _sqla_update
from sqlalchemy.orm import DeclarativeBase as _DeclarativeBase
from sqlalchemy.sql.expression import BindParameter as _BindParameter
from sqlalchemy.sql.expression import ColumnClause as _ColumnClause
from sqlalchemy.sql.expression import Join as _Join
from sqlalchemy.sql.expression import ScalarSelect as _ScalarSelect
from sqlalchemy.sql.expression import SelectBase as _SelectBase
from sqlalchemy.sql.expression import Subquery as _Subquery
from sqlalchemy.sql.visitors import ExternallyTraversible as _ExternallyTraversible
from sqlalchemy.sql.visitors import iterate as _sqla_iterate
from typing_extensions import Self as _Self

from .._binding import _bind_dependency
from .._contextargs import Contextual as _Contextual
from .._contextargs import MissingContextValues as _MissingContextValues
from .._contextargs import _caller_origin

_T = _TypeVar("_T")


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
        self._where_fn: _Contextual | None = None
        self._transform_fn: _Contextual | None = None
        self._param_fn: _Contextual | None = None
        self._placeholder: _BindParameter | None = None
        self._condition: _ColumnElement[bool] | None = None
        # context names routable to the transform: its accepted names
        # minus the statement parameter, which is never a context value
        self._transform_routable: frozenset[str] | None = None
        self._children: tuple[Clause, ...] = ()

    def __bool__(self) -> bool:
        # `if item.is_deleted:` would otherwise always be True: a Clause
        # is a query fragment, not an answer
        raise TypeError("a Clause is not a boolean; apply it to a query instead")

    def _own_routing(self) -> _Iterator[tuple[_Contextual, frozenset[str] | None]]:
        if self._where_fn is not None:
            yield self._where_fn, self._where_fn.accepted
        if self._transform_fn is not None:
            yield self._transform_fn, self._transform_routable
        if self._param_fn is not None:
            yield self._param_fn, self._param_fn.accepted

    def _routing(
        self, _seen: set[int] | None = None
    ) -> _Iterator[tuple[_Contextual, frozenset[str] | None]]:
        # visited-set: shared subtrees walk once, not once per path
        seen = set() if _seen is None else _seen
        if id(self) in seen:
            return
        seen.add(id(self))
        yield from self._own_routing()
        for child in self._children:
            yield from child._routing(seen)

    def _transforms(self, _seen: set[int] | None = None) -> _Iterator[_Contextual]:
        seen = set() if _seen is None else _seen
        if id(self) in seen:
            return
        seen.add(id(self))
        if self._transform_fn is not None:
            yield self._transform_fn
        for child in self._children:
            yield from child._transforms(seen)

    @_contextmanager
    def bind(self, /, **values: _Any):
        """Bind values for the duration of the with block.

        Each value is routed to the one leaf in this tree whose function
        accepts its name, so binding on a composite is equivalent to
        binding on the leaf. A value nobody accepts raises TypeError; so
        does a name accepted by two distinct leaves, which happens with
        aliases or an accidental name collision. Bind those on each leaf
        directly.
        """
        # deduplicate on instance: the same leaf reached through several
        # branches (a shared owned_by_tenant, say) binds once
        pairs: list[tuple[_Contextual, frozenset[str] | None]] = []
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
                kind = (
                    "aliases of the same clause"
                    if len({fn.family for fn in claimants}) == 1
                    else "unrelated clauses"
                )
                raise TypeError(
                    f"{key!r} is accepted by multiple {kind}; "
                    "bind it on each leaf directly"
                )

        with _ExitStack() as stack:
            for fn, _ in pairs:
                subset = {k: v for k, v in values.items() if claims[k][0] is fn}
                if subset:
                    stack.enter_context(fn.context(**subset))
            yield

    def select(self, /, *entities: _Any, **binds: _Any) -> _Select[_Any]:
        """``sqlalchemy.select(*entities)`` with this clause applied.

        Positional arguments are exactly SQLAlchemy's: mapped classes,
        columns, functions. Keyword arguments are an ephemeral bind():
        the values live only for the duration of building this
        statement. Without them the ambient bind (a surrounding with
        ...bind():) applies as usual. Types as Select[Any]; for precise
        row typing build the statement with plain select() into its own
        variable and pass that to apply_clauses(), which preserves the
        statement's exact type. The inline nested call widens to
        Select[Any] under bidirectional inference.
        """
        with self.bind(**binds):
            return apply_clauses(_sqla_select(*entities), self)

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

    def explain(self) -> str:
        """The clause tree with per-leaf bind names, values, and origins.

        Node labels are the nodes' reprs; under each node, one line per
        bind name it owns: the bound value (repr, truncated) with the
        file:line that bound it, or UNBOUND. A subtree reached through
        several paths renders once and is marked shared after that. The
        answer to "why is this query filtered the way it is" without
        leaving the debugger.
        """
        lines: list[str] = []
        seen: set[int] = set()

        def bind_entries(node: Clause, label: str) -> list[str]:
            entries: list[str] = []
            for fn, routable in node._own_routing():
                names = routable if routable is not None else fn.accepted
                bound = fn.bindings()
                for bind_name in sorted(names or ()):
                    if bind_name in bound:
                        value, origin = bound[bind_name]
                        try:
                            shown = repr(value)
                        except Exception:
                            shown = f"<unrepresentable {type(value).__name__}>"
                        if len(shown) > 60:
                            shown = shown[:57] + "..."
                        # a ContextParam's label already names its bind site
                        suffix = (
                            f"   bound at {origin}"
                            if origin and origin not in label
                            else ""
                        )
                        entries.append(f"{bind_name} = {shown}{suffix}")
                    else:
                        entries.append(f"{bind_name}: UNBOUND")
            return entries

        def render(node: Clause, prefix: str, connector: str) -> None:
            label = repr(node)
            if id(node) in seen:
                lines.append(prefix + connector + label + "  (shared, shown above)")
                return
            seen.add(id(node))
            lines.append(prefix + connector + label)
            if connector == "└─ ":
                child_prefix = prefix + "   "
            elif connector == "├─ ":
                child_prefix = prefix + "│  "
            else:
                child_prefix = prefix
            entries: list[tuple[str, object]] = [
                ("bind", entry) for entry in bind_entries(node, label)
            ]
            entries += [("node", child) for child in node._children]
            for index, (kind, payload) in enumerate(entries):
                last = index == len(entries) - 1
                branch = "└─ " if last else "├─ "
                if kind == "bind":
                    lines.append(child_prefix + branch + payload)  # type: ignore[operator]
                else:
                    render(payload, child_prefix, branch)  # type: ignore[arg-type]

        render(self, "", "")
        return "\n".join(lines)

    @classmethod
    def _blank(cls) -> _Self:
        # internal construction path; ContextParam overrides it because its
        # public __init__ deliberately raises
        return cls()

    def alias(self, name: str | None = None) -> _Self:
        """An independent instance with its own context namespace.

        Leaf-only: which leaves of a composite should share bindings and
        which should split cannot be decided automatically. Rebuild the
        composite from aliased leaves instead. Named aliases are memoized
        at the contextargs level, so the same name addresses the same
        namespace anywhere; anonymous aliases are always fresh.
        """
        if any(not isinstance(c, ContextParam) for c in self._children):
            raise TypeError(
                "alias works on leaf clauses only; "
                "rebuild composites from aliased leaves"
            )
        result = type(self)._blank()
        result._children = self._children  # embedded slots stay shared by design
        result._condition = self._condition
        if self._where_fn is not None:
            result._where_fn = self._where_fn.alias(name)
        if self._transform_fn is not None:
            result._transform_fn = self._transform_fn.alias(name)
            result._transform_routable = self._transform_routable
        if self._param_fn is not None:
            result._param_fn = self._param_fn.alias(name)
            assert self._placeholder is not None
            placeholder = _sqla_bindparam(self._placeholder.key)
            placeholder._fr_param = result  # type: ignore[attr-defined]
            result._placeholder = placeholder
        return result


class WhereClause(Clause):
    """A pure predicate: no transforms anywhere in its tree.

    The only kind allowed in any_of()/none_of() and on UPDATE/DELETE,
    and the only callable one: calling it returns the raw ColumnElement
    for use inside plain SQLAlchemy expressions.
    """

    def __call__(self, /, **binds: _Any) -> _ColumnElement[bool]:
        """Resolve to the raw ColumnElement, for plain SQLAlchemy use.

        Keyword arguments are an ephemeral bind(). The result drops into
        any expression position: .where(), a join condition, a CASE.
        This path skips apply_clauses' table validation.
        """
        with self.bind(**binds):
            result = _resolve_where(self)
        assert result is not None  # invariant: a WhereClause always has a where
        return result

    def update(self, model: type[_DeclarativeBase], /, **binds: _Any) -> _Update:
        """UPDATE on model with this clause applied; see select()."""
        with self.bind(**binds):
            return apply_clauses(_sqla_update(model), self)

    def delete(self, model: type[_DeclarativeBase], /, **binds: _Any) -> _Delete:
        """DELETE on model with this clause applied; see select()."""
        with self.bind(**binds):
            return apply_clauses(_sqla_delete(model), self)


class TransformClause(Clause):
    """Only reshapes the statement: joins, ordering, limits."""


@_final
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


class ContextParam(Clause, _Generic[_T]):
    """A named value slot: no predicate, no transform, no SQL of its own.

    Declared in a ContextNamespace (``tenant_id: ContextParam[UUID]``),
    never constructed directly: a slot is pure name and identity, and the
    namespace is its address. One slot, three positions. Embedded in an
    expression (``Item.tenant_id == Current.tenant_id``) it becomes a
    placeholder, filled with the bound value each time the clause
    resolves. As ``Annotated`` metadata on a clause function's parameter
    it feeds that parameter from the slot. Called, it returns the bound
    value for Python-side use. Sharing is by identity: every clause that
    embeds or marks the same slot is served by a single bind().
    """

    def __init__(self) -> None:
        raise TypeError(
            "a ContextParam is not constructed directly; declare it in a "
            "ContextNamespace: `name: ContextParam[T]` in the class body"
        )

    @classmethod
    def _blank(cls) -> _Self:
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

    def __repr__(self) -> str:
        assert self._param_fn is not None
        names = ", ".join(sorted(self._param_fn.accepted or ()))
        for _, origin in self._param_fn.bindings().values():
            if origin:
                return f"<ContextParam {names}, bound at {origin}>"
        if self._param_fn.bindings():
            return f"<ContextParam {names}, bound>"
        return f"<ContextParam {names}>"

    def __call__(self, /, **binds: _Any) -> _T:
        """Resolve to the bound value; keyword arguments are an ephemeral bind()."""
        with self.bind(**binds):
            assert self._param_fn is not None  # invariant: set at declaration
            return _teaching_call(self._param_fn)

    def depends(self, source: _Any, /) -> _Any:
        """A FastAPI dependency that binds this slot per request.

        ``source`` is the dependency the value comes from: a callable, a
        ``Depends(...)``, or an ``Annotated`` alias, so
        ``app.dependency_overrides`` keeps working. The result drops into
        a ``dependencies=[...]`` list at app, router, or view level, and
        is an async dependency underneath: the bind lands in the request
        task, where async and def endpoints alike read it.
        """
        return _bind_dependency([(self, source)], _caller_origin())


def _teaching_call(fn: _Contextual, *args):
    try:
        return fn.context_call(*args)
    except _MissingContextValues as error:
        first = error.names[0]
        raise TypeError(
            f"{error.label} is missing bound values for: "
            + ", ".join(error.names)
            + f"; bind them around this code with .bind({first}=...) on the "
            "shared ContextParam itself, or on the clause or an enclosing "
            "composite when it routes the name; routable names can also be "
            "passed as keywords to the statement shorthands or apply_clauses()"
        ) from None


def _embedded_slots(expr: object) -> tuple[ContextParam, ...]:
    slots: list[ContextParam] = []
    stack: list = [expr]
    while stack:
        el = stack.pop()
        slot = getattr(el, "_fr_param", None)
        if slot is not None:
            if slot not in slots:
                slots.append(slot)
            continue
        stack.extend(el.get_children())
    return tuple(slots)


_HANDWRITTEN = object()  # owners-entry for a user-written bindparam


def _fill_slots(
    expr: _ColumnElement[bool], owners: dict[str, object] | None = None
) -> _ColumnElement[bool]:
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
        if getattr(existing, "family", None) is getattr(owner, "family", object()):
            raise TypeError(
                f"the slot {key!r} appears through two different aliases in "
                "one statement; aliases share a key, use distinct "
                "context_param names instead"
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
        if isinstance(el, _BindParameter) and not el.unique:
            claim(el.key, _HANDWRITTEN)
        stack.extend(el.get_children())
    return expr.params(values) if values else expr


def _resolve_where(
    clause: Clause, owners: dict[str, object] | None = None
) -> _ColumnElement[bool] | None:
    if clause._where_fn is None:
        return None
    return _fill_slots(_teaching_call(clause._where_fn), owners)


def _resolve_carried_where(clause: Clause) -> _ColumnElement[bool]:
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


def _apply_transforms(stmt: _Select[_Any], clauses: _Sequence[Clause]) -> _Select[_Any]:
    # whole-tree collection, each distinct transform applied once: a join
    # carried inside a composite is never lost, a shared join never doubled
    seen: set[int] = set()
    for clause in clauses:
        for fn in clause._transforms():
            if id(fn) not in seen:
                seen.add(id(fn))
                stmt = _teaching_call(fn, stmt)
    return stmt


def _seed_owners(stmt: _ExternallyTraversible) -> dict[str, object]:
    # SQLAlchemy's bind-key space is per statement, so ownership must be
    # too: binds the statement already carries (earlier apply_clauses
    # layers, hand-written bindparams) claim their keys before any
    # clause of this call fills a slot
    owners: dict[str, object] = {}
    for el in _sqla_iterate(stmt):
        if isinstance(el, _BindParameter) and not el.unique:
            slot = getattr(el, "_fr_param", None)
            owners.setdefault(
                el.key, slot._param_fn if slot is not None else _HANDWRITTEN
            )
    return owners


def _resolved_wheres(
    clauses: _Sequence[Clause], owners: dict[str, object] | None = None
) -> list[_ColumnElement[bool]]:
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


def _expression_tables(expr: _ColumnElement) -> set[str]:
    # tables the expression references at its outer level; self-contained
    # subqueries (EXISTS, IN (SELECT ...)) bring their own FROMs and are
    # skipped
    names: set[str] = set()
    stack: list = [expr]
    while stack:
        el = stack.pop()
        if isinstance(el, (_SelectBase, _ScalarSelect, _Subquery)):
            continue
        if isinstance(el, _ColumnClause):
            if el.table is not None:
                names.add(_table_name(el.table))
            continue
        stack.extend(el.get_children())
    return names


def _statement_tables(stmt: _Select[_Any] | _Update | _Delete) -> set[str]:
    froms: list = (
        list(stmt.get_final_froms()) if isinstance(stmt, _Select) else [stmt.table]
    )
    names: set[str] = set()
    while froms:
        el = froms.pop()
        if isinstance(el, _Join):
            froms.extend([el.left, el.right])
        else:
            names.add(_table_name(el))
    return names


_SelectT = _TypeVar("_SelectT", bound=_Select[_Any])


class _Forest(Clause):
    """Routing-only node over apply_clauses' arguments: one tree, so an
    ephemeral bind routes across them with Clause.bind()'s rules."""


@_overload
def apply_clauses(
    stmt: _SelectT, /, *clauses: Clause | Unscoped, **binds: _Any
) -> _SelectT: ...
@_overload
def apply_clauses(
    stmt: _Update, /, *clauses: WhereClause | Unscoped, **binds: _Any
) -> _Update: ...
@_overload
def apply_clauses(
    stmt: _Delete, /, *clauses: WhereClause | Unscoped, **binds: _Any
) -> _Delete: ...
def apply_clauses(stmt, /, *clauses: Clause | Unscoped, **binds: _Any):
    """Apply clauses to a statement built with plain SQLAlchemy.

    The bridge between the two worlds: build select()/update()/delete()
    as usual, then let this add the clauses' wheres and, for a Select,
    transforms. UPDATE/DELETE cannot join, so a clause carrying a
    transform is rejected there. A where that references a table the
    statement does not select from is rejected too: the silent
    alternative is a cartesian product. ``UNSCOPED`` among the clauses
    applies nothing: a scope seam passes on what it was given.

    Keyword arguments are an ephemeral bind() routed across all the
    given clauses, layered over any ambient bind for the duration of
    the call. The statement is positional-only, so every keyword name
    stays free for binding.
    """
    given = _without_unscoped("apply_clauses", clauses)
    if binds:
        forest = _Forest()
        forest._children = given
        with forest.bind(**binds):
            return apply_clauses(stmt, *given)
    for clause in given:
        if clause._where_fn is None and not _has_transform(clause):
            raise TypeError(
                f"{clause!r} contributes no predicate and no transform; a "
                "ContextParam carries a value: embed it in an expression "
                "instead of applying it"
            )
    if isinstance(stmt, _Select):
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
    stmt: _Select[_Any] | _Update | _Delete, wheres: _Sequence[_ColumnElement[bool]]
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
