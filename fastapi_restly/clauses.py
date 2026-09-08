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

import functools as _functools
import inspect as _inspect
import sys as _sys
import weakref as _weakref
from contextlib import ExitStack as _ExitStack
from contextlib import contextmanager as _contextmanager
from typing import Any as _Any
from typing import Callable as _Callable
from typing import ClassVar as _ClassVar
from typing import Generic as _Generic
from typing import Iterator as _Iterator
from typing import Sequence as _Sequence
from typing import TypeVar as _TypeVar
from typing import final as _final
from typing import get_args as _get_args
from typing import get_origin as _get_origin
from typing import overload as _overload

from sqlalchemy import ColumnElement as _ColumnElement
from sqlalchemy import Delete as _Delete
from sqlalchemy import Select as _Select
from sqlalchemy import Update as _Update
from sqlalchemy import and_ as _sqla_and
from sqlalchemy import bindparam as _sqla_bindparam
from sqlalchemy import delete as _sqla_delete
from sqlalchemy import false as _sqla_false
from sqlalchemy import not_ as _sqla_not
from sqlalchemy import or_ as _sqla_or
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

from ._binding import _bind_dependency
from ._contextargs import Contextual as _Contextual
from ._contextargs import MissingContextValues as _MissingContextValues
from ._contextargs import _caller_origin
from ._contextargs import contextual as _contextual

__all__ = [
    "UNSCOPED",
    "Unscoped",
    "Clause",
    "ClauseNamespace",
    "CombinedClause",
    "ContextNamespace",
    "ContextParam",
    "TransformClause",
    "WhereClause",
    "all_of",
    "any_of",
    "apply_clauses",
    "combine",
    "none_of",
    "transform_clause",
    "where_clause",
]

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


# model -> its ClauseNamespace; weak keys, so throwaway models (tests)
# do not keep dead namespaces alive
_NAMESPACES: _weakref.WeakKeyDictionary[
    type[_DeclarativeBase], type[ClauseNamespace]
] = _weakref.WeakKeyDictionary()


class ClauseNamespace:
    """Groups clauses under one named class.

    Subclass and put the clauses in the class body; earlier names are
    available to later compositions. On definition the namespace
    validates that every public attribute is a Clause (catching a bare
    SQLAlchemy expression that forgot its where_clause() wrapper). Usage
    is by class name (`ItemClauses.visible`): plain attribute access
    that any type checker follows. A clause needs no model; a namespace
    that declares `model = <mapped class>` is *the* namespace for that
    model and registers itself, so its `default_scope` reaches the
    model's reads and reference checks; the model class itself is never
    touched. Define such a namespace in the model's module, so importing
    the model guarantees the registration ran. A namespace without a
    model is a plain group: shared clauses, or a base class whose
    `__init_subclass__` shapes the namespaces that extend it.

    The name `default_scope` is reserved: a clause under that name is
    the scope every view read and every reference check on the model
    applies unless a view declares its own (see the Scopes guide). It
    must be a WhereClause: a reference check is an existence probe and
    cannot honor a transform, so ordering and joins stay on the view
    scope, and a join-dependent predicate is an EXISTS (.any()/.has()).
    A model subclass inherits the nearest declared `default_scope`
    along its MRO: a namespace that does not declare one leaves an
    inherited scope in force, and `default_scope = UNSCOPED` is the
    explicit opt-out. `None` says nothing here and is rejected.
    """

    model: _ClassVar[type[_DeclarativeBase]]
    default_scope: _ClassVar[WhereClause | Unscoped] = UNSCOPED

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        model = cls.__dict__.get("model")
        for name, value in vars(cls).items():
            if name.startswith("_") or name == "model":
                continue
            if name == "default_scope":
                if value is UNSCOPED:
                    continue  # explicit opt-out of an inherited scope
                if value is None:
                    raise TypeError(
                        f"{cls.__name__}.default_scope = None says nothing; "
                        "opt out explicitly with UNSCOPED "
                        "(fr.clauses.UNSCOPED)"
                    )
            if not isinstance(value, Clause):
                raise TypeError(
                    f"{cls.__name__}.{name} is not a Clause; wrap it with "
                    "where_clause() or transform_clause(), or prefix it with "
                    "an underscore if it is a helper"
                )
        scope = vars(cls).get("default_scope")
        if not (scope is None or scope is UNSCOPED or isinstance(scope, WhereClause)):
            # a transform cannot be honored by the reference checks this
            # scope also feeds, so accepting one here would be a lie
            raise TypeError(
                f"{cls.__name__}.default_scope must be a WhereClause; "
                "ordering and joins belong on the view scope, and a "
                "join-dependent predicate is an EXISTS (.any()/.has())"
            )
        if model is None:
            return  # a plain group of clauses, or a base: nothing to register
        existing = _NAMESPACES.get(model)
        if existing is not None:
            raise TypeError(
                f"{model.__name__} already has a clause namespace: {existing.__name__}"
            )
        _NAMESPACES[model] = cls


_NO_DECLARATION = object()


def _declared_default_scope(namespace: type[ClauseNamespace]) -> _Any:
    """The namespace's own `default_scope` declaration, or _NO_DECLARATION.

    Looks above the ClauseNamespace base, so a namespace subclass keeps
    its parent's declaration while the base's UNSCOPED class default
    does not count as one.
    """
    for klass in namespace.__mro__:
        if klass is ClauseNamespace:
            return _NO_DECLARATION
        if "default_scope" in vars(klass):
            return vars(klass)["default_scope"]
    return _NO_DECLARATION


def _default_scope(model: type[_DeclarativeBase]) -> WhereClause | None:
    """The model's `default_scope`, or None for unscoped.

    Walks the model's MRO and applies the first registered namespace
    that declares `default_scope` itself: a model subclass inherits the
    base model's scope through namespaces that stay silent, and
    UNSCOPED stops the walk as the explicit opt-out. A `default_scope`
    that is not a WhereClause raises: the namespace validation
    guarantees it at class definition, so this only fires on a later
    assignment, which must not silently weaken the model's scope.
    """
    for klass in model.__mro__:
        namespace = _NAMESPACES.get(klass)
        if namespace is None:
            continue
        scope = _declared_default_scope(namespace)
        if scope is _NO_DECLARATION:
            continue  # no declaration here: keep walking the model MRO
        if scope is UNSCOPED:
            return None
        if not isinstance(scope, WhereClause):
            raise TypeError(
                f"{namespace.__name__}.default_scope must be a WhereClause or "
                "UNSCOPED; wrap a raw expression with where_clause(), and keep "
                "ordering and joins on the view scope"
            )
        return scope
    return None


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


def _context_member_type(cls: type, name: str, annotation: _Any) -> _Any | None:
    # tolerant per-member resolve, like the parameter markers: a stringified
    # annotation (PEP 563) that names ContextParam is accepted with the type
    # unresolved; anything that is not a ContextParam annotation raises
    if isinstance(annotation, str):
        module = _sys.modules.get(cls.__module__)
        try:
            annotation = eval(annotation, getattr(module, "__dict__", {}))  # noqa: S307
        except Exception:
            if "ContextParam" in annotation:
                return None
            raise TypeError(
                f"{cls.__name__}.{name}: a ContextNamespace member is "
                f"annotated `ContextParam[T]`; cannot resolve {annotation!r}"
            ) from None
    if _get_origin(annotation) is _ClassVar:
        args = _get_args(annotation)
        annotation = args[0] if args else annotation
    if annotation is ContextParam:
        return None
    if _get_origin(annotation) is ContextParam:
        args = _get_args(annotation)
        return args[0] if args else None
    raise TypeError(
        f"{cls.__name__}.{name}: a ContextNamespace member is annotated "
        f"`ContextParam[T]`, got {annotation!r}"
    )


class ContextNamespace:
    """Declares the ContextParams of one context, one per annotation.

    Subclass and annotate: each public annotation `name: ContextParam[T]`
    materializes a ContextParam bound under `name`, so the attribute name
    is the bind name and a typo fails at import. Assigning an existing
    member adopts it (`tenant_id = Current.tenant_id`), so namespaces can
    share one slot; helpers take a leading underscore. The conventional
    app-wide subclass is named Current; a value with a smaller audience
    gets a smaller namespace beside its consumers. Declaring here says
    where a value lives, not where it is bound: binding stays at the
    narrowest level that knows the value, and an unbound read still
    raises.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        annotations = cls.__dict__.get("__annotations__") or {}
        for name, annotation in annotations.items():
            if name.startswith("_"):
                continue
            existing = vars(cls).get(name)
            if isinstance(existing, ContextParam):
                continue  # annotated and adopted; the assignment wins
            type_ = _context_member_type(cls, name, annotation)
            setattr(cls, name, _context_param(name, type_))
        for name, value in list(vars(cls).items()):
            if name.startswith("_"):
                continue
            if not isinstance(value, ContextParam):
                raise TypeError(
                    f"{cls.__name__}.{name} is not a ContextParam; declare "
                    "members by annotation (`name: ContextParam[T]`), adopt "
                    "one by assignment, or prefix a helper with an underscore"
                )

    @classmethod
    def _members(cls) -> dict[str, ContextParam[_Any]]:
        members: dict[str, ContextParam[_Any]] = {}
        for klass in reversed(cls.__mro__):
            for name, value in vars(klass).items():
                if isinstance(value, ContextParam):
                    members[name] = value
        return members

    @classmethod
    @_contextmanager
    def bind(cls, /, **values: _Any):
        """Bind member values for the duration of the with block.

        Keys are member names; a name this namespace does not declare
        raises. Equivalent to nesting each member's own bind().
        """
        members = cls._members()
        unknown = sorted(set(values) - set(members))
        if unknown:
            raise TypeError(f"{cls.__name__} has no member(s): " + ", ".join(unknown))
        with _ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(members[name].bind(**{name: value}))
            yield

    @classmethod
    def depends(cls, /, **sources: _Any) -> _Any:
        """A FastAPI dependency that binds the named members per request.

        Keyword names are member names; each value is the dependency the
        member's value comes from (a callable, a ``Depends(...)``, or an
        ``Annotated`` alias), so ``app.dependency_overrides`` keeps
        working. One generated dependency binds them all; see
        :meth:`ContextParam.depends` for the single-slot form.
        """
        members = cls._members()
        unknown = sorted(set(sources) - set(members))
        if unknown:
            raise TypeError(f"{cls.__name__} has no member(s): " + ", ".join(unknown))
        entries = [(members[name], source) for name, source in sources.items()]
        return _bind_dependency(entries, _caller_origin())

    @classmethod
    def explain(cls) -> str:
        """Each member with its bound value and origin, or UNBOUND."""
        lines = [cls.__name__]
        for name, member in sorted(cls._members().items()):
            fn = member._param_fn
            bound = fn.bindings() if fn is not None else {}
            if name in bound:
                value, origin = bound[name]
                try:
                    shown = repr(value)
                except Exception:
                    shown = f"<unrepresentable {type(value).__name__}>"
                if len(shown) > 60:
                    shown = shown[:57] + "..."
                suffix = f"   bound at {origin}" if origin else ""
                lines.append(f"├─ {name} = {shown}{suffix}")
            else:
                lines.append(f"├─ {name}: UNBOUND")
        if len(lines) > 1:
            lines[-1] = "└─" + lines[-1][2:]
        return "\n".join(lines)


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


def _validate_clause_fn(fn: _Callable) -> None:
    label = getattr(fn, "__name__", "clause function")
    if _inspect.iscoroutinefunction(fn) or _inspect.iscoroutinefunction(
        getattr(type(fn), "__call__", None)
    ):
        raise TypeError(
            f"{label} is async; clause functions build expressions and must be sync"
        )
    for param in _inspect.signature(fn).parameters.values():
        if param.kind in (
            _inspect.Parameter.VAR_POSITIONAL,
            _inspect.Parameter.VAR_KEYWORD,
        ):
            raise TypeError(
                f"{label} takes *{param.name}; clause function parameters are "
                "bound by name, so variadic parameters cannot be"
            )
        if param.default is not _inspect.Parameter.empty:
            raise TypeError(
                f"{label} gives {param.name!r} a default; bound parameters must "
                "not have one; a default would silently stand in when the "
                "binding is missing"
            )


def _marker_slots(fn: _Callable) -> tuple[_Callable, tuple[ContextParam, ...]]:
    # parameters annotated with a ContextParam (Annotated[T, slot]) are fed
    # from the slot instead of the function's own binding namespace. Each
    # annotation resolves individually and tolerantly: an unresolvable one
    # (TYPE_CHECKING-only names under PEP 563) simply carries no marker,
    # instead of get_type_hints() failing the whole function
    raw = getattr(fn, "__annotations__", {})
    globalns = getattr(fn, "__globals__", {})
    localns: dict[str, object] = {}
    closure = getattr(fn, "__closure__", None)
    if closure:
        for cell_name, cell in zip(fn.__code__.co_freevars, closure):
            try:
                localns[cell_name] = cell.cell_contents
            except ValueError:  # empty cell
                pass
    markers: dict[str, ContextParam] = {}
    for pname in _inspect.signature(fn).parameters:
        annotation = raw.get(pname)
        if isinstance(annotation, str):
            source = annotation
            try:
                annotation = eval(annotation, globalns, localns)  # noqa: S307
            except Exception:
                if "Annotated" in source:
                    label = getattr(fn, "__qualname__", fn)
                    raise TypeError(
                        f"cannot resolve the annotation {source!r} on parameter "
                        f"{pname!r} of {label}; an Annotated marker must be "
                        "resolvable at declaration time. Define the ContextParam "
                        "at module scope, or avoid string annotations for this "
                        "function"
                    ) from None
                continue
        for meta in getattr(annotation, "__metadata__", ()):
            if isinstance(meta, ContextParam):
                markers[pname] = meta
    if not markers:
        return fn, ()

    @_functools.wraps(fn)
    def filled(*args, **kwargs):
        for pname, slot in markers.items():
            if pname not in kwargs:
                kwargs[pname] = slot()
        return fn(*args, **kwargs)

    # the marked parameters leave the visible signature: they are bound
    # under the slot's name, not the local one
    sig = _inspect.signature(fn)
    filled.__signature__ = sig.replace(  # type: ignore[attr-defined]
        parameters=[p for p in sig.parameters.values() if p.name not in markers]
    )
    return filled, tuple(dict.fromkeys(markers.values()))


def where_clause(
    condition: _ColumnElement[bool] | _Callable[..., _ColumnElement[bool]],
) -> WhereClause:
    """A WhereClause from a condition or a function returning one.

    A ready-made ColumnElement is reused as-is; a ContextParam embedded
    in it is detected and attached, so binding reaches the slot through
    this clause. A function's parameters are filled from values bound
    via Clause.bind() each time the clause resolves; a parameter marked
    Annotated[T, slot] is fed from that slot instead.

    In a ClauseNamespace body, stack this decorator over ``staticmethod``:
    the marker keeps a type checker from reading the def as a method, and
    is unwrapped here.
    """
    if isinstance(condition, staticmethod):
        condition = condition.__func__
    clause = WhereClause()
    if callable(condition):
        _validate_clause_fn(condition)
        fn, slots = _marker_slots(condition)
        clause._where_fn = _contextual(fn)
        clause._children = slots
    else:
        clause._where_fn = _contextual(lambda: condition)
        clause._children = _embedded_slots(condition)
        clause._condition = condition
    return clause


def transform_clause(fn: _Callable[..., _Select[_Any]]) -> TransformClause:
    """A TransformClause from a function that reshapes a Select.

    The first parameter receives the statement; any further parameters
    are filled from values bound via Clause.bind(). A parameter marked
    Annotated[T, slot] is fed from that ContextParam instead. Stacks over
    ``staticmethod`` in a namespace body, like where_clause().
    """
    if isinstance(fn, staticmethod):
        fn = fn.__func__
    _validate_clause_fn(fn)
    wrapped, slots = _marker_slots(fn)
    clause = TransformClause()
    clause._transform_fn = _contextual(wrapped)
    if clause._transform_fn.accepted is not None:
        own_params = list(_inspect.signature(wrapped).parameters)
        clause._transform_routable = clause._transform_fn.accepted - set(own_params[:1])
    clause._children = slots
    return clause


def _context_param(name: str, type_: _Any | None = None) -> ContextParam[_Any]:
    """A ContextParam holding one value, bound under `name`.

    The construction primitive behind ContextNamespace, deliberately not
    public: a slot is pure name and identity, so it is declared where it
    has an address. The optional type lands in the slot's synthesized
    signature, where introspection can read it.
    """

    def fn(**values):
        return values[name]

    fn.__name__ = fn.__qualname__ = f"ContextParam({name})"
    annotation = type_ if type_ is not None else _inspect.Parameter.empty
    setattr(
        fn,
        "__signature__",
        _inspect.Signature(
            [
                _inspect.Parameter(
                    name, _inspect.Parameter.KEYWORD_ONLY, annotation=annotation
                )
            ]
        ),
    )
    clause = ContextParam._blank()
    clause._param_fn = _contextual(fn)
    placeholder = _sqla_bindparam(name)
    setattr(placeholder, "_fr_param", clause)  # tag for _embedded_slots
    clause._placeholder = placeholder
    return clause


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


def _require_wheres(
    name: str, clauses: tuple[Clause | Unscoped, ...]
) -> tuple[Clause, ...]:
    if not clauses:
        raise TypeError(f"{name}() requires at least one clause")
    given = _without_unscoped(name, clauses)
    if any(c._where_fn is None for c in given):
        raise TypeError(f"{name} combines only clauses with a where")
    return given


def _require_no_transforms(name: str, clauses: tuple[Clause, ...], why: str) -> None:
    if any(_has_transform(c) for c in clauses):
        raise TypeError(f"{name} cannot include transform clauses; {why}")


@_overload
def all_of(*clauses: WhereClause) -> WhereClause: ...
@_overload
def all_of(*clauses: Clause) -> Clause: ...
@_overload
def all_of(first: WhereClause, /, *clauses: WhereClause | Unscoped) -> WhereClause: ...
@_overload
def all_of(
    first: WhereClause | Unscoped,
    second: WhereClause,
    /,
    *clauses: WhereClause | Unscoped,
) -> WhereClause: ...
@_overload
def all_of(*clauses: Unscoped) -> Unscoped: ...
@_overload
def all_of(*clauses: WhereClause | Unscoped) -> WhereClause | Unscoped: ...
@_overload
def all_of(*clauses: Clause | Unscoped) -> Clause | Unscoped: ...
def all_of(*clauses: Clause | Unscoped) -> Clause | Unscoped:
    """AND the operands' predicates.

    UNSCOPED is a no-op. Every other operand must carry a where, and its
    transforms travel along. One remaining operand is returned unchanged,
    and none returns UNSCOPED. Otherwise returns a WhereClause when every
    remaining operand is one. Calling with no arguments raises.
    """
    given = _require_wheres("all_of", clauses)
    if not given:
        return UNSCOPED
    if len(given) == 1:
        return given[0]

    def fn() -> _ColumnElement[bool]:
        return _sqla_and(*(_resolve_carried_where(c) for c in given))

    fn.__name__ = fn.__qualname__ = "all_of"
    if any(_has_transform(c) for c in given):
        result: Clause = CombinedClause()
        result._where_fn = _contextual(fn)
    else:
        result = where_clause(fn)
    result._children = given
    return result


@_overload
def any_of(*clauses: WhereClause) -> WhereClause: ...
@_overload
def any_of(*clauses: Unscoped) -> Unscoped: ...
@_overload
def any_of(*clauses: WhereClause | Unscoped) -> WhereClause | Unscoped: ...
def any_of(*clauses: WhereClause | Unscoped) -> WhereClause | Unscoped:
    """OR the operands' predicates.

    UNSCOPED accepts every row, so any UNSCOPED operand returns the sentinel.
    Other operands are validated before this simplification and are not
    resolved if the result is UNSCOPED. Calling with no arguments raises.
    Other operands must be WhereClauses: OR over a predicate that depends on
    an inner join changes which rows exist at all. Express such a
    condition as EXISTS, via relationship .any()/.has(), in a where.
    """
    given = _require_wheres("any_of", clauses)
    _require_no_transforms(
        "any_of",
        given,
        "an inner join already removes rows, breaking OR semantics; "
        "express the joined condition as EXISTS via .any()/.has() in a where",
    )
    if len(given) != len(clauses):
        return UNSCOPED
    fn = lambda: _sqla_or(*(_resolve_carried_where(c) for c in given))  # noqa: E731
    fn.__name__ = fn.__qualname__ = "any_of"
    result = where_clause(fn)
    result._children = given
    return result


def none_of(*clauses: WhereClause | Unscoped) -> WhereClause:
    """True when none of the operands' predicates hold.

    NOT over the OR of all operands. An UNSCOPED operand returns a
    WhereClause over SQL false(), matching no rows. Other operands are
    validated but not resolved in that case. Operand rules as for any_of.
    """
    # none_of(a, b) means: not a and not b, NOT over the OR of all operands
    given = _require_wheres("none_of", clauses)
    _require_no_transforms(
        "none_of",
        given,
        "an inner join already removes rows, breaking NOT semantics; "
        "express the joined condition as EXISTS via .any()/.has() in a where",
    )
    if len(given) != len(clauses):
        return where_clause(_sqla_false())
    fn = lambda: _sqla_not(_sqla_or(*(_resolve_carried_where(c) for c in given)))  # noqa: E731
    fn.__name__ = fn.__qualname__ = "none_of"
    result = where_clause(fn)
    result._children = given
    return result


def combine(*clauses: Clause | Unscoped) -> CombinedClause:
    """Bundle the wheres and transforms of several clauses under one name.

    Not boolean logic: it gives a transform+where combination its own
    name instead of mutating an existing Clause (which may already be
    reused elsewhere). Wheres are AND-ed; transforms stay on the
    children, where apply_clauses finds them. At least one operand must
    carry a transform. UNSCOPED is ignored and does not supply a transform.
    A bundle of only wheres is all_of's job.
    """
    given = _without_unscoped("combine", clauses)
    for clause in given:
        if isinstance(clause, ContextParam):
            raise TypeError(
                "combine cannot bundle a ContextParam; embed the slot in an "
                "expression instead"
            )
    if not any(_has_transform(c) for c in given):
        raise TypeError(
            "combine requires at least one transform-carrying clause; "
            "a bundle of only wheres is all_of's job"
        )
    result = CombinedClause()
    wheres = [c for c in given if c._where_fn is not None]
    if wheres:
        fn = lambda: _sqla_and(*(_resolve_carried_where(c) for c in wheres))  # noqa: E731
        fn.__name__ = fn.__qualname__ = "combine"
        result._where_fn = _contextual(fn)
    result._children = given
    return result


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
    given = tuple(clause for clause in clauses if isinstance(clause, Clause))
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
